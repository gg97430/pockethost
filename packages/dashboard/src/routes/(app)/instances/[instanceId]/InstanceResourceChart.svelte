<script lang="ts">
  import type { DashboardInstanceMetric } from '$src/pocketbase-client'
  import { client } from '$src/pocketbase-client'
  import { onDestroy, onMount } from 'svelte'
  import {
    INSTANCE_METRIC_HISTORY_WINDOW_MS,
    INSTANCE_METRIC_POLL_INTERVAL_MS,
    appendMetricHistory,
    parseMetricHistory,
    toMetricHistoryPoint,
    type InstanceMetricHistoryPoint,
  } from './instanceMetricHistory'

  export let instanceId: string
  export let initialMetric: DashboardInstanceMetric | undefined
  export let initialCollectedAt: string | undefined
  export let onMetric: ((metric: DashboardInstanceMetric, collectedAt: string) => void) | undefined

  type MetricKey = 'cpuPercent' | 'memoryPercent'

  const chart = { left: 42, top: 16, width: 662, height: 166 }
  const nf = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 })
  const timeFormatter = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' })
  const preciseTimeFormatter = new Intl.DateTimeFormat('fr-FR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  let history: InstanceMetricHistoryPoint[] = []
  let now = Date.now()
  let isFetching = false
  let isPaused = false
  let fetchError = false
  let seededCollectedAt = ''
  let metricsTimer: ReturnType<typeof setInterval> | undefined

  $: storageKey = `pockethost:instance-metrics:${instanceId}`
  $: latest = history.at(-1)
  $: cpuPath = buildPath(history, now, 'cpuPercent')
  $: memoryPath = buildPath(history, now, 'memoryPercent')
  $: axisTimes = [
    now - INSTANCE_METRIC_HISTORY_WINDOW_MS,
    now - (INSTANCE_METRIC_HISTORY_WINDOW_MS * 2) / 3,
    now - INSTANCE_METRIC_HISTORY_WINDOW_MS / 3,
    now,
  ]

  $: if (initialMetric && initialCollectedAt && seededCollectedAt !== initialCollectedAt) {
    seededCollectedAt = initialCollectedAt
    addMetric(initialMetric, initialCollectedAt)
  }

  const clampPercent = (value: number | null | undefined) => {
    if (value === null || value === undefined || !Number.isFinite(value)) return null
    return Math.max(0, Math.min(100, value))
  }

  const xFor = (timestamp: number, referenceNow: number) => {
    const start = referenceNow - INSTANCE_METRIC_HISTORY_WINDOW_MS
    return chart.left + ((timestamp - start) / INSTANCE_METRIC_HISTORY_WINDOW_MS) * chart.width
  }

  const yFor = (value: number) => chart.top + chart.height - (value / 100) * chart.height

  function buildPath(points: InstanceMetricHistoryPoint[], referenceNow: number, key: MetricKey) {
    let path = ''
    let hasOpenSegment = false

    for (const point of points) {
      const value = clampPercent(point[key])
      if (value === null) {
        hasOpenSegment = false
        continue
      }

      const command = hasOpenSegment ? 'L' : 'M'
      path += `${command}${xFor(point.timestamp, referenceNow).toFixed(2)},${yFor(value).toFixed(2)} `
      hasOpenSegment = true
    }

    return path.trim()
  }

  const formatPercent = (value: number | null | undefined) => {
    const percent = clampPercent(value)
    return percent === null ? 'Indispo.' : `${nf.format(percent)} %`
  }

  const formatBytes = (bytes: number | null | undefined) => {
    if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return 'Indispo.'
    if (bytes <= 0) return '0 o'
    const units = ['o', 'Ko', 'Mo', 'Go', 'To']
    let value = bytes
    let unit = 0
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024
      unit += 1
    }
    return `${nf.format(value)} ${units[unit]}`
  }

  const persistHistory = () => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(history))
    } catch {
      // The graph remains usable when browser storage is unavailable.
    }
  }

  function addMetric(metric: DashboardInstanceMetric, collectedAt: string) {
    const point = toMetricHistoryPoint(metric, collectedAt)
    now = Math.max(Date.now(), point.timestamp)
    history = appendMetricHistory(history, point, now)
    if (typeof sessionStorage !== 'undefined') persistHistory()
  }

  const addUnavailablePoint = () => {
    now = Date.now()
    history = appendMetricHistory(
      history,
      {
        timestamp: now,
        cpuPercent: null,
        memoryBytes: null,
        memoryLimitBytes: null,
        memoryPercent: null,
      },
      now
    )
    persistHistory()
  }

  const refreshMetrics = async () => {
    if (isFetching || (typeof document !== 'undefined' && document.hidden)) return

    isFetching = true
    try {
      const response = await client().getInstanceMetrics(instanceId)
      fetchError = false
      addMetric(response.metric, response.collectedAt)
      onMetric?.(response.metric, response.collectedAt)
    } catch {
      fetchError = true
      addUnavailablePoint()
    } finally {
      isFetching = false
    }
  }

  const handleVisibilityChange = () => {
    isPaused = document.hidden
    if (!isPaused) void refreshMetrics()
  }

  onMount(() => {
    history = parseMetricHistory(sessionStorage.getItem(storageKey))
    if (initialMetric && initialCollectedAt) addMetric(initialMetric, initialCollectedAt)
    isPaused = document.hidden
    document.addEventListener('visibilitychange', handleVisibilityChange)
    void refreshMetrics()
    metricsTimer = setInterval(refreshMetrics, INSTANCE_METRIC_POLL_INTERVAL_MS)
  })

  onDestroy(() => {
    if (metricsTimer) clearInterval(metricsTimer)
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', handleVisibilityChange)
  })
</script>

<section class="resource-chart-panel" aria-labelledby="resource-chart-title">
  <div class="resource-chart-head">
    <div>
      <span class="resource-chart-eyebrow">Télémétrie conteneur</span>
      <h3 id="resource-chart-title">Ressources en direct</h3>
    </div>

    <div
      class="resource-chart-status"
      class:resource-chart-status--paused={isPaused}
      class:resource-chart-status--error={fetchError}
    >
      <span class="resource-chart-pulse"></span>
      {#if isPaused}
        En pause
      {:else if fetchError}
        Mesure indisponible
      {:else if latest}
        Actualisé à {preciseTimeFormatter.format(latest.timestamp)}
      {:else}
        Connexion…
      {/if}
    </div>
  </div>

  <div class="resource-chart-summary" aria-label="Dernières mesures">
    <div class="resource-chart-metric resource-chart-metric--cpu">
      <span><i></i> CPU</span>
      <strong>{formatPercent(latest?.cpuPercent)}</strong>
    </div>
    <div class="resource-chart-metric resource-chart-metric--memory">
      <span><i></i> RAM</span>
      <strong>{formatBytes(latest?.memoryBytes)}</strong>
      <small>
        {formatPercent(latest?.memoryPercent)}{latest?.memoryLimitBytes
          ? ` · limite ${formatBytes(latest.memoryLimitBytes)}`
          : ''}
      </small>
    </div>
    <div class="resource-chart-window">
      <span>Fenêtre</span>
      <strong>30 min</strong>
      <small>Mesure toutes les 30 s</small>
    </div>
  </div>

  <div class="resource-chart-canvas">
    <svg viewBox="0 0 720 220" role="img" aria-labelledby="resource-chart-svg-title resource-chart-svg-desc">
      <title id="resource-chart-svg-title">Historique CPU et RAM des 30 dernières minutes</title>
      <desc id="resource-chart-svg-desc">
        CPU à {formatPercent(latest?.cpuPercent)} et mémoire à {formatPercent(latest?.memoryPercent)}.
      </desc>

      <defs>
        <linearGradient id="resource-chart-glow" x1="0" x2="1">
          <stop offset="0" stop-color="#22c55e" stop-opacity="0.05" />
          <stop offset="0.72" stop-color="#22c55e" stop-opacity="0.28" />
          <stop offset="1" stop-color="#38bdf8" stop-opacity="0.12" />
        </linearGradient>
        <filter id="resource-chart-line-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="2.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <rect
        x={chart.left}
        y={chart.top}
        width={chart.width}
        height={chart.height}
        rx="8"
        fill="url(#resource-chart-glow)"
      />

      {#each [100, 75, 50, 25, 0] as tick}
        <line
          x1={chart.left}
          x2={chart.left + chart.width}
          y1={yFor(tick)}
          y2={yFor(tick)}
          class="resource-chart-gridline"
        />
        <text x={chart.left - 8} y={yFor(tick) + 4} class="resource-chart-y-label">{tick}%</text>
      {/each}

      {#each axisTimes as timestamp}
        <line
          x1={xFor(timestamp, now)}
          x2={xFor(timestamp, now)}
          y1={chart.top}
          y2={chart.top + chart.height}
          class="resource-chart-gridline resource-chart-gridline--vertical"
        />
        <text x={xFor(timestamp, now)} y={chart.top + chart.height + 25} class="resource-chart-x-label">
          {timeFormatter.format(timestamp)}
        </text>
      {/each}

      {#if memoryPath}
        <path d={memoryPath} class="resource-chart-line resource-chart-line--memory" />
      {/if}
      {#if cpuPath}
        <path
          d={cpuPath}
          class="resource-chart-line resource-chart-line--cpu"
          filter="url(#resource-chart-line-glow)"
        />
      {/if}

      {#each history as point (point.timestamp)}
        {#if clampPercent(point.cpuPercent) !== null}
          <circle
            cx={xFor(point.timestamp, now)}
            cy={yFor(clampPercent(point.cpuPercent) ?? 0)}
            r="2.4"
            class="resource-chart-point resource-chart-point--cpu"
          >
            <title>{timeFormatter.format(point.timestamp)} · CPU {formatPercent(point.cpuPercent)}</title>
          </circle>
        {/if}
        {#if clampPercent(point.memoryPercent) !== null}
          <circle
            cx={xFor(point.timestamp, now)}
            cy={yFor(clampPercent(point.memoryPercent) ?? 0)}
            r="2.1"
            class="resource-chart-point resource-chart-point--memory"
          >
            <title>{timeFormatter.format(point.timestamp)} · RAM {formatPercent(point.memoryPercent)}</title>
          </circle>
        {/if}
      {/each}
    </svg>

    {#if history.length === 0}
      <div class="resource-chart-empty">Première mesure en cours…</div>
    {/if}
  </div>
</section>

<style>
  .resource-chart-panel {
    position: relative;
    margin-top: 0.875rem;
    overflow: hidden;
    border: 1px solid var(--app-border);
    border-radius: 0.875rem;
    background:
      radial-gradient(circle at 76% 0%, rgb(56 189 248 / 0.1), transparent 30%),
      linear-gradient(145deg, rgb(30 184 84 / 0.07), transparent 36%), var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .resource-chart-panel::before {
    position: absolute;
    inset: 0;
    background-image: linear-gradient(rgb(148 163 184 / 0.025) 1px, transparent 1px);
    background-size: 100% 4px;
    content: '';
    pointer-events: none;
  }

  .resource-chart-head,
  .resource-chart-summary,
  .resource-chart-canvas {
    position: relative;
  }

  .resource-chart-head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
  }

  .resource-chart-eyebrow {
    display: block;
    color: var(--app-text-muted);
    font-size: 0.68rem;
    font-weight: 900;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .resource-chart-head h3 {
    margin: 0.2rem 0 0;
    color: var(--app-text-strong);
    font-size: 1.15rem;
    font-weight: 900;
  }

  .resource-chart-status {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    min-height: 1.9rem;
    border: 1px solid rgb(34 197 94 / 0.22);
    border-radius: 999px;
    background: rgb(34 197 94 / 0.08);
    padding: 0.35rem 0.65rem;
    color: var(--app-text-muted);
    font-size: 0.72rem;
    font-weight: 800;
  }

  .resource-chart-pulse {
    width: 0.45rem;
    height: 0.45rem;
    border-radius: 50%;
    background: #22c55e;
    box-shadow: 0 0 0 0 rgb(34 197 94 / 0.55);
    animation: resource-pulse 2s infinite;
  }

  .resource-chart-status--paused,
  .resource-chart-status--error {
    border-color: rgb(245 158 11 / 0.25);
    background: rgb(245 158 11 / 0.08);
  }

  .resource-chart-status--paused .resource-chart-pulse,
  .resource-chart-status--error .resource-chart-pulse {
    background: #f59e0b;
    animation: none;
  }

  .resource-chart-summary {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.625rem;
    margin-top: 1rem;
  }

  .resource-chart-metric,
  .resource-chart-window {
    min-width: 0;
    border-left: 2px solid rgb(148 163 184 / 0.2);
    padding: 0.2rem 0.75rem;
  }

  .resource-chart-metric--cpu {
    border-color: #22c55e;
  }

  .resource-chart-metric--memory {
    border-color: #38bdf8;
  }

  .resource-chart-metric span,
  .resource-chart-window span {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--app-text-muted);
    font-size: 0.67rem;
    font-weight: 900;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .resource-chart-metric i {
    display: inline-block;
    width: 1rem;
    height: 0.16rem;
    border-radius: 999px;
    background: currentColor;
  }

  .resource-chart-metric--cpu span {
    color: #22c55e;
  }

  .resource-chart-metric--memory span {
    color: #38bdf8;
  }

  .resource-chart-metric strong,
  .resource-chart-window strong {
    display: block;
    margin-top: 0.28rem;
    color: var(--app-text-strong);
    font-size: 1.2rem;
    font-weight: 900;
    line-height: 1.1;
  }

  .resource-chart-metric small,
  .resource-chart-window small {
    display: block;
    margin-top: 0.25rem;
    color: var(--app-text-muted);
    font-size: 0.72rem;
  }

  .resource-chart-canvas {
    min-height: 14rem;
    margin-top: 0.75rem;
  }

  .resource-chart-canvas svg {
    display: block;
    width: 100%;
    min-width: 36rem;
  }

  .resource-chart-canvas {
    overflow-x: auto;
    scrollbar-color: rgb(148 163 184 / 0.25) transparent;
    scrollbar-width: thin;
  }

  :global(.resource-chart-gridline) {
    stroke: rgb(148 163 184 / 0.13);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }

  :global(.resource-chart-gridline--vertical) {
    stroke-dasharray: 3 6;
  }

  :global(.resource-chart-y-label),
  :global(.resource-chart-x-label) {
    fill: var(--app-text-faint);
    font-size: 10px;
    font-weight: 700;
  }

  :global(.resource-chart-y-label) {
    text-anchor: end;
  }

  :global(.resource-chart-x-label) {
    text-anchor: middle;
  }

  :global(.resource-chart-line) {
    fill: none;
    stroke-linecap: round;
    stroke-linejoin: round;
    vector-effect: non-scaling-stroke;
  }

  :global(.resource-chart-line--cpu) {
    stroke: #22c55e;
    stroke-width: 2.4;
  }

  :global(.resource-chart-line--memory) {
    stroke: #38bdf8;
    stroke-width: 2;
  }

  :global(.resource-chart-point) {
    vector-effect: non-scaling-stroke;
  }

  :global(.resource-chart-point--cpu) {
    fill: #22c55e;
    stroke: var(--app-surface);
    stroke-width: 1;
  }

  :global(.resource-chart-point--memory) {
    fill: #38bdf8;
    stroke: var(--app-surface);
    stroke-width: 1;
  }

  .resource-chart-empty {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: var(--app-text-muted);
    font-size: 0.82rem;
    font-weight: 750;
    pointer-events: none;
  }

  @keyframes resource-pulse {
    70% {
      box-shadow: 0 0 0 0.35rem rgb(34 197 94 / 0);
    }
    100% {
      box-shadow: 0 0 0 0 rgb(34 197 94 / 0);
    }
  }

  @media (max-width: 42rem) {
    .resource-chart-panel {
      padding: 0.875rem;
    }

    .resource-chart-summary {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .resource-chart-window {
      grid-column: 1 / -1;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .resource-chart-pulse {
      animation: none;
    }
  }
</style>
