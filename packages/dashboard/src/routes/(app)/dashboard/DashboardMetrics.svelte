<script lang="ts">
  import { globalInstancesStore, userStore } from '$util/stores'
  import { getInstanceRuntimeState, runtimeStateLabel, type InstanceRuntimeState } from '$util/instancePower'
  import type { InstanceFields } from 'pockethost/common'

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

  const clampPercent = (value: number) => Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0))

  const formatNumber = (value: number) => new Intl.NumberFormat('fr-FR').format(value)

  const formatPercent = (value: number) =>
    `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 }).format(clampPercent(value))} %`

  const percentOf = (value: number, total: number) => (total > 0 ? clampPercent((value / total) * 100) : 0)

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
      <small
        >{quota > 0
          ? `${formatNumber(quotaRemaining)} disponible${quotaRemaining > 1 ? 's' : ''}`
          : 'Quota non configuré'}</small
      >
    </div>
    <div class="kpi-card">
      <span class="kpi-label">Actives</span>
      <strong>{formatNumber(activeCount)}</strong>
      <small>{formatNumber(runningCount)} en service</small>
    </div>
    <div class="kpi-card">
      <span class="kpi-label">Quota BDD</span>
      <strong>{quota > 0 ? `${formatNumber(totalInstances)} / ${formatNumber(quota)}` : '—'}</strong>
      <small>{quota > 0 ? `${formatPercent(quotaPercent)} utilisé` : 'Quota non configuré'}</small>
    </div>
    <div class="kpi-card" class:kpi-card--danger={failedCount > 0}>
      <span class="kpi-label">Incidents</span>
      <strong>{formatNumber(failedCount)}</strong>
      <small>{failedCount > 0 ? 'À vérifier' : 'Aucun échec'}</small>
    </div>
  </div>

  <div class="metric-panel-grid">
    <div class="metric-panel">
      <div class="metric-panel-head">
        <div>
          <h2>Capacité</h2>
          <p>
            {quota > 0
              ? `${formatNumber(totalInstances)} / ${formatNumber(quota)} instances autorisées`
              : 'Quota à configurer'}
          </p>
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
    box-shadow: none;
  }

  .kpi-card {
    min-height: 6rem;
    padding: 1rem;
  }

  .kpi-card--primary {
    border-color: var(--app-border-strong);
    background: var(--app-surface);
  }

  .kpi-card--danger {
    border-color: rgb(239 68 68 / 0.45);
    background: color-mix(in srgb, var(--app-surface) 96%, #ef4444);
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
    font-size: 1.5rem;
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
    grid-template-columns: repeat(12, minmax(0, 1fr));
    gap: 0.75rem;
    align-items: stretch;
  }

  .metric-panel {
    min-width: 0;
    grid-column: span 3;
    min-height: 0;
    padding: 1rem;
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

  .metric-bar-label,
  .version-row-head,
  .config-list div {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .metric-bar-label,
  .version-row-head {
    margin-bottom: 0.4rem;
    font-size: 0.78rem;
    color: var(--app-text-muted);
  }

  .metric-bar-label strong,
  .version-row-head strong {
    color: var(--app-text);
    font-weight: 700;
  }

  .metric-bar-track {
    position: relative;
    height: 0.5rem;
    overflow: hidden;
    border-radius: 999px;
    background: var(--app-surface-hover);
  }

  .metric-bar-fill {
    position: absolute;
    inset: 0 auto 0 0;
    width: 0;
    border-radius: inherit;
    transition: width 220ms ease;
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
      conic-gradient(var(--app-accent, #3b7cff) var(--quota), var(--app-surface-hover) 0);
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

    .metric-panel {
      grid-column: span 1;
    }
  }

  @media (max-width: 680px) {
    .kpi-grid,
    .metric-panel-grid {
      grid-template-columns: 1fr;
    }

    .metric-panel-grid > .metric-panel {
      grid-column: auto;
    }

    .kpi-card {
      min-height: 6.25rem;
    }
  }
</style>
