<script lang="ts">
  import type { DashboardInstanceMetric } from '$src/pocketbase-client'

  export let metrics: DashboardInstanceMetric | undefined
  export let variant: 'table' | 'card' = 'table'

  const clampPercent = (value: number | null | undefined) => {
    if (value === null || value === undefined || !Number.isFinite(value)) return 0
    return Math.max(0, Math.min(100, value))
  }

  const formatPercent = (value: number | null | undefined) => {
    if (value === null || value === undefined || !Number.isFinite(value)) return '-'
    return `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 }).format(clampPercent(value))} %`
  }

  const formatBytes = (bytes: number | null | undefined) => {
    if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '-'
    if (bytes <= 0) return '0 o'
    const units = ['o', 'Ko', 'Mo', 'Go', 'To']
    let value = bytes
    let unit = 0
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024
      unit += 1
    }
    return `${new Intl.NumberFormat('fr-FR', {
      maximumFractionDigits: value >= 10 || unit === 0 ? 0 : 1,
    }).format(value)} ${units[unit]}`
  }

  $: cpuPercent = clampPercent(metrics?.cpuPercent)
  $: memoryPercent = clampPercent(metrics?.memoryPercent)
  $: memoryLabel =
    metrics?.memoryBytes !== null && metrics?.memoryBytes !== undefined && metrics?.memoryLimitBytes
      ? `${formatBytes(metrics.memoryBytes)} / ${formatBytes(metrics.memoryLimitBytes)}`
      : formatPercent(metrics?.memoryPercent)
  $: diskLabel = formatBytes(metrics?.diskBytes)
</script>

<div class="resource-meters resource-meters--{variant}" aria-label="Ressources instance">
  <div class="resource-meter">
    <div class="resource-meter-head">
      <span>CPU</span>
      <strong>{formatPercent(metrics?.cpuPercent)}</strong>
    </div>
    <div class="resource-track">
      <span class="resource-fill resource-fill--cpu" style={`width: ${cpuPercent}%`}></span>
    </div>
  </div>

  <div class="resource-meter">
    <div class="resource-meter-head">
      <span>RAM</span>
      <strong>{memoryLabel}</strong>
    </div>
    <div class="resource-track">
      <span class="resource-fill resource-fill--ram" style={`width: ${memoryPercent}%`}></span>
    </div>
  </div>

  <div class="resource-meter resource-meter--disk">
    <div class="resource-meter-head">
      <span>Disque</span>
      <strong>{diskLabel}</strong>
    </div>
  </div>
</div>

<style>
  .resource-meters {
    display: grid;
    min-width: 0;
    gap: 0.45rem;
  }

  .resource-meters--table {
    width: 100%;
    grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.35fr) minmax(0, 0.9fr);
    align-items: center;
    min-width: 0;
  }

  .resource-meters--card {
    width: min(100%, 30rem);
    grid-template-columns: repeat(3, minmax(0, 1fr));
    margin-top: 0.15rem;
  }

  .resource-meter {
    min-width: 0;
  }

  .resource-meter-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.45rem;
    min-width: 0;
    color: var(--app-text-muted);
    font-size: 0.68rem;
    font-weight: 800;
    text-transform: uppercase;
  }

  .resource-meter-head strong {
    overflow: hidden;
    color: var(--app-text-strong);
    font-size: 0.74rem;
    font-weight: 850;
    text-align: right;
    text-overflow: ellipsis;
    text-transform: none;
    white-space: nowrap;
  }

  .resource-track {
    height: 0.28rem;
    margin-top: 0.28rem;
    overflow: hidden;
    border-radius: 999px;
    background: var(--app-meter-bg, rgb(148 163 184 / 0.18));
  }

  .resource-fill {
    display: block;
    height: 100%;
    border-radius: inherit;
  }

  .resource-fill--cpu {
    background: linear-gradient(90deg, #22c55e, #eab308, #f97316);
  }

  .resource-fill--ram {
    background: linear-gradient(90deg, #38bdf8, #6366f1);
  }

  .resource-meter--disk {
    align-self: end;
    padding-bottom: 0.05rem;
  }

  @media (max-width: 48rem) {
    .resource-meters--table,
    .resource-meters--card {
      grid-template-columns: 1fr;
      min-width: 0;
    }
  }
</style>
