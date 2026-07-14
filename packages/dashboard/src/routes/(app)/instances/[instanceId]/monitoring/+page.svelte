<script lang="ts">
  import FeatureTab from '$components/FeatureTab.svelte'
  import {
    client,
    type InstanceMonitoringHistoryPoint,
    type InstanceMonitoringHistoryRange,
    type InstanceMonitoringHistoryResponse,
    type InstanceMonitoringIncident,
    type InstanceMonitoringPolicy,
    type InstanceMonitoringResponse,
    type UpdateInstanceMonitoringInput,
  } from '$src/pocketbase-client'
  import { onDestroy, onMount } from 'svelte'
  import { instance } from '../store'

  type MonitoringDraft = UpdateInstanceMonitoringInput & {
    discordWebhook: string
    slackWebhook: string
  }

  const recommended = {
    healthPath: '/api/health',
    healthFailureCount: 3,
    cpuThresholdPercent: 85,
    cpuSustainMinutes: 5,
    memoryThresholdPercent: 85,
    memorySustainMinutes: 5,
  }
  const ranges: Array<{ value: InstanceMonitoringHistoryRange; label: string }> = [
    { value: '24h', label: '24 h' },
    { value: '7d', label: '7 jours' },
    { value: '30d', label: '30 jours' },
  ]
  const chart = { left: 44, top: 18, width: 672, height: 172 }
  const dateFormatter = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' })
  const compactTimeFormatter = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' })
  const compactDateFormatter = new Intl.DateTimeFormat('fr-FR', { day: '2-digit', month: 'short' })
  const percentFormatter = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 3 })
  const metricFormatter = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 })

  let response: InstanceMonitoringResponse | undefined
  let policy: InstanceMonitoringPolicy | undefined
  let draft: MonitoringDraft | undefined
  let history: InstanceMonitoringHistoryResponse | undefined
  let incidents: InstanceMonitoringIncident[] = []
  let nextCursor = ''
  let selectedRange: InstanceMonitoringHistoryRange = '24h'
  let isLoading = true
  let isSaving = false
  let isTesting = false
  let isLoadingHistory = false
  let isLoadingMore = false
  let errorMessage = ''
  let successMessage = ''
  let refreshTimer: ReturnType<typeof setInterval> | undefined

  $: ({ id, power } = $instance)
  $: openIncidents = incidents.filter((incident) => incident.status === 'open')
  $: status = monitoringStatus(policy, power)
  $: points = history?.points || []
  $: availabilityPath = buildAvailabilityPath(points)
  $: latencyPath = buildLatencyPath(points)
  $: maxLatency = Math.max(100, ...points.map((point) => point.maxLatencyMs || 0))
  $: axisTimes = historyAxisTimes(selectedRange)

  const createDraft = (value: InstanceMonitoringPolicy): MonitoringDraft => ({
    enabled: value.enabled,
    healthEnabled: value.healthEnabled,
    healthPath: value.healthPath,
    healthFailureCount: value.healthFailureCount,
    cpuEnabled: value.cpuEnabled,
    cpuThresholdPercent: value.cpuThresholdPercent,
    cpuSustainMinutes: value.cpuSustainMinutes,
    memoryEnabled: value.memoryEnabled,
    memoryThresholdPercent: value.memoryThresholdPercent,
    memorySustainMinutes: value.memorySustainMinutes,
    backupAlertsEnabled: value.backupAlertsEnabled,
    emailEnabled: value.emailEnabled,
    discordEnabled: value.discordEnabled,
    slackEnabled: value.slackEnabled,
    discordWebhook: '',
    slackWebhook: '',
    clearDiscordWebhook: false,
    clearSlackWebhook: false,
  })

  function monitoringStatus(value: InstanceMonitoringPolicy | undefined, isPowered: boolean) {
    if (!value?.enabled) return { key: 'disabled', label: 'Désactivée', detail: 'Aucun contrôle automatique' }
    if (!isPowered || value.lastHealthStatus === 'paused') {
      return { key: 'paused', label: 'En pause', detail: 'Instance arrêtée volontairement' }
    }
    if (!value.healthEnabled) {
      return { key: 'healthy', label: 'Surveillance active', detail: 'Sonde HTTP désactivée · ressources surveillées' }
    }
    if (value.lastHealthStatus === 'unhealthy') {
      return { key: 'unhealthy', label: 'Incident', detail: value.lastError || 'Le contrôle HTTP échoue' }
    }
    if (value.lastHealthStatus === 'healthy') {
      return { key: 'healthy', label: 'Opérationnelle', detail: `${value.lastLatencyMs ?? 0} ms au dernier contrôle` }
    }
    return { key: 'pending', label: 'Initialisation', detail: 'Premier contrôle en attente' }
  }

  const parseError = (error: unknown) =>
    error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`

  const formatDate = (value: string) => {
    const timestamp = Date.parse(value)
    return Number.isFinite(timestamp) ? dateFormatter.format(timestamp) : 'Aucune mesure'
  }

  const formatPercent = (value: number | null | undefined) =>
    value === null || value === undefined ? '—' : `${percentFormatter.format(value)} %`

  const formatMetricPercent = (value: number | null | undefined) =>
    value === null || value === undefined ? '—' : `${metricFormatter.format(value)} %`

  const incidentLabel = (type: InstanceMonitoringIncident['type']) => {
    if (type === 'health') return 'Disponibilité'
    if (type === 'cpu') return 'CPU'
    if (type === 'memory') return 'Mémoire'
    return 'Sauvegarde'
  }

  const incidentIcon = (type: InstanceMonitoringIncident['type']) => {
    if (type === 'health') return 'globe'
    if (type === 'cpu') return 'microchip'
    if (type === 'memory') return 'memory'
    return 'box-archive'
  }

  const rangeDuration = (range: InstanceMonitoringHistoryRange) => {
    if (range === '24h') return 24 * 60 * 60 * 1000
    if (range === '7d') return 7 * 24 * 60 * 60 * 1000
    return 30 * 24 * 60 * 60 * 1000
  }

  function historyAxisTimes(range: InstanceMonitoringHistoryRange) {
    const now = Date.now()
    const duration = rangeDuration(range)
    return [now - duration, now - duration / 2, now]
  }

  const xFor = (timestamp: number) => {
    const start = Date.now() - rangeDuration(selectedRange)
    return chart.left + ((timestamp - start) / rangeDuration(selectedRange)) * chart.width
  }

  const availabilityY = (value: number) =>
    chart.top + chart.height - (Math.max(0, Math.min(100, value)) / 100) * chart.height
  const latencyY = (value: number) => chart.top + chart.height - (Math.max(0, value) / maxLatency) * chart.height

  function buildAvailabilityPath(values: InstanceMonitoringHistoryPoint[]) {
    return values
      .filter((point) => point.availabilityPercent !== null)
      .map((point, index) => {
        const timestamp = Date.parse(point.collectedAt)
        return `${index === 0 ? 'M' : 'L'}${xFor(timestamp).toFixed(2)},${availabilityY(point.availabilityPercent!).toFixed(2)}`
      })
      .join(' ')
  }

  function buildLatencyPath(values: InstanceMonitoringHistoryPoint[]) {
    return values
      .filter((point) => Number.isFinite(point.averageLatencyMs))
      .map((point, index) => {
        const timestamp = Date.parse(point.collectedAt)
        return `${index === 0 ? 'M' : 'L'}${xFor(timestamp).toFixed(2)},${latencyY(point.averageLatencyMs).toFixed(2)}`
      })
      .join(' ')
  }

  const formatAxisTime = (timestamp: number) =>
    selectedRange === '24h' ? compactTimeFormatter.format(timestamp) : compactDateFormatter.format(timestamp)

  const loadPolicy = async (replaceDraft = false) => {
    const result = await client().getInstanceMonitoring(id)
    response = result
    policy = result.policy
    if (!draft || replaceDraft) draft = createDraft(result.policy)
  }

  const loadHistory = async (range = selectedRange) => {
    isLoadingHistory = true
    try {
      history = await client().getInstanceMonitoringHistory(id, range)
    } finally {
      isLoadingHistory = false
    }
  }

  const loadIncidents = async (append = false) => {
    const result = await client().getInstanceMonitoringIncidents(id, append ? nextCursor : '')
    incidents = append ? [...incidents, ...result.incidents] : result.incidents
    nextCursor = result.nextCursor
  }

  const loadPage = async () => {
    isLoading = true
    errorMessage = ''
    try {
      await Promise.all([loadPolicy(true), loadHistory(), loadIncidents()])
    } catch (error) {
      errorMessage = parseError(error)
    } finally {
      isLoading = false
    }
  }

  const refreshLiveState = async () => {
    if (isSaving || isTesting || (typeof document !== 'undefined' && document.hidden)) return
    try {
      await Promise.all([loadPolicy(false), loadHistory(), loadIncidents()])
    } catch {
      // Keep the last valid state visible during a transient refresh error.
    }
  }

  const selectRange = async (range: InstanceMonitoringHistoryRange) => {
    if (range === selectedRange) return
    selectedRange = range
    errorMessage = ''
    try {
      await loadHistory(range)
    } catch (error) {
      errorMessage = parseError(error)
    }
  }

  const saveMonitoring = async (showSuccess = true) => {
    if (!draft || isSaving) return false
    isSaving = true
    errorMessage = ''
    if (showSuccess) successMessage = ''
    try {
      const result = await client().updateInstanceMonitoring(id, draft)
      response = result
      policy = result.policy
      draft = createDraft(result.policy)
      if (showSuccess) successMessage = 'Configuration de surveillance enregistrée.'
      await Promise.all([loadHistory(), loadIncidents()])
      return true
    } catch (error) {
      errorMessage = parseError(error)
      return false
    } finally {
      isSaving = false
    }
  }

  const toggleMonitoring = async (event: Event) => {
    if (!draft || isSaving) return
    const enabled = (event.currentTarget as HTMLInputElement).checked
    const previousEnabled = policy?.enabled ?? !enabled
    draft = { ...draft, enabled }
    if (!(await saveMonitoring(false)) && draft) {
      draft = { ...draft, enabled: previousEnabled }
      return
    }
    successMessage = enabled ? 'Surveillance activée.' : 'Surveillance désactivée.'
  }

  const testNotifications = async () => {
    if (!draft || isTesting) return
    isTesting = true
    errorMessage = ''
    successMessage = ''
    try {
      if (!(await saveMonitoring(false))) return
      const result = await client().testInstanceMonitoring(id)
      const failed = result.results.filter((item) => item.status !== 'sent')
      if (failed.length) {
        errorMessage = failed.map((item) => `${item.channel} : ${item.error || item.status}`).join(' · ')
      } else {
        successMessage = `Test envoyé sur ${result.results.map((item) => item.channel).join(', ')}.`
      }
    } catch (error) {
      errorMessage = parseError(error)
    } finally {
      isTesting = false
    }
  }

  const resetRecommended = () => {
    if (!draft) return
    draft = { ...draft, ...recommended }
    successMessage = 'Valeurs recommandées restaurées. Enregistrez pour les appliquer.'
  }

  const clearDiscord = () => {
    if (!draft) return
    draft = { ...draft, discordWebhook: '', discordEnabled: false, clearDiscordWebhook: true }
  }

  const clearSlack = () => {
    if (!draft) return
    draft = { ...draft, slackWebhook: '', slackEnabled: false, clearSlackWebhook: true }
  }

  const loadMoreIncidents = async () => {
    if (!nextCursor || isLoadingMore) return
    isLoadingMore = true
    try {
      await loadIncidents(true)
    } catch (error) {
      errorMessage = parseError(error)
    } finally {
      isLoadingMore = false
    }
  }

  onMount(() => {
    void loadPage()
    refreshTimer = setInterval(refreshLiveState, 60_000)
  })

  onDestroy(() => {
    if (refreshTimer) clearInterval(refreshTimer)
  })
</script>

<FeatureTab title="Surveillance" bind:errorMessage {successMessage} successFlash>
  <svelte:fragment slot="summary">
    <p>
      PocketHost vérifie la disponibilité et les ressources chaque minute. Les incidents sont confirmés sur plusieurs
      mesures pour éviter les faux positifs, puis notifiés par les canaux configurés.
    </p>
  </svelte:fragment>

  {#if isLoading && !draft}
    <div class="monitor-loading">
      <wa-icon name="rotate"></wa-icon>
      Initialisation de la surveillance…
    </div>
  {:else if draft && policy}
    <form
      class="monitor-stack"
      onsubmit={(event) => {
        event.preventDefault()
        void saveMonitoring()
      }}
    >
      <section class="monitor-command monitor-command--{status.key}">
        <div class="monitor-command__signal" aria-hidden="true">
          <span></span>
          <wa-icon name="heart-pulse"></wa-icon>
        </div>
        <div class="monitor-command__copy">
          <span class="monitor-kicker">État de la sonde</span>
          <h3>{status.label}</h3>
          <p>{status.detail}</p>
        </div>
        <div class="monitor-command__meta">
          <span>Intervalle <strong>60 s</strong></span>
          <span>Rétention <strong>30 jours</strong></span>
          <span>Incidents ouverts <strong>{openIncidents.length}</strong></span>
        </div>
        <label class="master-switch">
          <input
            type="checkbox"
            checked={draft.enabled}
            disabled={isSaving}
            onchange={(event) => void toggleMonitoring(event)}
          />
          <span class="master-switch__track"><span></span></span>
          <span>{draft.enabled ? 'Activée' : 'Désactivée'}</span>
        </label>
      </section>

      <section class="monitor-metrics" aria-label="Résumé de la surveillance">
        <article>
          <span class="metric-label">Disponibilité {selectedRange === '24h' ? '24 h' : selectedRange}</span>
          <strong>{formatPercent(history?.summary.availabilityPercent)}</strong>
          <small
            >{history?.summary.successfulChecks || 0} contrôles réussis sur {history?.summary.totalChecks || 0}</small
          >
        </article>
        <article>
          <span class="metric-label">Latence moyenne</span>
          <strong>{history?.summary.averageLatencyMs ?? '—'}<i> ms</i></strong>
          <small>Dernière mesure : {policy.lastLatencyMs ?? '—'} ms</small>
        </article>
        <article>
          <span class="metric-label">Charge actuelle</span>
          <strong>{formatMetricPercent(policy.lastCpuCapacityPercent)}</strong>
          <small>Mémoire : {formatMetricPercent(policy.lastMemoryPercent)}</small>
        </article>
        <article>
          <span class="metric-label">Dernier contrôle</span>
          <strong class="metric-date">{formatDate(policy.lastCheckAt)}</strong>
          <small>{policy.lastStatusCode ? `HTTP ${policy.lastStatusCode}` : 'Pas encore de réponse HTTP'}</small>
        </article>
      </section>

      <section class="monitor-panel monitor-chart-panel">
        <header class="monitor-panel__header">
          <div>
            <span class="monitor-kicker">Disponibilité et latence</span>
            <h3>Historique de la sonde</h3>
          </div>
          <div class="range-selector" aria-label="Période du graphique">
            {#each ranges as range}
              <button
                type="button"
                class:active={selectedRange === range.value}
                onclick={() => void selectRange(range.value)}
              >
                {range.label}
              </button>
            {/each}
          </div>
        </header>

        <div class="monitor-legend">
          <span><i class="legend-line legend-line--availability"></i>Disponibilité</span>
          <span><i class="legend-line legend-line--latency"></i>Latence</span>
          {#if isLoadingHistory}<span class="chart-refreshing">Actualisation…</span>{/if}
        </div>

        {#if points.length}
          <div class="monitor-chart-scroll">
            <svg
              class="monitor-chart"
              viewBox="0 0 760 230"
              role="img"
              aria-label="Historique disponibilité et latence"
            >
              <defs>
                <linearGradient id="availabilityGlow" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stop-color="#55e98d" stop-opacity="0.95"></stop>
                  <stop offset="1" stop-color="#55e98d" stop-opacity="0.35"></stop>
                </linearGradient>
              </defs>
              {#each [0, 25, 50, 75, 100] as tick}
                <line
                  class="chart-grid"
                  x1={chart.left}
                  x2={chart.left + chart.width}
                  y1={availabilityY(tick)}
                  y2={availabilityY(tick)}
                ></line>
                <text class="chart-y-label" x="36" y={availabilityY(tick) + 4}>{tick}%</text>
              {/each}
              <path class="chart-line chart-line--availability" d={availabilityPath}></path>
              <path class="chart-line chart-line--latency" d={latencyPath}></path>
              {#each axisTimes as timestamp, index}
                <text
                  class="chart-x-label"
                  x={chart.left + (chart.width * index) / 2}
                  y="216"
                  text-anchor={index === 0 ? 'start' : index === 2 ? 'end' : 'middle'}
                >
                  {formatAxisTime(timestamp)}
                </text>
              {/each}
            </svg>
          </div>
        {:else}
          <div class="monitor-empty-chart">
            <wa-icon name="wave-square"></wa-icon>
            <strong>Aucune mesure sur cette période</strong>
            <span>Les contrôles commenceront après activation et démarrage de l’instance.</span>
          </div>
        {/if}
      </section>

      <section class="monitor-config-grid">
        <article class="signal-card signal-card--health">
          <header>
            <div class="signal-icon"><wa-icon name="globe"></wa-icon></div>
            <div>
              <span class="monitor-kicker">Sonde HTTP</span>
              <h3>Disponibilité</h3>
            </div>
            <label class="compact-toggle">
              <input type="checkbox" bind:checked={draft.healthEnabled} />
              <span></span>
            </label>
          </header>
          <p>Un code HTTP 2xx ou 3xx confirme que PocketBase répond.</p>
          <label class="field-block">
            <span>Chemin contrôlé</span>
            <input type="text" bind:value={draft.healthPath} maxlength="200" placeholder="/api/health" />
          </label>
          <div class="target-preview" title={response?.capabilities.targetUrl}>
            <wa-icon name="crosshairs"></wa-icon>
            <code>{response?.capabilities.targetUrl}</code>
          </div>
          <label class="field-block field-block--number">
            <span>Échecs avant alerte</span>
            <input type="number" bind:value={draft.healthFailureCount} min="1" max="10" />
            <small>contrôles consécutifs</small>
          </label>
        </article>

        <article class="signal-card signal-card--cpu">
          <header>
            <div class="signal-icon"><wa-icon name="microchip"></wa-icon></div>
            <div>
              <span class="monitor-kicker">Capacité Docker</span>
              <h3>CPU</h3>
            </div>
            <label class="compact-toggle">
              <input type="checkbox" bind:checked={draft.cpuEnabled} />
              <span></span>
            </label>
          </header>
          <p>Le seuil utilise la capacité CPU réellement disponible, y compris avec plusieurs cœurs.</p>
          <div class="threshold-row">
            <label class="field-block field-block--number">
              <span>Seuil</span>
              <input type="number" bind:value={draft.cpuThresholdPercent} min="1" max="100" step="1" />
              <small>% capacité</small>
            </label>
            <label class="field-block field-block--number">
              <span>Durée</span>
              <input type="number" bind:value={draft.cpuSustainMinutes} min="1" max="60" />
              <small>minutes</small>
            </label>
          </div>
          <div class="signal-reading">
            <span>Dernière mesure</span><strong>{formatMetricPercent(policy.lastCpuCapacityPercent)}</strong>
          </div>
        </article>

        <article class="signal-card signal-card--memory">
          <header>
            <div class="signal-icon"><wa-icon name="memory"></wa-icon></div>
            <div>
              <span class="monitor-kicker">Limite du conteneur</span>
              <h3>Mémoire</h3>
            </div>
            <label class="compact-toggle">
              <input type="checkbox" bind:checked={draft.memoryEnabled} />
              <span></span>
            </label>
          </header>
          <p>L’incident se résout après trois mesures revenues cinq points sous le seuil.</p>
          <div class="threshold-row">
            <label class="field-block field-block--number">
              <span>Seuil</span>
              <input type="number" bind:value={draft.memoryThresholdPercent} min="1" max="100" step="1" />
              <small>% mémoire</small>
            </label>
            <label class="field-block field-block--number">
              <span>Durée</span>
              <input type="number" bind:value={draft.memorySustainMinutes} min="1" max="60" />
              <small>minutes</small>
            </label>
          </div>
          <div class="signal-reading">
            <span>Dernière mesure</span><strong>{formatMetricPercent(policy.lastMemoryPercent)}</strong>
          </div>
        </article>

        <article class="signal-card signal-card--backup">
          <header>
            <div class="signal-icon"><wa-icon name="box-archive"></wa-icon></div>
            <div>
              <span class="monitor-kicker">Protection des données</span>
              <h3>Sauvegardes</h3>
            </div>
            <label class="compact-toggle">
              <input type="checkbox" bind:checked={draft.backupAlertsEnabled} />
              <span></span>
            </label>
          </header>
          <p>Une alerte part dès qu’une sauvegarde échoue. La prochaine sauvegarde réussie clôt l’incident.</p>
          <div class="backup-rule">
            <wa-icon name="bolt"></wa-icon>
            <span>Déclenchement immédiat</span>
            <strong>{draft.backupAlertsEnabled ? 'Armé' : 'Inactif'}</strong>
          </div>
        </article>
      </section>

      <section class="monitor-panel channels-panel">
        <header class="monitor-panel__header">
          <div>
            <span class="monitor-kicker">Canaux de sortie</span>
            <h3>Notifications</h3>
          </div>
          <button type="button" class="secondary-action" disabled={isTesting || isSaving} onclick={testNotifications}>
            <wa-icon name={isTesting ? 'rotate' : 'paper-plane'}></wa-icon>
            {isTesting ? 'Test en cours…' : 'Tester les canaux'}
          </button>
        </header>

        <div class="channel-list">
          <article class:active={draft.emailEnabled}>
            <label class="channel-toggle">
              <input type="checkbox" bind:checked={draft.emailEnabled} />
              <span><wa-icon name="envelope"></wa-icon></span>
              <div>
                <strong>Email</strong><small>{response?.capabilities.emailAddress || 'Adresse du compte'}</small>
              </div>
              <i>{draft.emailEnabled ? 'Actif' : 'Inactif'}</i>
            </label>
          </article>

          <article class:active={draft.discordEnabled}>
            <label class="channel-toggle">
              <input type="checkbox" bind:checked={draft.discordEnabled} />
              <span><wa-icon name="discord" family="brands"></wa-icon></span>
              <div><strong>Discord</strong><small>Webhook officiel discord.com</small></div>
              <i>{draft.discordEnabled ? 'Actif' : 'Inactif'}</i>
            </label>
            <div class="secret-row">
              <input
                type="password"
                bind:value={draft.discordWebhook}
                placeholder={policy.hasDiscordWebhook
                  ? 'Webhook configuré — laisser vide pour conserver'
                  : 'https://discord.com/api/webhooks/…'}
                autocomplete="off"
              />
              {#if policy.hasDiscordWebhook}
                <button type="button" onclick={clearDiscord}>Retirer</button>
              {/if}
            </div>
          </article>

          <article class:active={draft.slackEnabled}>
            <label class="channel-toggle">
              <input type="checkbox" bind:checked={draft.slackEnabled} />
              <span><wa-icon name="slack" family="brands"></wa-icon></span>
              <div><strong>Slack</strong><small>Webhook officiel hooks.slack.com</small></div>
              <i>{draft.slackEnabled ? 'Actif' : 'Inactif'}</i>
            </label>
            <div class="secret-row">
              <input
                type="password"
                bind:value={draft.slackWebhook}
                placeholder={policy.hasSlackWebhook
                  ? 'Webhook configuré — laisser vide pour conserver'
                  : 'https://hooks.slack.com/services/…'}
                autocomplete="off"
              />
              {#if policy.hasSlackWebhook}
                <button type="button" onclick={clearSlack}>Retirer</button>
              {/if}
            </div>
          </article>
        </div>
      </section>

      <section class="monitor-panel incidents-panel">
        <header class="monitor-panel__header">
          <div>
            <span class="monitor-kicker">Chronologie</span>
            <h3>Incidents récents</h3>
          </div>
          <span class="incident-count">{incidents.length} événement{incidents.length > 1 ? 's' : ''}</span>
        </header>

        {#if incidents.length}
          <div class="incident-list">
            {#each incidents as incident}
              <article class:incident-open={incident.status === 'open'}>
                <div class="incident-marker"><wa-icon name={incidentIcon(incident.type)}></wa-icon></div>
                <div class="incident-copy">
                  <div>
                    <strong>{incidentLabel(incident.type)}</strong>
                    <span class="incident-status incident-status--{incident.status}">
                      {incident.status === 'open' ? 'Ouvert' : 'Résolu'}
                    </span>
                  </div>
                  <p>{incident.message}</p>
                  <time datetime={incident.openedAt}>
                    Début {formatDate(incident.openedAt)}
                    {incident.resolvedAt ? ` · résolution ${formatDate(incident.resolvedAt)}` : ''}
                  </time>
                </div>
              </article>
            {/each}
          </div>
          {#if nextCursor}
            <button type="button" class="load-more" disabled={isLoadingMore} onclick={loadMoreIncidents}>
              {isLoadingMore ? 'Chargement…' : 'Afficher les incidents précédents'}
            </button>
          {/if}
        {:else}
          <div class="incidents-empty">
            <wa-icon name="shield-check"></wa-icon>
            <strong>Aucun incident enregistré</strong>
            <span>La chronologie apparaîtra ici dès qu’un seuil sera confirmé.</span>
          </div>
        {/if}
      </section>

      <footer class="monitor-actions">
        <button type="button" class="secondary-action" onclick={resetRecommended}>
          <wa-icon name="arrow-rotate-left"></wa-icon>
          Valeurs recommandées
        </button>
        <button type="submit" class="primary-action" disabled={isSaving}>
          <wa-icon name={isSaving ? 'rotate' : 'floppy-disk'}></wa-icon>
          {isSaving ? 'Enregistrement…' : 'Enregistrer la surveillance'}
        </button>
      </footer>
    </form>
  {/if}
</FeatureTab>

<style>
  .monitor-stack {
    --monitor-border: rgb(255 255 255 / 0.1);
    --monitor-muted: rgb(255 255 255 / 0.52);
    --monitor-surface: #101411;
    display: grid;
    gap: 1rem;
  }

  .monitor-loading {
    display: flex;
    min-height: 18rem;
    align-items: center;
    justify-content: center;
    gap: 0.7rem;
    border: 1px solid var(--monitor-border);
    border-radius: 0.8rem;
    background: #101210;
    color: rgb(255 255 255 / 0.65);
  }

  .monitor-loading wa-icon,
  wa-icon[name='rotate'] {
    animation: monitor-spin 850ms linear infinite;
  }

  .monitor-command {
    position: relative;
    display: grid;
    overflow: hidden;
    grid-template-columns: auto minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 1rem;
    border: 1px solid var(--monitor-border);
    border-radius: 0.85rem;
    background:
      linear-gradient(100deg, rgb(52 211 153 / 0.08), transparent 42%),
      repeating-linear-gradient(90deg, transparent 0 39px, rgb(255 255 255 / 0.018) 40px), #0b0f0c;
    padding: 1rem 1.1rem;
    box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.045);
  }

  .monitor-command::after {
    position: absolute;
    right: 0;
    bottom: 0;
    left: 0;
    height: 2px;
    background: #55e98d;
    content: '';
    opacity: 0.7;
  }

  .monitor-command--unhealthy::after {
    background: #fb7185;
  }

  .monitor-command--disabled::after,
  .monitor-command--paused::after {
    background: #8b9a90;
  }

  .monitor-command__signal {
    position: relative;
    display: grid;
    width: 3.3rem;
    height: 3.3rem;
    place-items: center;
    border: 1px solid rgb(85 233 141 / 0.32);
    border-radius: 50%;
    background: rgb(85 233 141 / 0.08);
    color: #6df1a0;
    font-size: 1.2rem;
  }

  .monitor-command__signal span {
    position: absolute;
    inset: -0.35rem;
    border: 1px solid rgb(85 233 141 / 0.2);
    border-radius: 50%;
    animation: monitor-pulse 2.2s ease-out infinite;
  }

  .monitor-command--unhealthy .monitor-command__signal {
    border-color: rgb(251 113 133 / 0.4);
    background: rgb(251 113 133 / 0.1);
    color: #fb7185;
  }

  .monitor-command__copy h3,
  .monitor-panel__header h3,
  .signal-card h3 {
    margin: 0;
    color: #f2f7f4;
    font-size: 1rem;
    font-weight: 850;
    letter-spacing: -0.01em;
  }

  .monitor-command__copy p,
  .signal-card p {
    margin: 0.18rem 0 0;
    color: var(--monitor-muted);
    font-size: 0.78rem;
  }

  .monitor-kicker,
  .metric-label {
    color: #71db98;
    font-size: 0.63rem;
    font-weight: 900;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .monitor-command__meta {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 0.4rem;
  }

  .monitor-command__meta span {
    border: 1px solid rgb(255 255 255 / 0.08);
    border-radius: 0.35rem;
    background: rgb(255 255 255 / 0.025);
    padding: 0.38rem 0.5rem;
    color: var(--monitor-muted);
    font-size: 0.66rem;
  }

  .monitor-command__meta strong {
    color: #e5eee8;
    font-variant-numeric: tabular-nums;
  }

  .master-switch,
  .compact-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    color: #e8f0eb;
    font-size: 0.76rem;
    font-weight: 800;
    cursor: pointer;
  }

  .master-switch input,
  .compact-toggle input,
  .channel-toggle input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }

  .master-switch__track,
  .compact-toggle > span {
    display: flex;
    width: 2.5rem;
    height: 1.35rem;
    align-items: center;
    border: 1px solid rgb(255 255 255 / 0.16);
    border-radius: 99px;
    background: #262d28;
    padding: 0.13rem;
    transition: 150ms ease;
  }

  .master-switch__track span,
  .compact-toggle > span::after {
    width: 0.92rem;
    height: 0.92rem;
    border-radius: 50%;
    background: #9aa49d;
    content: '';
    transition: 150ms ease;
  }

  .master-switch input:checked + .master-switch__track,
  .compact-toggle input:checked + span {
    border-color: rgb(85 233 141 / 0.5);
    background: rgb(39 168 88 / 0.32);
  }

  .master-switch input:checked + .master-switch__track span,
  .compact-toggle input:checked + span::after {
    transform: translateX(1.1rem);
    background: #6df1a0;
  }

  .monitor-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.65rem;
  }

  .monitor-metrics article {
    display: grid;
    min-height: 7.8rem;
    align-content: space-between;
    border: 1px solid var(--monitor-border);
    border-radius: 0.7rem;
    background: linear-gradient(145deg, #131814, #0e110f);
    padding: 0.85rem;
  }

  .monitor-metrics strong {
    color: #f3f7f4;
    font-size: clamp(1.3rem, 2.2vw, 2rem);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.05em;
  }

  .monitor-metrics strong i {
    color: var(--monitor-muted);
    font-size: 0.78rem;
    font-style: normal;
    letter-spacing: 0;
  }

  .monitor-metrics .metric-date {
    font-size: 0.86rem;
    letter-spacing: 0;
    line-height: 1.35;
  }

  .monitor-metrics small {
    color: var(--monitor-muted);
    font-size: 0.69rem;
  }

  .monitor-panel,
  .signal-card {
    border: 1px solid var(--monitor-border);
    border-radius: 0.78rem;
    background: var(--monitor-surface);
    box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.035);
  }

  .monitor-panel__header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    border-bottom: 1px solid var(--monitor-border);
    padding: 0.85rem 1rem;
  }

  .range-selector {
    display: inline-flex;
    gap: 0.16rem;
    border: 1px solid var(--monitor-border);
    border-radius: 0.45rem;
    background: #090c0a;
    padding: 0.18rem;
  }

  .range-selector button,
  .secondary-action,
  .primary-action,
  .load-more {
    border: 0;
    border-radius: 0.34rem;
    font: inherit;
    font-size: 0.72rem;
    font-weight: 850;
    cursor: pointer;
  }

  .range-selector button {
    background: transparent;
    padding: 0.38rem 0.56rem;
    color: var(--monitor-muted);
  }

  .range-selector button.active {
    background: #253329;
    color: #80efa8;
    box-shadow: inset 0 0 0 1px rgb(85 233 141 / 0.18);
  }

  .monitor-legend {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.75rem 1rem 0;
    color: var(--monitor-muted);
    font-size: 0.68rem;
  }

  .monitor-legend span {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }

  .legend-line {
    width: 1.2rem;
    height: 2px;
    background: #55e98d;
  }

  .legend-line--latency {
    background: #7c9dff;
  }

  .chart-refreshing {
    margin-left: auto;
  }

  .monitor-chart-scroll {
    overflow-x: auto;
    padding: 0 0.5rem 0.5rem;
  }

  .monitor-chart {
    display: block;
    width: 100%;
    min-width: 38rem;
  }

  .chart-grid {
    stroke: rgb(255 255 255 / 0.075);
    stroke-dasharray: 3 5;
  }

  .chart-line {
    fill: none;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-width: 2.2;
    vector-effect: non-scaling-stroke;
  }

  .chart-line--availability {
    stroke: url('#availabilityGlow');
  }

  .chart-line--latency {
    stroke: #7c9dff;
    stroke-width: 1.5;
    opacity: 0.78;
  }

  .chart-y-label,
  .chart-x-label {
    fill: rgb(255 255 255 / 0.4);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 9px;
  }

  .monitor-empty-chart,
  .incidents-empty {
    display: grid;
    min-height: 12rem;
    place-items: center;
    align-content: center;
    gap: 0.35rem;
    padding: 1rem;
    color: var(--monitor-muted);
    text-align: center;
  }

  .monitor-empty-chart wa-icon,
  .incidents-empty wa-icon {
    color: #56df8a;
    font-size: 1.5rem;
  }

  .monitor-empty-chart strong,
  .incidents-empty strong {
    color: #e7eee9;
    font-size: 0.82rem;
  }

  .monitor-empty-chart span,
  .incidents-empty span {
    font-size: 0.72rem;
  }

  .monitor-config-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem;
  }

  .signal-card {
    display: grid;
    align-content: start;
    gap: 0.75rem;
    padding: 0.9rem;
  }

  .signal-card header {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.65rem;
  }

  .signal-icon {
    display: grid;
    width: 2.25rem;
    height: 2.25rem;
    place-items: center;
    border: 1px solid rgb(85 233 141 / 0.2);
    border-radius: 0.45rem;
    background: rgb(85 233 141 / 0.065);
    color: #67e99a;
  }

  .field-block {
    display: grid;
    gap: 0.3rem;
    color: rgb(255 255 255 / 0.68);
    font-size: 0.69rem;
    font-weight: 800;
  }

  .field-block input,
  .secret-row input {
    width: 100%;
    min-width: 0;
    border: 1px solid rgb(255 255 255 / 0.12);
    border-radius: 0.42rem;
    outline: none;
    background: #090c0a;
    padding: 0.62rem 0.68rem;
    color: #eff5f1;
    font: inherit;
    font-size: 0.78rem;
    transition: 130ms ease;
  }

  .field-block input:focus,
  .secret-row input:focus {
    border-color: rgb(85 233 141 / 0.55);
    box-shadow: 0 0 0 3px rgb(85 233 141 / 0.1);
  }

  .field-block--number {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
  }

  .field-block--number > span {
    grid-column: 1 / -1;
  }

  .field-block--number small {
    padding-bottom: 0.62rem;
    color: var(--monitor-muted);
    font-weight: 650;
  }

  .target-preview {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: 0.4rem;
    border-left: 2px solid #55e98d;
    background: rgb(255 255 255 / 0.025);
    padding: 0.5rem 0.58rem;
    color: var(--monitor-muted);
  }

  .target-preview code {
    overflow: hidden;
    font-size: 0.68rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .threshold-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.65rem;
  }

  .signal-reading,
  .backup-rule {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    border-top: 1px solid var(--monitor-border);
    padding-top: 0.7rem;
    color: var(--monitor-muted);
    font-size: 0.7rem;
  }

  .signal-reading strong,
  .backup-rule strong {
    color: #e8f0eb;
    font-variant-numeric: tabular-nums;
  }

  .backup-rule {
    justify-content: flex-start;
    margin-top: auto;
  }

  .backup-rule strong {
    margin-left: auto;
    color: #6bea9c;
  }

  .channels-panel,
  .incidents-panel {
    overflow: hidden;
  }

  .secondary-action,
  .primary-action,
  .load-more {
    display: inline-flex;
    min-height: 2.35rem;
    align-items: center;
    justify-content: center;
    gap: 0.45rem;
    padding: 0.5rem 0.72rem;
  }

  .secondary-action,
  .load-more {
    border: 1px solid rgb(255 255 255 / 0.12);
    background: #171c18;
    color: #dbe6df;
  }

  .primary-action {
    background: #2fbd66;
    color: #06120a;
    box-shadow: 0 8px 22px rgb(47 189 102 / 0.16);
  }

  .secondary-action:disabled,
  .primary-action:disabled,
  .load-more:disabled {
    cursor: wait;
    opacity: 0.55;
  }

  .channel-list {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.65rem;
    padding: 0.8rem;
  }

  .channel-list article {
    display: grid;
    gap: 0.6rem;
    border: 1px solid var(--monitor-border);
    border-radius: 0.6rem;
    background: #0b0e0c;
    padding: 0.7rem;
  }

  .channel-list article.active {
    border-color: rgb(85 233 141 / 0.26);
    background: rgb(85 233 141 / 0.035);
  }

  .channel-toggle {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.55rem;
    cursor: pointer;
  }

  .channel-toggle > span {
    display: grid;
    width: 2rem;
    height: 2rem;
    place-items: center;
    border-radius: 0.4rem;
    background: #1a211c;
    color: #8aa095;
  }

  .channel-list article.active .channel-toggle > span {
    background: rgb(85 233 141 / 0.12);
    color: #6bea9c;
  }

  .channel-toggle div {
    display: grid;
    min-width: 0;
  }

  .channel-toggle strong {
    color: #ebf2ed;
    font-size: 0.77rem;
  }

  .channel-toggle small {
    overflow: hidden;
    color: var(--monitor-muted);
    font-size: 0.62rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .channel-toggle i {
    color: var(--monitor-muted);
    font-size: 0.61rem;
    font-style: normal;
    font-weight: 850;
    text-transform: uppercase;
  }

  .channel-list article.active .channel-toggle i {
    color: #65e895;
  }

  .secret-row {
    display: flex;
    gap: 0.35rem;
  }

  .secret-row button {
    border: 1px solid rgb(251 113 133 / 0.22);
    border-radius: 0.4rem;
    background: rgb(251 113 133 / 0.07);
    color: #fda4af;
    font: inherit;
    font-size: 0.66rem;
    cursor: pointer;
  }

  .incident-count {
    color: var(--monitor-muted);
    font-size: 0.68rem;
  }

  .incident-list {
    display: grid;
  }

  .incident-list article {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 0.75rem;
    border-bottom: 1px solid var(--monitor-border);
    padding: 0.78rem 1rem;
  }

  .incident-list article:last-child {
    border-bottom: 0;
  }

  .incident-list article.incident-open {
    background: linear-gradient(90deg, rgb(251 113 133 / 0.055), transparent 45%);
  }

  .incident-marker {
    display: grid;
    width: 2rem;
    height: 2rem;
    place-items: center;
    border: 1px solid rgb(255 255 255 / 0.1);
    border-radius: 50%;
    background: #171d18;
    color: #7beaa3;
  }

  .incident-open .incident-marker {
    border-color: rgb(251 113 133 / 0.3);
    background: rgb(251 113 133 / 0.09);
    color: #fb8b9e;
  }

  .incident-copy > div {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.45rem;
  }

  .incident-copy strong {
    color: #eaf1ec;
    font-size: 0.76rem;
  }

  .incident-copy p {
    margin: 0.18rem 0;
    color: rgb(255 255 255 / 0.68);
    font-size: 0.72rem;
  }

  .incident-copy time {
    color: var(--monitor-muted);
    font-size: 0.62rem;
  }

  .incident-status {
    border-radius: 99px;
    padding: 0.16rem 0.38rem;
    font-size: 0.57rem;
    font-weight: 900;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .incident-status--open {
    background: rgb(251 113 133 / 0.12);
    color: #fda4af;
  }

  .incident-status--resolved {
    background: rgb(85 233 141 / 0.1);
    color: #76e9a0;
  }

  .load-more {
    display: flex;
    margin: 0.8rem auto;
  }

  .monitor-actions {
    position: sticky;
    z-index: 5;
    bottom: 0.5rem;
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    border: 1px solid rgb(255 255 255 / 0.12);
    border-radius: 0.7rem;
    background: rgb(10 13 11 / 0.92);
    padding: 0.65rem;
    box-shadow: 0 16px 40px rgb(0 0 0 / 0.3);
    backdrop-filter: blur(12px);
  }

  @keyframes monitor-spin {
    to {
      transform: rotate(360deg);
    }
  }

  @keyframes monitor-pulse {
    0% {
      transform: scale(0.82);
      opacity: 0.8;
    }
    75%,
    100% {
      transform: scale(1.2);
      opacity: 0;
    }
  }

  @media (max-width: 980px) {
    .monitor-command {
      grid-template-columns: auto minmax(0, 1fr) auto;
    }

    .monitor-command__meta {
      grid-column: 1 / -1;
      justify-content: flex-start;
    }

    .monitor-metrics,
    .channel-list {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .channel-list article:last-child {
      grid-column: 1 / -1;
    }
  }

  @media (max-width: 720px) {
    .monitor-command,
    .monitor-config-grid,
    .monitor-metrics,
    .channel-list {
      grid-template-columns: 1fr;
    }

    .monitor-command__signal {
      display: none;
    }

    .monitor-command__meta,
    .channel-list article:last-child {
      grid-column: auto;
    }

    .master-switch {
      position: absolute;
      top: 1rem;
      right: 1rem;
    }

    .monitor-panel__header,
    .monitor-actions {
      align-items: stretch;
    }

    .monitor-panel__header,
    .monitor-actions,
    .threshold-row {
      flex-direction: column;
      grid-template-columns: 1fr;
    }

    .monitor-actions .secondary-action,
    .monitor-actions .primary-action {
      width: 100%;
    }
  }
</style>
