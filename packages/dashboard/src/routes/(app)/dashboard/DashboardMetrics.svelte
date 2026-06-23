<script lang="ts">
  import { globalInstancesStore, userStore } from '$util/stores'
  import {
    getInstanceRuntimeState,
    runtimeStateLabel,
    type InstanceRuntimeState,
  } from '$util/instancePower'
  import type { InstanceFields } from 'pockethost/common'
  import { onDestroy, onMount } from 'svelte'

  type SystemMetrics = {
    collectedAt: string
    hostname: string
    uptimeSeconds: number
    cpu: {
      count: number
      loadAverage: number[]
    }
    memory: {
      totalBytes: number
      freeBytes: number
      usedBytes: number
      usedPercent: number
    }
    disk: {
      path: string
      totalBytes: number
      freeBytes: number
      usedBytes: number
      usedPercent: number
    }
    process: {
      uptimeSeconds: number
      rssBytes: number
      heapUsedBytes: number
      heapTotalBytes: number
    }
  }

  type StatusItem = {
    key: InstanceRuntimeState
    label: string
    value: number
    percent: number
    className: string
  }

  const statusOrder: Array<{ key: InstanceRuntimeState; className: string }> = [
    { key: 'running', className: 'metric-bar-fill--green' },
    { key: 'starting', className: 'metric-bar-fill--amber' },
    { key: 'vacuuming', className: 'metric-bar-fill--blue' },
    { key: 'sleeping', className: 'metric-bar-fill--slate' },
    { key: 'off', className: 'metric-bar-fill--muted' },
    { key: 'failed', className: 'metric-bar-fill--red' },
  ]

  let systemMetrics: SystemMetrics | null = null
  let systemMetricsError = ''
  let refreshTimer: ReturnType<typeof setInterval> | undefined

  const clampPercent = (value: number) => Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0))

  const formatNumber = (value: number) => new Intl.NumberFormat('fr-FR').format(value)

  const formatPercent = (value: number) =>
    `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 }).format(clampPercent(value))} %`

  const formatBytes = (bytes: number) => {
    if (!Number.isFinite(bytes) || bytes < 0) return '0 o'
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

  const formatDuration = (seconds: number) => {
    if (!Number.isFinite(seconds) || seconds < 0) return '0 min'
    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    if (days > 0) return `${days} j ${hours} h`
    if (hours > 0) return `${hours} h ${minutes} min`
    return `${minutes} min`
  }

  const percentOf = (value: number, total: number) => (total > 0 ? clampPercent((value / total) * 100) : 0)

  const fetchSystemMetrics = async () => {
    try {
      const response = await fetch('/api/internal/system-metrics', { cache: 'no-store' })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      systemMetrics = await response.json()
      systemMetricsError = ''
    } catch (error) {
      systemMetrics = null
      systemMetricsError = error instanceof Error ? error.message : String(error)
    }
  }

  onMount(() => {
    void fetchSystemMetrics()
    refreshTimer = setInterval(fetchSystemMetrics, 30_000)
  })

  onDestroy(() => {
    if (refreshTimer) clearInterval(refreshTimer)
  })

  $: instances = Object.values($globalInstancesStore)
  $: totalInstances = instances.length
  $: quota = $userStore?.subscription_quantity ?? 0
  $: quotaRemaining = Math.max(0, quota - totalInstances)
  $: quotaPercent = quota > 0 ? percentOf(totalInstances, quota) : 0
  $: statusCounts = instances.reduce(
    (counts, instance) => {
      const state = getInstanceRuntimeState(instance)
      counts[state] = (counts[state] || 0) + 1
      return counts
    },
    {} as Record<InstanceRuntimeState, number>
  )
  $: runningCount = statusCounts.running || 0
  $: activeCount = runningCount + (statusCounts.starting || 0) + (statusCounts.vacuuming || 0)
  $: failedCount = statusCounts.failed || 0
  $: customDomainCount = instances.filter((instance) => !!instance.cname).length
  $: devCount = instances.filter((instance) => !!instance.dev).length
  $: adminSyncCount = instances.filter((instance) => !!instance.syncAdmin).length
  $: autoVacuumCount = instances.filter((instance) => instance.autoVacuum !== false).length
  $: statusItems = statusOrder.map(
    ({ key, className }): StatusItem => ({
      key,
      className,
      label: runtimeStateLabel[key],
      value: statusCounts[key] || 0,
      percent: percentOf(statusCounts[key] || 0, totalInstances),
    })
  )
  $: versionItems = Object.entries(
    instances.reduce(
      (acc, instance: InstanceFields) => {
        const version = instance.version || 'inconnue'
        acc[version] = (acc[version] || 0) + 1
        return acc
      },
      {} as Record<string, number>
    )
  )
    .map(([version, count]) => ({ version, count, percent: percentOf(count, totalInstances) }))
    .sort((a, b) => b.count - a.count || b.version.localeCompare(a.version))
    .slice(0, 5)
</script>

<section class="dashboard-metrics" aria-label="Indicateurs du tableau de bord">
  <div class="kpi-grid">
    <div class="kpi-card kpi-card--primary">
      <span class="kpi-label">Instances</span>
      <strong>{formatNumber(totalInstances)}</strong>
      <small>{quota > 0 ? `${formatNumber(quotaRemaining)} disponible${quotaRemaining > 1 ? 's' : ''}` : 'Quota non configuré'}</small>
    </div>
    <div class="kpi-card">
      <span class="kpi-label">Actives</span>
      <strong>{formatNumber(activeCount)}</strong>
      <small>{formatNumber(runningCount)} en service</small>
    </div>
    <div class="kpi-card">
      <span class="kpi-label">RAM libre</span>
      <strong>{systemMetrics ? formatBytes(systemMetrics.memory.freeBytes) : '...'}</strong>
      <small>{systemMetrics ? `${formatPercent(systemMetrics.memory.usedPercent)} utilisée` : 'Métrique indisponible'}</small>
    </div>
    <div class="kpi-card" class:kpi-card--danger={failedCount > 0}>
      <span class="kpi-label">Incidents</span>
      <strong>{formatNumber(failedCount)}</strong>
      <small>{failedCount > 0 ? 'À vérifier' : 'Aucun échec'}</small>
    </div>
  </div>

  <div class="metric-panel-grid">
    <div class="metric-panel metric-panel--system">
      <div class="metric-panel-head">
        <div>
          <h2>Infrastructure</h2>
          <p>{systemMetrics ? `${systemMetrics.hostname} · uptime ${formatDuration(systemMetrics.uptimeSeconds)}` : 'Données serveur en attente'}</p>
        </div>
        <button type="button" class="metric-refresh" onclick={fetchSystemMetrics} aria-label="Actualiser les métriques">
          <wa-icon name="rotate"></wa-icon>
        </button>
      </div>

      {#if systemMetrics}
        <div class="system-grid">
          <div class="system-tile">
            <span>Charge CPU</span>
            <strong>{systemMetrics.cpu.loadAverage[0]?.toFixed(2) ?? '0.00'}</strong>
            <small>{systemMetrics.cpu.count} CPU</small>
          </div>
          <div class="system-tile">
            <span>Disque libre</span>
            <strong>{formatBytes(systemMetrics.disk.freeBytes)}</strong>
            <small>{formatPercent(systemMetrics.disk.usedPercent)} utilisé</small>
          </div>
          <div class="system-tile">
            <span>Process dashboard</span>
            <strong>{formatBytes(systemMetrics.process.rssBytes)}</strong>
            <small>{formatDuration(systemMetrics.process.uptimeSeconds)}</small>
          </div>
        </div>
        <div class="meter-block">
          <div class="meter-row">
            <span>RAM</span>
            <span>{formatBytes(systemMetrics.memory.usedBytes)} / {formatBytes(systemMetrics.memory.totalBytes)}</span>
          </div>
          <div class="meter-track">
            <span class="meter-fill meter-fill--memory" style={`width: ${clampPercent(systemMetrics.memory.usedPercent)}%`}></span>
          </div>
        </div>
        <div class="meter-block">
          <div class="meter-row">
            <span>Stockage</span>
            <span>{formatBytes(systemMetrics.disk.usedBytes)} / {formatBytes(systemMetrics.disk.totalBytes)}</span>
          </div>
          <div class="meter-track">
            <span class="meter-fill meter-fill--disk" style={`width: ${clampPercent(systemMetrics.disk.usedPercent)}%`}></span>
          </div>
        </div>
      {:else}
        <div class="metric-empty">
          <wa-icon name="triangle-exclamation"></wa-icon>
          <span>Métriques serveur non disponibles{systemMetricsError ? ` (${systemMetricsError})` : ''}</span>
        </div>
      {/if}
    </div>

    <div class="metric-panel">
      <div class="metric-panel-head">
        <div>
          <h2>Capacité</h2>
          <p>{quota > 0 ? `${formatNumber(totalInstances)} / ${formatNumber(quota)} instances autorisées` : 'Quota à configurer'}</p>
        </div>
      </div>
      <div class="quota-ring" style={`--quota: ${quotaPercent}%`}>
        <div>
          <strong>{formatPercent(quotaPercent)}</strong>
          <span>utilisé</span>
        </div>
      </div>
    </div>

    <div class="metric-panel">
      <div class="metric-panel-head">
        <div>
          <h2>État des instances</h2>
          <p>Répartition temps réel</p>
        </div>
      </div>
      <div class="metric-bars">
        {#each statusItems as item (item.key)}
          <div class="metric-bar-row">
            <div class="metric-bar-label">
              <span>{item.label}</span>
              <strong>{formatNumber(item.value)}</strong>
            </div>
            <div class="metric-bar-track">
              <span class="metric-bar-fill {item.className}" style={`width: ${item.percent}%`}></span>
            </div>
          </div>
        {/each}
      </div>
    </div>

    <div class="metric-panel">
      <div class="metric-panel-head">
        <div>
          <h2>Configuration</h2>
          <p>Options activées sur le parc</p>
        </div>
      </div>
      <div class="config-list">
        <div>
          <span>Domaines personnalisés</span>
          <strong>{formatNumber(customDomainCount)}</strong>
        </div>
        <div>
          <span>Mode dev</span>
          <strong>{formatNumber(devCount)}</strong>
        </div>
        <div>
          <span>Synchro admin</span>
          <strong>{formatNumber(adminSyncCount)}</strong>
        </div>
        <div>
          <span>Nettoyage auto</span>
          <strong>{formatNumber(autoVacuumCount)}</strong>
        </div>
      </div>
    </div>

    <div class="metric-panel metric-panel--versions">
      <div class="metric-panel-head">
        <div>
          <h2>Versions PocketBase</h2>
          <p>Top versions utilisées</p>
        </div>
      </div>
      {#if versionItems.length > 0}
        <div class="version-list">
          {#each versionItems as item (item.version)}
            <div class="version-row">
              <div class="version-row-head">
                <span>v{item.version}</span>
                <strong>{formatNumber(item.count)}</strong>
              </div>
              <div class="metric-bar-track">
                <span class="metric-bar-fill metric-bar-fill--green" style={`width: ${item.percent}%`}></span>
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="metric-empty">
          <wa-icon name="database"></wa-icon>
          <span>Aucune instance à analyser</span>
        </div>
      {/if}
    </div>
  </div>
</section>

<style>
  .dashboard-metrics {
    margin-bottom: 1.5rem;
  }

  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.75rem;
    margin-bottom: 0.75rem;
  }

  .kpi-card,
  .metric-panel {
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-strong);
    box-shadow: var(--app-shadow-sm);
  }

  .kpi-card {
    min-height: 7rem;
    padding: 1rem;
  }

  .kpi-card--primary {
    border-color: rgb(30 184 84 / 0.38);
    background: linear-gradient(145deg, rgb(30 184 84 / 0.18), var(--app-surface-strong));
  }

  .kpi-card--danger {
    border-color: rgb(239 68 68 / 0.45);
    background: linear-gradient(145deg, rgb(239 68 68 / 0.16), var(--app-surface-strong));
  }

  .kpi-label {
    display: block;
    margin-bottom: 0.65rem;
    color: var(--app-text-faint);
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .kpi-card strong {
    display: block;
    color: var(--app-text-strong);
    font-size: 1.8rem;
    line-height: 1;
  }

  .kpi-card small {
    display: block;
    margin-top: 0.65rem;
    color: var(--app-text-faint);
    font-size: 0.78rem;
  }

  .metric-panel-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.45fr) minmax(18rem, 0.8fr) minmax(0, 1fr);
    gap: 0.75rem;
    align-items: stretch;
  }

  .metric-panel {
    min-width: 0;
    min-height: 16rem;
    padding: 1rem;
  }

  .metric-panel--system {
    grid-row: span 2;
  }

  .metric-panel--versions {
    grid-column: span 2;
  }

  .metric-panel-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1rem;
  }

  .metric-panel h2 {
    margin: 0;
    color: var(--app-text-strong);
    font-size: 0.95rem;
    font-weight: 700;
  }

  .metric-panel p {
    margin: 0.25rem 0 0;
    color: var(--app-text-faint);
    font-size: 0.78rem;
  }

  .metric-refresh {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: 1px solid var(--app-border);
    border-radius: 0.375rem;
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
    cursor: pointer;
  }

  .metric-refresh:hover {
    color: var(--app-text-strong);
    border-color: var(--app-border-strong);
  }

  .system-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.5rem;
    margin-bottom: 1rem;
  }

  .system-tile {
    min-width: 0;
    min-height: 6.25rem;
    padding: 0.75rem;
    border: 1px solid var(--app-border);
    border-radius: 0.45rem;
    background: var(--app-surface-soft);
  }

  .system-tile span,
  .system-tile small {
    display: block;
    color: var(--app-text-faint);
    font-size: 0.72rem;
  }

  .system-tile strong {
    display: block;
    margin: 0.45rem 0;
    color: var(--app-text-strong);
    font-size: 1.05rem;
    line-height: 1.15;
  }

  .meter-block + .meter-block {
    margin-top: 0.9rem;
  }

  .meter-row,
  .metric-bar-label,
  .version-row-head,
  .config-list div {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .meter-row,
  .metric-bar-label,
  .version-row-head {
    margin-bottom: 0.4rem;
    font-size: 0.78rem;
    color: var(--app-text-muted);
  }

  .meter-row span:last-child,
  .metric-bar-label strong,
  .version-row-head strong {
    color: var(--app-text);
    font-weight: 700;
  }

  .meter-track,
  .metric-bar-track {
    position: relative;
    height: 0.5rem;
    overflow: hidden;
    border-radius: 999px;
    background: var(--app-surface-hover);
  }

  .meter-fill,
  .metric-bar-fill {
    position: absolute;
    inset: 0 auto 0 0;
    width: 0;
    border-radius: inherit;
    transition: width 220ms ease;
  }

  .meter-fill--memory {
    background: linear-gradient(90deg, #1eb854, #38bdf8);
  }

  .meter-fill--disk {
    background: linear-gradient(90deg, #fbbf24, #f97316);
  }

  .metric-bar-fill--green {
    background: #1eb854;
  }

  .metric-bar-fill--amber {
    background: #fbbf24;
  }

  .metric-bar-fill--blue {
    background: #38bdf8;
  }

  .metric-bar-fill--slate {
    background: #94a3b8;
  }

  .metric-bar-fill--muted {
    background: #64748b;
  }

  .metric-bar-fill--red {
    background: #ef4444;
  }

  .metric-bars,
  .version-list {
    display: grid;
    gap: 0.8rem;
  }

  .config-list {
    display: grid;
    gap: 0.5rem;
  }

  .config-list div {
    min-height: 2.55rem;
    padding: 0.65rem 0.75rem;
    border-radius: 0.45rem;
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
    font-size: 0.82rem;
  }

  .config-list strong {
    color: var(--app-text-strong);
  }

  .quota-ring {
    --quota: 0%;
    display: grid;
    place-items: center;
    width: 10.5rem;
    height: 10.5rem;
    margin: 0.75rem auto 0;
    border-radius: 999px;
    background:
      radial-gradient(circle at center, var(--app-bg) 0 58%, transparent 59%),
      conic-gradient(#1eb854 var(--quota), var(--app-surface-hover) 0);
  }

  .quota-ring div {
    display: grid;
    place-items: center;
    gap: 0.25rem;
  }

  .quota-ring strong {
    color: var(--app-text-strong);
    font-size: 1.35rem;
    line-height: 1;
  }

  .quota-ring span {
    color: var(--app-text-faint);
    font-size: 0.75rem;
  }

  .metric-empty {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    min-height: 8rem;
    color: var(--app-text-faint);
    font-size: 0.85rem;
  }

  @media (max-width: 1100px) {
    .kpi-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .metric-panel-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .metric-panel--system,
    .metric-panel--versions {
      grid-column: span 2;
      grid-row: auto;
    }
  }

  @media (max-width: 680px) {
    .kpi-grid,
    .metric-panel-grid,
    .system-grid {
      grid-template-columns: 1fr;
    }

    .metric-panel--system,
    .metric-panel--versions {
      grid-column: auto;
    }

    .kpi-card {
      min-height: 6.25rem;
    }
  }
</style>
