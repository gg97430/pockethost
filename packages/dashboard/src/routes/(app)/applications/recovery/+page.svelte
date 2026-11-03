<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { webDate, webError } from '$lib/webApps'
  import WebAppNotifications from '$components/webApps/WebAppNotifications.svelte'
  import RecoveryNavigation from '$components/webApps/RecoveryNavigation.svelte'

  type Coverage = {
    complete?: boolean
    ephemeralPreviewsExcludedCount?: number
    instancesMissingCount?: number
    externalStorageCount?: number
    unprotectedWebImagesCount?: number
    unprotectedVolumesCount?: number
    instancesMissing?: string[]
    externalStorage?: string[]
    unprotectedWebImages?: string[]
    unprotectedVolumes?: string[]
  }
  type Job = {
    id: string
    backupId?: string
    operation: string
    status: string
    stage?: string
    created?: string
    startedAt?: string
    finishedAt?: string
    error?: string
    bytes?: number
    encryptedBytes?: number
    progress?: { completed: number; total: number; unit: 'bytes' | 'items' }
    recoverable?: boolean
    retained?: boolean
    retentionPending?: boolean
    verified?: boolean
    coverage?: Coverage
    trigger?: 'manual' | 'scheduled' | 'cli'
    held?: boolean
    webBackups?: { created: number; reused: number; staleReused: number }
    result?: {
      verified?: boolean
      coverage?: Coverage
      databases?: { id: string; databaseBytes: number }[]
      databasesRestored?: string[]
      noServicesStarted?: boolean
      paused?: boolean
      verification?: { passed: boolean; databases: number; files: number; mfaSidecars: number }
      cleanedUp?: boolean
    }
  }
  type ScheduleStatus = 'disabled' | 'active' | 'executionDisabled' | 'serviceOffline' | 'notConfigured'
  type Settings = {
    enabled: boolean
    hourUTC: number
    minuteUTC: number
    retention: number
    nextRunAt?: string
    plannedRunAt?: string
    status?: ScheduleStatus
    statusMessage?: string
  }
  // The service reports counts; older builds may send the lists themselves.
  type PlanCheck = {
    checked?: boolean
    drift?: boolean
    added?: number | string[]
    removed?: number | string[]
    checkedAt?: string
  }
  type State = {
    configured: boolean
    unavailable?: boolean
    platform?: string
    worker?: { online: boolean; lastSeenAt?: string }
    settings: Settings
    executionEnabled?: boolean
    planCheck?: PlanCheck
    active?: Job | null
    latest?: Job | null
    drillSelection?: string[]
    capabilities?: { canCapture?: boolean; canDrill?: boolean }
  }
  const send = <T,>(path: string, options: Record<string, unknown> = {}) =>
    client().client.send<T>(`/api/web-platform-recovery${path}`, { ...options, requestKey: null })
  let state: State | null = null
  let items: Job[] = []
  let page = 1,
    totalPages = 1,
    totalItems = 0
  let enabled = false,
    hour = '02:30',
    retention = 3
  let runToday = false,
    clock = Date.now()
  let dirty = false,
    loading = true,
    refreshing = false,
    busy = ''
  let error = '',
    message = '',
    disposed = false
  let selected: Job | null = null
  const terminal = (job: Job) => ['ready', 'complete', 'restored-paused'].includes(job.status)
  const coverage = (job?: Job | null) => job?.coverage || job?.result?.coverage
  const verified = (job: Job) => job.verified === true || job.result?.verified === true
  const label = (status: string) =>
    ({
      queued: 'En attente',
      running: 'En cours',
      ready: 'Terminée',
      complete: 'Terminée',
      failed: 'Échec',
      'restored-paused': 'Test isolé terminé',
    })[status] || status
  const bytes = (value = 0) =>
    value >= 1024 ** 3
      ? `${(value / 1024 ** 3).toFixed(1)} Gio`
      : value >= 1024 ** 2
        ? `${(value / 1024 ** 2).toFixed(1)} Mio`
        : `${Math.round(value / 1024)} Kio`
  const count = (value?: number | string[]) => (Array.isArray(value) ? value.length : value || 0)
  const triggers = { manual: 'Manuelle', scheduled: 'Planifiée', cli: 'Exercice CLI' }
  // Older services send no schedule status: keep their previous « next capture » line.
  const scheduleStatus = (value?: Settings) => value?.status ?? 'active'
  // The daily time (UTC) is already past today: arming now waits for tomorrow unless runToday.
  // `at` is the polled clock, so the option appears once the time passes while the page stays open.
  const afterTime = (value: string, at = Date.now()) => {
    const [hh = 0, mm = 0] = value.split(':').map(Number),
      now = new Date(at)
    return now.getUTCHours() * 60 + now.getUTCMinutes() >= hh * 60 + mm
  }
  // Counts only: the service never exposes which customer sites were concerned.
  const siteArchives = (value: { created: number; reused: number; staleReused: number }) =>
    `Archives de sites : ${value.created} créée(s)` +
    (value.reused
      ? `, ${value.reused} réutilisée(s) pour préserver les sauvegardes des clients` +
        (value.staleReused ? ` (dont ${value.staleReused} antérieure(s) au déploiement actif)` : '')
      : '') +
    '.'
  const issues = (value?: Coverage) =>
    (value?.instancesMissingCount ?? value?.instancesMissing?.length ?? 0) +
    (value?.externalStorageCount ?? value?.externalStorage?.length ?? 0) +
    (value?.unprotectedWebImagesCount ?? value?.unprotectedWebImages?.length ?? 0) +
    (value?.unprotectedVolumesCount ?? value?.unprotectedVolumes?.length ?? 0)
  const load = async () => {
    if (refreshing || disposed) return
    refreshing = true
    try {
      const [status, history] = await Promise.all([
        send<State>(''),
        send<{ items: Job[]; page: number; totalItems: number; totalPages: number }>(`/runs?page=${page}`),
      ])
      if (disposed) return
      state = status
      items = history.items
      totalItems = history.totalItems
      totalPages = Math.max(1, history.totalPages)
      if (!dirty) {
        enabled = status.settings.enabled
        hour = `${String(status.settings.hourUTC ?? 2).padStart(2, '0')}:${String(status.settings.minuteUTC ?? 30).padStart(2, '0')}`
        retention = status.settings.retention || 3
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      refreshing = false
      loading = false
    }
  }
  const save = async (event: SubmitEvent) => {
    event.preventDefault()
    busy = 'settings'
    error = ''
    message = ''
    try {
      const [hourUTC, minuteUTC] = hour.split(':').map(Number),
        today = enabled && runToday && afterTime(hour)
      const response = await send<{ immediate?: boolean }>('/settings', {
        method: 'PUT',
        body: { enabled, hourUTC, minuteUTC, retention: Number(retention), ...(today ? { runToday: true } : {}) },
      })
      dirty = false
      runToday = false
      message = response?.immediate
        ? 'Planification enregistrée. Une capture démarre aujourd’hui.'
        : 'Planification enregistrée.'
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const capture = async () => {
    busy = 'capture'
    error = ''
    message = ''
    try {
      await send('/captures', { method: 'POST', body: {} })
      page = 1
      message = 'Capture mise en attente. Sa progression apparaît ci-dessous.'
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const drill = async () => {
    if (!selected) return
    busy = 'drill'
    error = ''
    message = ''
    try {
      await send(`/captures/${encodeURIComponent(selected.id)}/drill`, { method: 'POST', body: {} })
      selected = null
      page = 1
      message = 'Test de reprise isolée mis en attente.'
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const changePage = async (next: number) => {
    page = next
    await load()
  }
  onMount(() => {
    void load()
    const timer = setInterval(() => {
      clock = Date.now()
      if (!document.hidden && !busy) void load()
    }, 10000)
    const visible = () => {
      if (!document.hidden) void load()
    }
    document.addEventListener('visibilitychange', visible)
    return () => {
      disposed = true
      clearInterval(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  })
</script>

<svelte:head><title>Reprise de la plateforme - Kolab Hosting</title></svelte:head>
<div class="recovery-shell">
  <div class="web-header">
    <div class="web-title">
      <a class="web-back" href="/applications/settings">← Paramètres des applications</a>
      <p class="web-eyebrow">ADMINISTRATION · PLATEFORME</p>
      <h1>Reprise après incident</h1>
      <p class="web-subtitle">
        Suivre les captures chiffrées et vérifier la récupération des données sur un espace isolé.
      </p>
    </div>
    <button
      class="web-button web-button--primary"
      type="button"
      onclick={capture}
      disabled={!!busy ||
        !state?.configured ||
        !state?.worker?.online ||
        !!state?.active ||
        state?.capabilities?.canCapture === false}
      >{busy === 'capture' ? 'Mise en attente…' : 'Capturer maintenant'}</button
    >
  </div>
  <RecoveryNavigation active="server" />
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}
  {#if loading}<div class="web-panel"><p>Chargement de la protection…</p></div>
  {:else}
    {#if !state?.configured}<div class="web-alert" role="note">
        <strong>Service de reprise à configurer.</strong> Le plan de capture et les clés sont installés dans la configuration
        privée du serveur. Ils ne sont jamais affichés dans le dashboard.
      </div>{/if}
    <div class="recovery-stats">
      <div class="web-panel">
        <span class="web-eyebrow">SERVICE DÉDIÉ</span><strong class:recovery-ok={state?.worker?.online}
          >{state?.worker?.online ? 'Disponible' : 'Hors ligne'}</strong
        ><small>Dernier signal : {webDate(state?.worker?.lastSeenAt)}</small>
      </div>
      <div class="web-panel">
        <span class="web-eyebrow">DERNIÈRE CAPTURE</span><strong
          >{state?.latest ? webDate(state.latest.finishedAt) : 'Aucune capture'}</strong
        ><small
          >{state?.latest?.retentionPending
            ? 'Purge incomplète · test de reprise indisponible'
            : state?.latest?.retained === false
              ? 'Copie distante purgée par la rétention'
              : state?.latest && verified(state.latest)
                ? 'Archive authentifiée et vérifiée'
                : 'Vérification à effectuer'}</small
        >
      </div>
      <div class="web-panel">
        <span class="web-eyebrow">COUVERTURE</span><strong
          >{coverage(state?.latest)?.complete === true
            ? 'Complète'
            : state?.latest
              ? 'Partielle'
              : 'À vérifier'}</strong
        ><small
          >{issues(coverage(state?.latest))
            ? `${issues(coverage(state?.latest))} dépendance(s) à compléter`
            : 'Contrôleur, bases et dépendances du plan'}</small
        >
      </div>
    </div>
    {#if coverage(state?.latest)?.ephemeralPreviewsExcludedCount}<p class="web-help">
        {coverage(state?.latest)?.ephemeralPreviewsExcludedCount} preview(s) temporaire(s) exclue(s) de la capture. Elles
        ne seront pas redémarrées lors d’une reprise.
      </p>{/if}
    <section class="web-panel">
      <div class="web-row">
        <div>
          <h2>Capture nocturne</h2>
          <p class="web-help">
            Les bases sont lues dans une transaction SQLite. Les fichiers sont contrôlés séparément ; leur capture n’est
            pas une transaction commune avec toutes les bases. Chaque capture arrête brièvement les sites qui ont des
            volumes.
          </p>
        </div>
      </div>
      {#if state?.planCheck?.drift}<p class="web-help recovery-warning" role="alert">
          Le plan privé ne correspond plus aux instances ({count(state.planCheck.added)} ajoutée(s), {count(
            state.planCheck.removed
          )} retirée(s)) : la prochaine capture échouera. Régénérez le plan (runbook de reprise §1.4).{#if state.planCheck.checkedAt}
            Contrôle du {webDate(state.planCheck.checkedAt)}.{/if}
        </p>{/if}
      {#if state?.configured && state.executionEnabled === false}<p class="web-help recovery-warning" role="note">
          L’exécution est verrouillée sur le serveur (executionEnabled=false) : aucune capture, planifiée ou manuelle,
          ne démarrera tant que ce verrou n’est pas levé dans la configuration privée du service.
        </p>{/if}
      <form class="recovery-schedule" onsubmit={save}>
        <label class="web-check"
          ><input
            type="checkbox"
            bind:checked={enabled}
            onchange={() => (dirty = true)}
            disabled={!!busy || !state?.configured}
          /> Activer chaque nuit</label
        >
        <label class="web-field"
          ><span>Horaire quotidien · UTC</span><input
            type="time"
            required
            bind:value={hour}
            oninput={() => (dirty = true)}
            disabled={!!busy || !state?.configured}
          /></label
        >
        <label class="web-field"
          ><span>Captures conservées</span><input
            type="number"
            min="1"
            max="30"
            required
            bind:value={retention}
            oninput={() => (dirty = true)}
            disabled={!!busy || !state?.configured}
          /></label
        >
        <button class="web-button" type="submit" disabled={!!busy || !dirty || !state?.configured}
          >{busy === 'settings' ? 'Enregistrement…' : 'Enregistrer'}</button
        >
        {#if enabled && afterTime(hour, clock)}<label class="web-check recovery-today"
            ><input
              type="checkbox"
              bind:checked={runToday}
              onchange={() => (dirty = true)}
              disabled={!!busy || !state?.configured}
            /> Lancer aussi aujourd’hui (l’horaire du jour est passé ; sinon première capture demain)</label
          >{/if}
      </form>
      <p
        class="web-help"
        class:recovery-warning={!dirty && enabled && scheduleStatus(state?.settings) !== 'active'}
        role={!dirty && enabled && scheduleStatus(state?.settings) !== 'active' ? 'note' : undefined}
      >
        {dirty
          ? 'Modifications non enregistrées.'
          : !enabled
            ? 'La capture automatique est désactivée.'
            : scheduleStatus(state?.settings) === 'active' && state?.settings.nextRunAt
              ? `Prochaine capture : ${webDate(state.settings.nextRunAt)} (heure locale).`
              : state?.settings.statusMessage ||
                'Planification enregistrée, mais le service ne peut pas l’exécuter.'}{#if !dirty && enabled && scheduleStatus(state?.settings) !== 'active' && state?.settings.plannedRunAt}
          Horaire prévu : {webDate(state.settings.plannedRunAt)} (heure locale), sans exécution garantie.{/if}
      </p>
    </section>
    {#if state?.active}<div class="web-panel recovery-active" role="status">
        <div>
          <span class="web-eyebrow">TRAITEMENT EN COURS</span>
          <h2>{state.active.operation === 'drill' ? 'Test de reprise isolée' : 'Capture de la plateforme'}</h2>
          <p>{state.active.stage || label(state.active.status)}</p>
          {#if state.active.progress}<progress
              max={state.active.progress.total}
              value={state.active.progress.completed}
              aria-label="Progression du traitement"
            ></progress>
            <p class="web-help">
              {state.active.progress.unit === 'bytes'
                ? `${bytes(state.active.progress.completed)} / ${bytes(state.active.progress.total)}`
                : `${state.active.progress.completed} / ${state.active.progress.total} éléments`}
            </p>{/if}
        </div>
        <div class="recovery-running" aria-label="Traitement en cours"></div>
      </div>{/if}
    {#if selected}<section class="web-panel recovery-preview" aria-labelledby="recovery-confirm">
        <h2 id="recovery-confirm">Vérifier cette capture</h2>
        <p>
          Le test récupère uniquement le contrôleur et la petite base définie dans le plan de test privé, dans un
          nouveau répertoire isolé. Il conserve une réserve de 15 Gio sur le serveur.
        </p>
        <p class="web-help">
          Les données récupérées restent en pause, sans hooks, sans démarrage de service et sans remplacement de la
          production. Ce test ne prouve pas une restauration complète de toutes les bases.
        </p>
        {#if state?.drillSelection?.length}<p class="web-help">Sélection : {state.drillSelection.join(', ')}</p>{/if}
        <div class="web-actions">
          <button class="web-button web-button--primary" type="button" onclick={drill} disabled={!!busy}
            >Lancer le test isolé</button
          ><button class="web-button" type="button" onclick={() => (selected = null)} disabled={!!busy}>Annuler</button>
        </div>
      </section>{/if}
    <section class="web-panel">
      <div class="web-row">
        <div>
          <h2>Historique des opérations</h2>
          <p class="web-help">
            {totalItems} opération(s) · Les clés, chemins privés et contenus des bases ne sont jamais affichés.
          </p>
        </div>
        <button class="web-button" type="button" onclick={load} disabled={refreshing}>Actualiser</button>
      </div>
      {#if !items.length}<div class="web-empty">
          <h3>Pas encore de capture</h3>
          <p>Lancez une première capture pour vérifier la couverture réelle du plan.</p>
        </div>{/if}
      <div class="recovery-history">
        {#each items as job (job.id)}<article class="recovery-row">
            <div>
              <div class="recovery-row-title">
                <strong>{job.operation === 'drill' ? 'Test isolé' : 'Capture chiffrée'}</strong><span
                  class="recovery-status"
                  class:recovery-ok={terminal(job)}
                  class:recovery-error={job.status === 'failed'}>{label(job.status)}</span
                >{#if job.trigger}<span class="web-help">{triggers[job.trigger] || job.trigger}</span
                  >{/if}{#if job.held}<span
                    class="recovery-status"
                    title="Capture réservée par un exercice : la rétention ne la supprime pas avant la fin de la réservation."
                    >Réservée</span
                  >{/if}
              </div>
              <p class="web-help">
                {webDate(job.created || job.startedAt)}{#if job.finishedAt}
                  · terminée le {webDate(job.finishedAt)}{/if}
              </p>
              {#if job.stage}<p class="web-help">{job.stage}</p>{/if}{#if job.error}<p
                  class="recovery-error"
                  role="alert"
                >
                  {job.error}
                </p>{/if}{#if terminal(job)}<p class="web-help">
                  {job.operation === 'drill'
                    ? 'Données isolées en pause'
                    : verified(job)
                      ? 'Archive vérifiée'
                      : 'Vérification non confirmée'}{#if coverage(job)}
                    · couverture {coverage(job)?.complete
                      ? 'complète'
                      : 'partielle'}{/if}{#if job.encryptedBytes || job.bytes}
                    · {bytes(job.encryptedBytes || job.bytes)}{/if}
                </p>{/if}{#if job.result?.databasesRestored?.length}<p class="web-help">
                  Bases effectivement récupérées : {job.result.databasesRestored.join(', ')}
                </p>{/if}{#if job.result?.verification}<p
                  class="web-help"
                  class:recovery-error={!job.result.verification.passed}
                >
                  Contrôle de la copie : {job.result.verification.passed ? 'réussi' : 'échoué'} · {job.result
                    .verification.databases} base(s), {job.result.verification.files} fichier(s), {job.result
                    .verification.mfaSidecars}
                  clé(s) MFA
                </p>{/if}{#if job.result?.cleanedUp !== undefined}<p class="web-help">
                  {job.result.cleanedUp ? 'Copie isolée supprimée.' : 'Copie isolée conservée : à supprimer.'}
                </p>{/if}{#if job.webBackups && (job.webBackups.created || job.webBackups.reused)}<p class="web-help">
                  {siteArchives(job.webBackups)}
                </p>{/if}
            </div>
            {#if job.operation !== 'drill' && terminal(job)}<button
                class="web-button"
                type="button"
                onclick={() => (selected = job)}
                disabled={!!busy ||
                  !verified(job) ||
                  job.recoverable === false ||
                  !!state?.active ||
                  !state?.worker?.online ||
                  state?.capabilities?.canDrill === false}
                >{job.retentionPending
                  ? 'Purge incomplète'
                  : job.retained === false
                    ? 'Capture purgée'
                    : 'Tester la reprise'}</button
              >{/if}
          </article>{/each}
      </div>
      <div class="web-pagination">
        <button class="web-button" type="button" onclick={() => changePage(page - 1)} disabled={page <= 1 || refreshing}
          >Précédent</button
        ><span>Page {page} / {totalPages}</span><button
          class="web-button"
          type="button"
          onclick={() => changePage(page + 1)}
          disabled={page >= totalPages || refreshing}>Suivant</button
        >
      </div>
    </section>
    <!-- Canal des superAdmins : échecs de reprise, worker muet, copies des échecs d'applications. -->
    <WebAppNotifications scope="platform" />
  {/if}
</div>

<style>
  .recovery-shell {
    display: grid;
    gap: 1.5rem;
    max-width: 1200px;
    min-width: 0;
  }
  .recovery-stats {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
  }
  .recovery-stats .web-panel {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }
  .recovery-stats strong {
    font-size: 1.1rem;
    overflow-wrap: anywhere;
  }
  .recovery-stats small {
    color: inherit;
  }
  .recovery-schedule {
    display: grid;
    grid-template-columns: minmax(180px, 1fr) 180px 150px auto;
    align-items: end;
    gap: 16px;
  }
  .recovery-schedule .web-check {
    min-height: 42px;
  }
  .recovery-active {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-color: var(--web-green);
  }
  .recovery-running {
    width: 28px;
    height: 28px;
    border: 3px solid var(--web-border);
    border-top-color: var(--web-green);
    border-radius: 50%;
    animation: turn 1s linear infinite;
    flex-shrink: 0;
  }
  .recovery-preview {
    border-color: var(--web-green);
  }
  .recovery-history {
    display: grid;
  }
  .recovery-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    padding: 20px 0;
    border-top: 1px solid var(--web-border);
  }
  .recovery-row > div {
    min-width: 0;
  }
  .recovery-row p {
    overflow-wrap: anywhere;
  }
  .recovery-row-title {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }
  .recovery-status {
    font-size: 0.75rem;
    font-weight: 700;
    padding: 4px 9px;
    background: var(--web-surface);
    border-radius: 99px;
  }
  .recovery-ok {
    color: var(--web-green);
  }
  .recovery-error {
    color: var(--wa-color-danger-60, #b93838);
  }
  .recovery-warning {
    color: var(--wa-color-warning-60, #a15c00);
    font-weight: 600;
  }
  .recovery-today {
    grid-column: 1 / -1;
  }
  @keyframes turn {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .recovery-running {
      animation: none;
    }
  }
  @media (max-width: 780px) {
    .recovery-stats {
      grid-template-columns: 1fr;
    }
    .recovery-schedule {
      grid-template-columns: 1fr 1fr;
    }
    .recovery-row {
      align-items: flex-start;
      flex-direction: column;
      gap: 8px;
    }
  }
  @media (max-width: 480px) {
    .recovery-schedule {
      grid-template-columns: 1fr;
    }
  }
</style>
