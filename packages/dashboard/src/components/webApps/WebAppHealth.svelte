<script lang="ts">
  import { onMount } from 'svelte'
  import { webRequest, webError, webDate } from '$lib/webApps'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'

  export let appId: string
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  type Config = {
    enabled: boolean
    path: string
    intervalSeconds: number
    timeoutSeconds: number
    failureThreshold: number
    revision: string
  }
  type State = {
    status: string
    checkedAt: string
    statusSince: string
    httpStatus: number
    durationMs: number
    consecutiveFailures: number
    lastResult: string
  }
  type Health = {
    config: Config
    state: State
    retention: { days: number; maxEvents: number }
    automaticRecovery: false
  }
  type Event = {
    id: string
    deploymentId: string
    fromStatus: string
    status: string
    reason: string
    checkedAt: string
    httpStatus: number
    durationMs: number
    consecutiveFailures: number
  }
  type History = {
    items: Event[]
    page: number
    perPage: number
    totalItems: number
    totalPages: number
    hasMore: boolean
  }
  const labels: Record<string, string> = {
    not_applicable: 'Profil sans route HTTP',
    healthy: 'Le site répond',
    degraded: 'Site dégradé',
    unknown: 'Premier contrôle attendu',
    stale: 'Contrôles en retard',
    disabled: 'Surveillance désactivée',
    paused: 'Site arrêté',
  }
  const reasons: Record<string, string> = {
    configuration_changed: 'Configuration mise à jour',
    disabled: 'Surveillance désactivée',
    healthy: 'Premier contrôle réussi',
    recovered: 'Le site répond à nouveau',
    failure_threshold: 'Seuil d’échecs atteint',
    public_dns: 'Injoignable depuis Internet : DNS',
    public_address_refused: 'Injoignable depuis Internet : adresse privée',
    public_timeout: 'Injoignable depuis Internet : délai dépassé',
    public_connection: 'Injoignable depuis Internet : connexion (proxy)',
    public_tls: 'Injoignable depuis Internet : certificat TLS',
    public_http_error: 'Injoignable depuis Internet : réponse en erreur',
  }
  const results: Record<string, string> = {
    timeout: 'Délai dépassé',
    connection_error: 'Connexion impossible',
    container_unavailable: 'Conteneur indisponible',
    target_mismatch: 'Publication à vérifier',
    http_error: 'Réponse en erreur',
    // Sonde publique (domaine réel en HTTPS:443) : le conteneur répond mais pas le chemin vu des visiteurs.
    public_dns: 'Nom de domaine introuvable depuis Internet',
    public_address_refused: 'Le domaine pointe vers une adresse privée',
    public_timeout: 'Domaine public trop lent',
    public_connection: 'Domaine public injoignable (proxy)',
    public_tls: 'Certificat TLS du domaine invalide',
    public_http_error: 'Le domaine public répond en erreur',
  }
  let health: Health | null = null
  let history: History | null = null
  let form: Config = {
    enabled: false,
    path: '/',
    intervalSeconds: 60,
    timeoutSeconds: 5,
    failureThreshold: 3,
    revision: '',
  }
  let loading = false
  let saving = false
  let disposed = false
  let error = ''
  let notice = ''
  let page = 1
  const refresh = async (draft = false) => {
    if (loading || saving || disposed) return
    loading = true
    error = ''
    try {
      const [value, events] = await Promise.all([
        webRequest<Health>(`/${appId}/health`),
        webRequest<History>(`/${appId}/health/history?page=${page}&perPage=20`),
      ])
      if (disposed) return
      health = value
      history = events
      if (draft) form = { ...value.config }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      loading = false
    }
  }
  const save = async (event: SubmitEvent) => {
    event.preventDefault()
    if (saving || loading) return
    saving = true
    error = ''
    notice = ''
    try {
      const value = await webRequest<Health>(`/${appId}/health`, { method: 'PATCH', body: form })
      if (disposed) return
      health = value
      form = { ...value.config }
      notice = value.config.enabled
        ? 'Surveillance enregistrée. Le prochain contrôle démarrera automatiquement.'
        : 'La surveillance est désactivée.'
      page = 1
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      saving = false
    }
    if (!disposed) await refresh()
  }
  const paginate = async (direction: number) => {
    if (loading || saving) return
    page = Math.max(1, page + direction)
    await refresh()
  }
  onMount(() => {
    void refresh(true)
    const timer = setInterval(() => {
      if (!document.hidden) void refresh()
    }, 10000)
    return () => {
      disposed = true
      clearInterval(timer)
    }
  })
</script>

<section class="web-panel site-health">
  <div class="web-row">
    <div class="health-heading">
      <span class="web-eyebrow">Surveillance continue</span>
      <h2>Un site qui reste sous contrôle</h2>
    </div>
    <button class="web-button" type="button" disabled={loading || saving} onclick={() => refresh()}
      >{loading ? 'Actualisation…' : 'Actualiser l’état'}</button
    >
  </div>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if notice}<div class="web-alert" role="status">{notice}</div>{/if}
  {#if health}
    <div class="health-summary" data-state={health.state.status}>
      <div class="health-summary-title">
        <span class="health-dot" aria-hidden="true"></span>
        <div>
          <strong>{labels[health.state.status] || health.state.status}</strong>
          <p>
            {health.state.status === 'not_applicable'
              ? 'Un worker ne publie pas de route HTTP. Son état et ses ressources restent visibles dans la vue d’ensemble.'
              : health.state.status === 'stale'
                ? 'Le worker ne transmet plus de contrôles récents. Vérifiez son état.'
                : health.state.status === 'degraded'
                  ? 'Les échecs consécutifs ont atteint votre seuil. Le site reste en ligne pour permettre le diagnostic.'
                  : health.state.status === 'unknown'
                    ? 'La première sonde ou le contrôle de la nouvelle version est attendu.'
                    : health.state.status === 'paused'
                      ? 'Les contrôles reprendront lorsque le site sera démarré.'
                      : health.state.status === 'disabled'
                        ? 'Activez les sondes pour recevoir les résultats, même pendant les constructions.'
                        : `Dernier contrôle : ${webDate(health.state.checkedAt)}`}
          </p>
        </div>
      </div>
      <dl class="health-metrics">
        <div>
          <dt>Réponse</dt>
          <dd>
            {health.state.httpStatus ? `HTTP ${health.state.httpStatus}` : results[health.state.lastResult] || '—'}
          </dd>
        </div>
        <div>
          <dt>Durée</dt>
          <dd>{health.state.checkedAt ? `${health.state.durationMs.toLocaleString('fr-FR')} ms` : '—'}</dd>
        </div>
        <div>
          <dt>Échecs consécutifs</dt>
          <dd>{health.state.consecutiveFailures} <span>/ {health.config.failureThreshold}</span></dd>
        </div>
        <div>
          <dt>Fréquence</dt>
          <dd>{health.config.intervalSeconds} <span>secondes</span></dd>
        </div>
      </dl>
    </div>
  {:else if loading}<p class="web-help" role="status">Chargement de la surveillance…</p>{/if}
  {#if capabilities['health.manage']}<form onsubmit={save} class="health-config">
      <div class="web-row">
        <h3>Configurer les sondes</h3>
        <label class="health-toggle"
          ><input type="checkbox" bind:checked={form.enabled} disabled={!health || saving} /> Activer la surveillance</label
        >
      </div>
      <div class="health-fields">
        <label class="health-path"
          >Chemin à contrôler<input
            bind:value={form.path}
            maxlength="512"
            required
            placeholder="/api/health"
            autocomplete="off"
            spellcheck="false"
            disabled={!health || saving}
          /><small>Chemin local uniquement, sans paramètres ni identifiants.</small></label
        >
        <label
          >Intervalle (secondes)<input
            type="number"
            bind:value={form.intervalSeconds}
            min="60"
            max="3600"
            step="1"
            required
            disabled={!health || saving}
          /></label
        >
        <label
          >Délai maximal (secondes)<input
            type="number"
            bind:value={form.timeoutSeconds}
            min="1"
            max="10"
            step="1"
            required
            disabled={!health || saving}
          /></label
        >
        <label
          >Échecs avant dégradation<input
            type="number"
            bind:value={form.failureThreshold}
            min="1"
            max="10"
            step="1"
            required
            disabled={!health || saving}
          /></label
        >
      </div>
      <p class="web-help">
        Les réponses HTTP 200 à 399 indiquent que le site répond. Les redirections ne sont pas suivies. Aucun arrêt ni
        redémarrage automatique n’est déclenché.
      </p>
      <p class="web-help">
        Ces sondes surveillent la version publiée. Leur configuration est indépendante du contrôle effectué avant une
        publication.
      </p>
      <p class="web-help">
        Quand le conteneur répond, le domaine public est aussi contrôlé en HTTPS (DNS, certificat, proxy). Après un
        incident, l’alerte « Site rétabli » est envoyée même si le retour à la normale vient d’un redéploiement ou d’un
        nouveau chemin de contrôle ; désactiver la surveillance l’annule.
      </p>
      <div class="web-row">
        <span class="web-help">Dernier résultat reçu : {webDate(health?.state.checkedAt)}</span><button
          class="web-button web-button--primary"
          type="submit"
          disabled={!health || loading || saving}>{saving ? 'Enregistrement…' : 'Enregistrer la surveillance'}</button
        >
      </div>
    </form>{/if}
</section>

<section class="web-panel health-history">
  <div class="web-row">
    <div class="health-heading">
      <span class="web-eyebrow">Historique des transitions</span>
      <h2>Comprendre ce qui a changé</h2>
    </div>
    <span class="web-help">30 jours · 1 000 événements maximum</span>
  </div>
  {#if history?.items.length}
    <div class="health-table-wrap">
      <table>
        <thead><tr><th>Date</th><th>État</th><th>Événement</th><th>Réponse</th><th>Durée</th></tr></thead><tbody
          >{#each history.items as item (item.id)}<tr
              ><td>{webDate(item.checkedAt)}</td><td
                ><span class="health-state" data-state={item.status}>{labels[item.status] || item.status}</span></td
              ><td>{reasons[item.reason] || item.reason}</td><td>{item.httpStatus ? `HTTP ${item.httpStatus}` : '—'}</td
              ><td>{item.httpStatus ? `${item.durationMs.toLocaleString('fr-FR')} ms` : '—'}</td></tr
            >{/each}</tbody
        >
      </table>
    </div>
  {:else}<p class="web-help">
      Les activations, changements d’état et rétablissements apparaîtront ici. Les réponses du site ne sont pas
      conservées.
    </p>{/if}
  <nav class="web-row" aria-label="Pages de l’historique de santé">
    <span class="web-help"
      >{history?.totalItems || 0} événements · Page {page} / {Math.max(1, history?.totalPages || 1)}</span
    >
    <div class="web-actions">
      <button class="web-button" type="button" onclick={() => paginate(-1)} disabled={page <= 1 || loading || saving}
        >Précédent</button
      ><button
        class="web-button"
        type="button"
        onclick={() => paginate(1)}
        disabled={!history?.hasMore || loading || saving}>Suivant</button
      >
    </div>
  </nav>
</section>

<style>
  .health-heading {
    display: grid;
    gap: 0.4rem;
  }
  .health-summary {
    border: 1px solid var(--web-border);
    border-left: 3px solid var(--app-text-faint, #71717a);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
    background: var(--web-surface-soft);
  }
  .health-summary[data-state='healthy'] {
    border-left-color: #528746;
    background: #6f9f5407;
  }
  .health-summary[data-state='degraded'] {
    border-left-color: #b95548;
    background: #b9554807;
  }
  .health-summary[data-state='stale'] {
    border-left-color: #b88b3c;
  }
  .health-summary-title {
    display: flex;
    align-items: start;
    gap: 0.7rem;
  }
  .health-summary-title p {
    margin-top: 0.4rem;
    font-size: 0.8rem;
    opacity: 0.7;
    max-width: 52rem;
  }
  .health-dot {
    flex: 0 0 0.5rem;
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--app-text-faint, #71717a);
    margin-top: 0.4rem;
  }
  [data-state='healthy'] .health-dot {
    background: #528746;
  }
  [data-state='degraded'] .health-dot {
    background: #b95548;
  }
  [data-state='stale'] .health-dot {
    background: #b88b3c;
  }
  .health-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1rem;
    margin: 1.2rem 0 0;
    padding-top: 1rem;
    border-top: 1px solid var(--web-border);
  }
  dt {
    font-size: 0.72rem;
    opacity: 0.65;
    margin-bottom: 0.4rem;
  }
  dd {
    font-size: 1.05rem;
    margin: 0;
    overflow-wrap: anywhere;
    font-weight: 550;
  }
  dd span {
    font-size: 0.75rem;
    font-weight: 400;
    opacity: 0.6;
  }
  .health-config {
    display: grid;
    gap: 1.1rem;
    padding-top: 0.3rem;
  }
  .health-toggle {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.82rem;
    cursor: pointer;
  }
  .health-toggle input {
    accent-color: var(--web-green);
    width: 1rem;
    height: 1rem;
  }
  .health-fields {
    display: grid;
    grid-template-columns: minmax(12rem, 1.6fr) repeat(3, minmax(0, 1fr));
    gap: 1rem;
  }
  .health-fields label {
    display: grid;
    align-content: start;
    gap: 0.5rem;
    font-size: 0.76rem;
  }
  .health-fields input {
    width: 100%;
    min-width: 0;
    padding: 0.7rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    background: var(--web-surface);
    color: inherit;
    font: inherit;
    font-size: 0.8125rem;
  }
  small {
    opacity: 0.6;
    font-size: 0.71rem;
    line-height: 1.5;
  }
  .health-table-wrap {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
    text-align: left;
  }
  th {
    font-size: 0.7rem;
    font-weight: 500;
    opacity: 0.65;
  }
  th,
  td {
    padding: 0.85rem 0.7rem;
    border-bottom: 1px solid var(--web-border);
    vertical-align: top;
  }
  th:first-child,
  td:first-child {
    padding-left: 0;
  }
  td:first-child {
    white-space: nowrap;
  }
  .health-state {
    display: inline-block;
    border-radius: var(--web-radius, 6px);
    padding: 0.25rem 0.4rem;
    background: var(--web-surface-soft);
    font-size: 0.73rem;
  }
  .health-state[data-state='healthy'] {
    color: #528746;
    background: #52874612;
  }
  .health-state[data-state='degraded'] {
    color: #b95548;
    background: #b9554812;
  }
  @media (max-width: 850px) {
    .health-fields {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .health-path {
      grid-column: 1 / -1;
    }
  }
  @media (max-width: 550px) {
    .health-metrics {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .health-fields {
      grid-template-columns: 1fr;
    }
    .health-summary {
      padding: 1rem;
    }
    table {
      min-width: 620px;
    }
  }
</style>
