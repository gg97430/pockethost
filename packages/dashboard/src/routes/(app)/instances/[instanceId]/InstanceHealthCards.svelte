<script lang="ts">
  import InstanceRuntimeBadge from '$components/InstanceRuntimeBadge.svelte'
  import { INSTANCE_HOST } from '$lib/appEnv'
  import type { InstanceOverview } from '$src/pocketbase-client'
  import type { InstanceFields } from 'pockethost/common'

  export let instance: InstanceFields
  export let overview: InstanceOverview | undefined

  const nf = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 })

  const formatPercent = (value: number | null | undefined) => {
    if (value === null || value === undefined || !Number.isFinite(value)) return 'Indispo.'
    return `${nf.format(Math.max(0, Math.min(100, value)))} %`
  }

  const formatBytes = (bytes: number | null | undefined) => {
    if (bytes === null || bytes === undefined) return 'Indispo.'
    if (!bytes) return '0 o'
    const units = ['o', 'Ko', 'Mo', 'Go', 'To']
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
    const value = bytes / 1024 ** index
    return `${nf.format(value)} ${units[index]}`
  }

  const formatDate = (value: string | undefined) => {
    if (!value) return 'Aucune'
    return new Intl.DateTimeFormat('fr-FR', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  }

  $: latestBackup = overview?.backups.latest
  $: runtime = overview?.runtime
  $: domainLabel = INSTANCE_HOST(instance)
  $: backupLabel = latestBackup ? formatDate(latestBackup.created) : 'Aucune'
  $: backupTone = latestBackup?.status === 'failed' ? 'danger' : latestBackup?.status === 'running' ? 'warning' : 'good'
  $: autoVacuumEnabled = instance.autoVacuum !== false
</script>

<section class="instance-health-grid" aria-label="État de l'instance">
  <article class="instance-health-card instance-health-card--runtime">
    <span class="instance-health-label">Runtime</span>
    <strong><InstanceRuntimeBadge {instance} /></strong>
    <small>Power {instance.power ? 'activé' : 'désactivé'} · statut {instance.status || 'inconnu'}</small>
  </article>

  <article class="instance-health-card">
    <span class="instance-health-label">CPU instance</span>
    <strong>{formatPercent(runtime?.cpuPercent)}</strong>
    <small>{runtime?.containerName ? `Conteneur ${runtime.containerName}` : 'Conteneur non mesuré'}</small>
  </article>

  <article class="instance-health-card">
    <span class="instance-health-label">RAM instance</span>
    <strong>{formatBytes(runtime?.memoryBytes)}</strong>
    <small>
      {formatPercent(runtime?.memoryPercent)} utilisée{runtime?.memoryLimitBytes
        ? ` · limite ${formatBytes(runtime.memoryLimitBytes)}`
        : ''}
    </small>
  </article>

  <article class="instance-health-card">
    <span class="instance-health-label">Domaine</span>
    <strong class="instance-health-domain">{domainLabel}</strong>
    <small>{instance.cname ? 'Domaine personnalisé actif' : 'Domaine interne'}</small>
  </article>

  <article class="instance-health-card">
    <span class="instance-health-label">Stockage instance</span>
    <strong>{formatBytes(overview?.storage.instanceBytes)}</strong>
    <small>Dossier local de l'instance</small>
  </article>

  <article class="instance-health-card">
    <span class="instance-health-label">Archives</span>
    <strong>{formatBytes(overview?.backups.totalCompressedBytes)}</strong>
    <small>{overview?.backups.count ?? 0} sauvegarde{(overview?.backups.count ?? 0) > 1 ? 's' : ''}</small>
  </article>
</section>

<section class="instance-config-strip" aria-label="Configuration active">
  <div class="instance-config-item">
    <span>Dernière sauvegarde</span>
    <strong
      class:instance-config-danger={backupTone === 'danger'}
      class:instance-config-warning={backupTone === 'warning'}
    >
      {backupLabel}
    </strong>
  </div>
  <div class="instance-config-item">
    <span>Version</span>
    <strong>v{instance.version}</strong>
  </div>
  <div class="instance-config-item">
    <span>Synchro admin</span>
    <strong>{instance.syncAdmin ? 'Activée' : 'Désactivée'}</strong>
  </div>
  <div class="instance-config-item">
    <span>Nettoyage auto</span>
    <strong>{autoVacuumEnabled ? 'Activé' : 'Désactivé'}</strong>
  </div>
  <div class="instance-config-item">
    <span>Mode dev</span>
    <strong>{instance.dev ? 'Actif' : 'Inactif'}</strong>
  </div>
</section>

<style>
  .instance-health-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 0.875rem;
  }

  .instance-health-card {
    min-width: 0;
    min-height: 8rem;
    border: 1px solid var(--app-border);
    border-radius: 0.75rem;
    background: var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .instance-health-card--runtime {
    border-color: rgb(30 184 84 / 0.32);
    background: linear-gradient(135deg, rgb(30 184 84 / 0.14), transparent 62%), var(--app-surface);
  }

  .instance-health-label {
    display: block;
    margin-bottom: 0.85rem;
    color: var(--app-text-muted);
    font-size: 0.72rem;
    font-weight: 900;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .instance-health-card strong {
    display: block;
    min-width: 0;
    color: var(--app-text-strong);
    font-size: 1.35rem;
    font-weight: 900;
    line-height: 1.15;
  }

  .instance-health-domain {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .instance-health-card small {
    display: block;
    margin-top: 0.6rem;
    color: var(--app-text-muted);
    font-size: 0.8rem;
    line-height: 1.35;
  }

  .instance-config-strip {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
    gap: 0.5rem;
    margin-top: 0.875rem;
  }

  .instance-config-item {
    min-width: 0;
    border: 1px solid var(--app-border);
    border-radius: 0.625rem;
    background: var(--app-surface-soft);
    padding: 0.8rem 0.9rem;
  }

  .instance-config-item span {
    display: block;
    margin-bottom: 0.25rem;
    color: var(--app-text-muted);
    font-size: 0.72rem;
    font-weight: 800;
  }

  .instance-config-item strong {
    display: block;
    overflow: hidden;
    color: var(--app-text-strong);
    font-size: 0.9rem;
    font-weight: 850;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .instance-config-danger {
    color: #f87171 !important;
  }

  .instance-config-warning {
    color: #fbbf24 !important;
  }
</style>
