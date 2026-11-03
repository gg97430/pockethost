<script lang="ts">
  import { onMount } from 'svelte'
  import ResourcesOverview from '$components/monitoring/ResourcesOverview.svelte'
  import OperatorControls from '$components/monitoring/OperatorControls.svelte'
  import IncidentDiagnostics from '$components/monitoring/IncidentDiagnostics.svelte'
  import type { PlatformDiagnostics } from '../../../../../pockethost/src/common/platformDiagnostics'
  import { client } from '$src/pocketbase-client'
  import { userStore } from '$util/stores'
  import type {
    MonitoringReport,
    MonitorState,
    MonitorGroup,
  } from '../../../../../pockethost/src/common/platformMonitoring'
  import '$lib/webApps.css'

  type AlertSettings = {
    emailEnabled: boolean
    emailRecipients: string[]
    discordEnabled: boolean
    hasDiscordWebhook: boolean
    revision: string
    smtpReady: boolean
  }
  let view: 'resources' | 'checks' = 'resources'
  let resourcesBusy = false
  let resourcesRefresh = 0
  const selectView = (next: 'resources' | 'checks') => {
    view = next
    if (next === 'checks' && loaded) void load()
  }
  const refresh = () => {
    if (view === 'resources') resourcesRefresh++
    else void load()
  }
  const tabKeys = (event: KeyboardEvent) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const next =
      event.key === 'Home'
        ? 'resources'
        : event.key === 'End'
          ? 'checks'
          : view === 'resources'
            ? 'checks'
            : 'resources'
    selectView(next)
    document.getElementById(`monitor-tab-${next}`)?.focus()
  }
  let settings: AlertSettings | null = null,
    emailEnabled = false,
    emailRecipients = '',
    discordEnabled = false,
    discordWebhook = '',
    clearDiscordWebhook = false,
    dirty = false,
    saving = false,
    saved = ''
  const applySettings = (next: AlertSettings) => {
    settings = next
    emailEnabled = next.emailEnabled
    emailRecipients = next.emailRecipients.join('\n')
    discordEnabled = next.discordEnabled
    discordWebhook = ''
    clearDiscordWebhook = false
    dirty = false
  }

  let report: MonitoringReport | null = null,
    available = false,
    stale = true,
    busy = false,
    loaded = false,
    error = '',
    query = '',
    group: MonitorGroup | 'all' = 'all',
    issuesOnly = false,
    now = Date.now()
  let diagnostics: PlatformDiagnostics | null = null
  const labels: Record<MonitorState, string> = {
    ok: 'OK',
    warning: 'À surveiller',
    error: 'En erreur',
    unknown: 'Sans mesure',
    paused: 'En pause',
  }
  const groups: { id: MonitorGroup | 'all'; label: string }[] = [
    { id: 'all', label: 'Tout' },
    { id: 'databases', label: 'Bases de données' },
    { id: 'hosting', label: 'Hébergement' },
    { id: 'backups', label: 'Sauvegardes' },
    { id: 'infrastructure', label: 'Serveur' },
    { id: 'security', label: 'Protections' },
  ]
  $: canAccess = !!$userStore?.superAdmin && !$userStore?.suspension
  $: if (canAccess && view === 'checks' && !loaded) {
    loaded = true
    void load()
  }
  $: isStale = stale || !report || now - Date.parse(report.checkedAt) > 180000
  $: rows = (report?.checks || [])
    .filter(
      (row) =>
        (group === 'all' || row.group === group) &&
        (!issuesOnly || ['error', 'warning', 'unknown'].includes(row.state)) &&
        `${row.name} ${row.detail}`.toLowerCase().includes(query.toLowerCase())
    )
    .sort(
      (a, b) =>
        ['error', 'warning', 'unknown', 'ok', 'paused'].indexOf(a.state) -
          ['error', 'warning', 'unknown', 'ok', 'paused'].indexOf(b.state) || a.name.localeCompare(b.name)
    )
  $: databases = report?.checks.filter((row) => row.id.startsWith('database:')) || []
  $: sites = report?.checks.filter((row) => row.id.startsWith('web:')) || []
  async function load() {
    if (!canAccess || busy) return
    busy = true
    try {
      const next = await client().client.send<{
        available: boolean
        stale: boolean
        report: MonitoringReport | null
        diagnostics: PlatformDiagnostics | null
        settings: AlertSettings
      }>('/api/admin/monitoring', { method: 'GET', requestKey: 'platform-monitoring' })
      report = next.report
      diagnostics = next.diagnostics
      if (!dirty && !saving) applySettings(next.settings)
      available = next.available
      stale = next.stale
      now = Date.now()
      error = ''
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : 'Lecture de la supervision impossible.'
    } finally {
      busy = false
    }
  }
  async function saveAlerts() {
    if (!settings || saving) return
    saving = true
    saved = ''
    error = ''
    try {
      const next = await client().client.send<{ settings: AlertSettings }>('/api/admin/monitoring', {
        method: 'PATCH',
        body: {
          revision: settings.revision,
          emailEnabled,
          emailRecipients: emailRecipients
            .split(/[\n,;]+/)
            .map((value) => value.trim())
            .filter(Boolean),
          discordEnabled,
          discordWebhook,
          clearDiscordWebhook,
        },
      })
      applySettings(next.settings)
      saved = 'Notifications enregistrées. Les changements s’appliquent au prochain contrôle.'
    } catch (e) {
      error = e instanceof Error ? client().parseError(e)[0] || e.message : 'Enregistrement impossible.'
    } finally {
      saving = false
    }
  }
  onMount(() => {
    const timer = setInterval(() => {
      now = Date.now()
      if (!document.hidden && view === 'checks') void load()
    }, 15000)
    return () => clearInterval(timer)
  })
</script>

<svelte:head><title>Supervision · Kolab Hosting</title></svelte:head>
<main class="webapps monitor-page">
  <div class="web-header">
    <div class="web-title">
      <span class="web-eyebrow">Exploitation</span>
      <h1>Supervision</h1>
      <p class="web-subtitle">Ressources du serveur, bases de données et hébergement.</p>
    </div>
    <button
      class="web-button secondary"
      onclick={refresh}
      disabled={(view === 'resources' ? resourcesBusy : busy) || !canAccess}
      ><wa-icon name="rotate"></wa-icon>{(view === 'resources' ? resourcesBusy : busy)
        ? 'Actualisation…'
        : 'Actualiser'}</button
    >
  </div>
  {#if !canAccess}
    <p class="web-notice" role="alert">Cette page est réservée aux administrateurs de la plateforme.</p>
  {:else}
    <div class="monitor-tabs" role="tablist" aria-label="Vues de supervision">
      <button
        id="monitor-tab-resources"
        role="tab"
        type="button"
        aria-selected={view === 'resources'}
        aria-controls="monitor-panel-resources"
        tabindex={view === 'resources' ? 0 : -1}
        onkeydown={tabKeys}
        onclick={() => selectView('resources')}><wa-icon name="chart-line"></wa-icon>Ressources</button
      >
      <button
        id="monitor-tab-checks"
        role="tab"
        type="button"
        aria-selected={view === 'checks'}
        aria-controls="monitor-panel-checks"
        tabindex={view === 'checks' ? 0 : -1}
        onkeydown={tabKeys}
        onclick={() => selectView('checks')}><wa-icon name="heart-pulse"></wa-icon>Contrôles et alertes</button
      >
    </div>
    <div
      id="monitor-panel-resources"
      class="monitor-tab-panel"
      role="tabpanel"
      aria-labelledby="monitor-tab-resources"
      hidden={view !== 'resources'}
    >
      <OperatorControls />
      <ResourcesOverview
        active={view === 'resources' && canAccess}
        refreshKey={resourcesRefresh}
        bind:busy={resourcesBusy}
      />
    </div>
    <div
      id="monitor-panel-checks"
      class="monitor-tab-panel"
      role="tabpanel"
      aria-labelledby="monitor-tab-checks"
      hidden={view !== 'checks'}
    >
      {#if error}<p class="web-notice error" role="alert">{error}</p>{/if}
      <section class="monitor-status" class:monitor-stale={isStale} aria-label="État du monitoring">
        <div class="monitor-status-title">
          <span class="monitor-dot" data-state={isStale ? 'unknown' : report?.state || 'unknown'}></span><strong
            >{!available
              ? 'Premier contrôle attendu'
              : isStale
                ? 'Contrôle périmé'
                : report?.state === 'ok'
                  ? 'Contrôles opérationnels'
                  : 'Points à vérifier'}</strong
          >
        </div>
        <div class="monitor-stamp">
          {report?.domain || 'Serveur actuel'}{#if report}<span
              >Dernier contrôle <time datetime={report.checkedAt}
                >{new Date(report.checkedAt).toLocaleString('fr-FR')}</time
              ></span
            >{/if}
        </div>
        {#if isStale}<p>
            Les mesures absentes ou vieilles de plus de trois minutes ne confirment pas la santé des services.
          </p>{/if}
      </section>
      <div class="monitor-metrics">
        <div>
          <span>Bases de données</span><strong
            >{databases.filter((row) => row.state === 'ok').length}<small> / {databases.length}</small></strong
          ><span>répondent au contrôle HTTP</span>
        </div>
        <div>
          <span>Applications web</span><strong
            >{sites.filter((row) => row.state === 'ok').length}<small> / {sites.length}</small></strong
          ><span>publications actives contrôlées</span>
        </div>
        <div>
          <span>À vérifier</span><strong class:monitor-count-error={(report?.counts.error || 0) > 0}
            >{(report?.counts.error || 0) + (report?.counts.warning || 0) + (report?.counts.unknown || 0)}</strong
          ><span>erreurs, alertes et mesures absentes</span>
        </div>
        <div>
          <span>En pause</span><strong>{report?.counts.paused || 0}</strong><span
            >arrêts volontaires et fonctions désactivées</span
          >
        </div>
      </div>
      <IncidentDiagnostics {diagnostics} stale={isStale} />
      {#if settings}
        <details class="monitor-alert-settings">
          <summary>Notifications <span>E-mail et Discord · ce serveur</span></summary>
          <div class="monitor-alert-body">
            <div class="monitor-alert-grid">
              <div class="monitor-alert-channel">
                <label class="web-check"
                  ><input
                    type="checkbox"
                    bind:checked={emailEnabled}
                    onchange={() => {
                      dirty = true
                    }}
                    disabled={saving}
                  /><strong>Activer les e-mails</strong></label
                >
                <label class="web-field"
                  ><span>Destinataires</span><textarea
                    rows="3"
                    bind:value={emailRecipients}
                    oninput={() => {
                      dirty = true
                    }}
                    disabled={saving}
                    placeholder="Une adresse par ligne"
                  ></textarea></label
                >
                {#if !settings.smtpReady}<p class="web-help">
                    Configurez le relais SMTP dans <a href="/admin">Administration</a> avant d’activer ce canal.
                  </p>{:else}<p class="web-help">Utilise le relais SMTP configuré pour ce serveur.</p>{/if}
              </div>
              <div class="monitor-alert-channel">
                <label class="web-check"
                  ><input
                    type="checkbox"
                    bind:checked={discordEnabled}
                    onchange={() => {
                      dirty = true
                    }}
                    disabled={saving}
                  /><strong>Activer Discord</strong></label
                >
                <label class="web-field"
                  ><span>{settings.hasDiscordWebhook ? 'Remplacer le webhook' : 'Webhook Discord'}</span><input
                    type="password"
                    autocomplete="new-password"
                    bind:value={discordWebhook}
                    oninput={() => {
                      dirty = true
                    }}
                    disabled={saving}
                    placeholder={settings.hasDiscordWebhook
                      ? 'Webhook enregistré · laisser vide pour le conserver'
                      : 'https://discord.com/api/webhooks/…'}
                  /></label
                >
                <p class="web-help">Le webhook reste masqué après l’enregistrement.</p>
                {#if settings.hasDiscordWebhook}<label class="web-check"
                    ><input
                      type="checkbox"
                      bind:checked={clearDiscordWebhook}
                      onchange={() => {
                        dirty = true
                      }}
                      disabled={saving}
                    />Effacer le webhook enregistré</label
                  >{/if}
              </div>
            </div>
            <div class="web-actions">
              <button class="web-button" onclick={saveAlerts} disabled={saving || !dirty}
                >{saving ? 'Enregistrement…' : 'Enregistrer les notifications'}</button
              >{#if dirty}<button
                  class="web-button secondary"
                  onclick={() => {
                    if (settings) applySettings(settings)
                  }}
                  disabled={saving}>Annuler</button
                >{/if}
            </div>
            {#if saved}<p role="status" class="web-help">{saved}</p>{/if}
          </div>
        </details>
      {/if}
      <div class="monitor-toolbar">
        <nav class="monitor-tabs" aria-label="Type de contrôle">
          {#each groups as item}<button
              onclick={() => (group = item.id)}
              class:active={group === item.id}
              aria-pressed={group === item.id}>{item.label}</button
            >{/each}
        </nav>
        <div class="monitor-filters">
          <input
            type="search"
            bind:value={query}
            aria-label="Rechercher un contrôle"
            placeholder="Rechercher une base, un site…"
          /><label><input type="checkbox" bind:checked={issuesOnly} /> Points à vérifier uniquement</label>
        </div>
      </div>
      <div class="monitor-table-wrap">
        <table>
          <thead
            ><tr
              ><th scope="col">Ressource</th><th scope="col">État</th><th scope="col">Dernière mesure</th><th
                scope="col">Réponse</th
              ></tr
            ></thead
          ><tbody>
            {#each rows as row (row.id)}<tr
                ><td
                  >{#if row.href}<a href={row.href}>{row.name}<wa-icon name="arrow-right"></wa-icon></a
                    >{:else}{row.name}{/if}</td
                ><td
                  ><span class="monitor-badge" data-state={row.state}
                    ><span class="monitor-dot" data-state={row.state}></span>{labels[row.state]}</span
                  ></td
                ><td class="monitor-detail">{row.detail}</td><td class="monitor-latency"
                  >{row.latencyMs === undefined ? '—' : `${row.latencyMs} ms`}</td
                ></tr
              >{:else}<tr
                ><td colspan="4" class="monitor-empty"
                  >{!available
                    ? 'Le collecteur publiera son premier état au prochain passage.'
                    : 'Aucun contrôle ne correspond aux filtres.'}</td
                ></tr
              >{/each}
          </tbody>
        </table>
      </div>
      <p class="web-help">
        Collecte chaque minute, page actualisée toutes les 15 secondes. Une panne et son rétablissement sont confirmés
        sur deux contrôles avant notification. La lecture de cette page ne réveille pas les bases en veille. Les sondes
        web vérifient la réponse HTTP de la publication ; elles ne valident pas ses parcours métier.
      </p>
    </div>
  {/if}
</main>

<style>
  .monitor-tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 1.4rem;
    border-bottom: 1px solid var(--web-border);
  }
  .monitor-tabs button {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    min-height: 43px;
    padding: 0.55rem 0 0.8rem;
    margin-bottom: -1px;
    border: 0;
    border-bottom: 2px solid transparent;
    color: var(--web-muted);
    background: transparent;
    font: inherit;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .monitor-tabs button[aria-selected='true'] {
    border-bottom-color: var(--web-accent);
    color: var(--web-accent);
  }
  .monitor-tabs button:focus-visible {
    outline: 2px solid var(--web-accent);
    outline-offset: 3px;
  }
  .monitor-tab-panel {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .monitor-tab-panel[hidden] {
    display: none;
  }

  .monitor-alert-settings {
    border: 1px solid var(--web-border);
    border-radius: 6px;
    background: var(--web-surface);
  }
  .monitor-alert-settings summary {
    padding: 1rem 1.2rem;
    cursor: pointer;
    font-weight: 600;
  }
  .monitor-alert-settings summary span {
    font-weight: 400;
    color: var(--web-muted);
    margin-left: 0.65rem;
    font-size: 0.75rem;
  }
  .monitor-alert-body {
    display: grid;
    gap: 1rem;
    padding: 1rem 1.2rem 1.2rem;
  }
  .monitor-alert-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1.5rem;
  }
  .monitor-alert-channel {
    display: grid;
    gap: 0.75rem;
    align-content: start;
    min-width: 0;
  }
  .monitor-alert-channel input[type='checkbox'] {
    appearance: auto;
    width: 16px;
    height: 16px;
  }
  @media (max-width: 700px) {
    .monitor-alert-grid {
      grid-template-columns: 1fr;
    }
    .monitor-alert-settings summary span {
      display: block;
      margin-left: 1rem;
      margin-top: 0.35rem;
    }
  }
  .monitor-page {
    max-width: 1500px;
    margin: 0 auto;
  }
  .monitor-status {
    padding: 1rem 1.2rem;
    border: 1px solid var(--web-border);
    border-left: 3px solid var(--web-success);
    border-radius: 6px;
    background: var(--web-surface);
  }
  .monitor-stale {
    border-left-color: #e6b657;
  }
  .monitor-status-title {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }
  .monitor-stamp {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.5rem;
    margin-top: 0.45rem;
    color: var(--web-muted);
  }
  .monitor-status p {
    margin-top: 0.5rem;
    color: var(--web-muted);
  }
  .monitor-dot {
    display: inline-block;
    flex: 0 0 auto;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--web-muted);
  }
  [data-state='ok'].monitor-dot {
    background: var(--web-success);
  }
  [data-state='error'].monitor-dot {
    background: #f07878;
  }
  [data-state='warning'].monitor-dot,
  [data-state='unknown'].monitor-dot {
    background: #e6b657;
  }
  .monitor-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    border: 1px solid var(--web-border);
    border-radius: 6px;
    background: var(--web-surface);
  }
  .monitor-metrics > div {
    display: grid;
    gap: 0.45rem;
    padding: 1rem 1.2rem;
    border-right: 1px solid var(--web-border);
  }
  .monitor-metrics > div:last-child {
    border-right: 0;
  }
  .monitor-metrics span {
    font-size: 0.75rem;
    color: var(--web-muted);
  }
  .monitor-metrics strong {
    font-size: 1.85rem;
    line-height: 1.15;
    letter-spacing: -0.04em;
    font-weight: 600;
  }
  .monitor-metrics small {
    font-size: 1rem;
    color: var(--web-muted);
    letter-spacing: 0;
  }
  .monitor-count-error {
    color: #f07878;
  }
  .monitor-toolbar {
    display: grid;
    gap: 1rem;
  }
  .monitor-tabs {
    display: flex;
    gap: 0.3rem;
    overflow-x: auto;
    padding-bottom: 0.25rem;
  }
  .monitor-tabs button {
    flex: 0 0 auto;
    padding: 0.5rem 0.8rem;
    border: 1px solid transparent;
    border-radius: 5px;
    color: var(--web-muted);
    background: transparent;
    cursor: pointer;
    font: inherit;
  }
  .monitor-tabs button.active {
    color: var(--app-text);
    border-color: var(--web-border);
    background: var(--web-surface);
  }
  .monitor-filters {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
  }
  .monitor-filters > input {
    min-width: 0;
    width: min(100%, 360px);
    padding: 0.55rem 0.75rem;
    border: 1px solid var(--web-border);
    background: var(--web-surface);
    color: inherit;
    border-radius: 5px;
  }
  .monitor-filters label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    color: var(--web-muted);
  }
  .monitor-filters input[type='checkbox'] {
    appearance: auto;
    width: 16px;
    height: 16px;
    accent-color: var(--web-accent);
  }
  .monitor-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--web-border);
    border-radius: 6px;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.8125rem;
  }
  th,
  td {
    padding: 0.85rem 1rem;
    text-align: left;
    border-bottom: 1px solid var(--web-border);
  }
  th {
    color: var(--web-muted);
    font-size: 0.7rem;
    font-weight: 500;
    background: var(--web-surface);
  }
  tr:last-child td {
    border-bottom: 0;
  }
  td:first-child {
    min-width: 180px;
    max-width: 300px;
    overflow-wrap: anywhere;
    font-weight: 500;
  }
  td a {
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    text-decoration: none;
  }
  td a:hover {
    text-decoration: underline;
  }
  td a wa-icon {
    font-size: 0.7rem;
    color: var(--web-muted);
  }
  .monitor-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    white-space: nowrap;
    font-size: 0.75rem;
  }
  .monitor-detail {
    color: var(--web-muted);
    min-width: 220px;
  }
  .monitor-latency {
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    color: var(--web-muted);
  }
  .monitor-empty {
    text-align: center;
    padding: 2.5rem 1rem;
    color: var(--web-muted);
  }
  @media (max-width: 700px) {
    .monitor-metrics {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .monitor-metrics > div:nth-child(2) {
      border-right: 0;
    }
    .monitor-metrics > div:nth-child(-n + 2) {
      border-bottom: 1px solid var(--web-border);
    }
  }
</style>
