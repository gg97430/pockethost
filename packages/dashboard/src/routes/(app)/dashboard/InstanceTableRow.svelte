<script lang="ts">
  import { goto } from '$app/navigation'
  import { INSTANCE_ADMIN_URL, PUBLIC_APEX_DOMAIN } from '$lib/appEnv'
  import { client } from '$src/pocketbase-client'
  import InstanceRuntimeBadge from '$components/InstanceRuntimeBadge.svelte'
  import { patchGlobalInstance } from '$util/stores'
  import { isInstanceShuttingDown } from '$util/instancePower'
  import InstanceFavoriteButton from '$components/InstanceFavoriteButton.svelte'
  import Toggle from '../instances/[instanceId]/Toggle.svelte'
  import type { InstanceFields } from 'pockethost/common'
  import type { DashboardInstanceMetric } from '$src/pocketbase-client'
  import DuplicateInstanceButton from './DuplicateInstanceButton.svelte'
  import BackupInstanceButton from './BackupInstanceButton.svelte'
  import InstanceResourceMeters from './InstanceResourceMeters.svelte'

  export let instance: InstanceFields
  export let metrics: DashboardInstanceMetric | undefined
  export let isFavorite = false
  export let onToggleFavorite: () => void = () => {}

  $: isShuttingDown = isInstanceShuttingDown(instance)
  $: displayName = instance.cname || instance.subdomain

  const { updateInstance } = client()

  const handlePowerChange = (power: boolean) => {
    patchGlobalInstance(instance.id, { power })

    updateInstance({ id: instance.id, fields: { power } }).catch(() => {
      patchGlobalInstance(instance.id, { power: !power })
    })
  }

  const openInstance = () => {
    goto(`/instances/${instance.id}`)
  }

  const onRowKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      openInstance()
    }
  }
</script>

<tr class="instance-table-row" tabindex="0" role="link" onclick={openInstance} onkeydown={onRowKeydown}>
  <td class="instance-table-name" data-label="Nom" onclick={(e) => e.stopPropagation()}>
    <div class="instance-table-name-cell">
      <InstanceFavoriteButton {isFavorite} onToggle={onToggleFavorite} />
      <div class="instance-table-name-label">
        <button type="button" class="instance-table-name-link" onclick={openInstance}>
          <span class="instance-table-name-text">{displayName}</span>
          <span class="instance-table-sub">.{PUBLIC_APEX_DOMAIN}</span>
        </button>
      </div>
    </div>
  </td>
  <td class="instance-table-status" data-label="État">
    <InstanceRuntimeBadge {instance} />
  </td>
  <td class="instance-table-resources" data-label="Ressources">
    <InstanceResourceMeters {metrics} variant="table" />
  </td>
  <td class="instance-table-version" data-label="Version">v{instance.version}</td>
  <td class="instance-table-actions" data-label="Actions" onclick={(e) => e.stopPropagation()}>
    <div class="instance-table-action-row">
      <a
        href={INSTANCE_ADMIN_URL(instance)}
        target="_blank"
        rel="noopener noreferrer"
        class="instance-table-admin-link"
        title="Ouvrir l'admin PocketBase"
      >
        <wa-icon name="up-right-from-square"></wa-icon>
        Admin
      </a>
      <BackupInstanceButton {instance} compact />
      <DuplicateInstanceButton {instance} compact />
    </div>
  </td>
  <td class="instance-table-power" data-label="Alimentation" onclick={(e) => e.stopPropagation()}>
    <Toggle checked={instance.power} loading={isShuttingDown} disabled={isShuttingDown} onChange={handlePowerChange} />
  </td>
</tr>

<style>
  .instance-table-row {
    cursor: pointer;
    color: var(--app-text);
    transition:
      background-color 120ms ease,
      box-shadow 120ms ease;
  }

  .instance-table-row:hover,
  .instance-table-row:focus-visible {
    background: var(--app-surface-hover);
    outline: none;
  }

  .instance-table-row:focus-visible {
    box-shadow: inset 0 0 0 2px rgb(30 184 84 / 0.32);
  }

  .instance-table-name {
    min-width: 0;
  }

  .instance-table-name-cell {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    min-width: 0;
  }

  .instance-table-name-label {
    flex: 1 1 0;
    min-width: 0;
  }

  .instance-table-name-link {
    display: block;
    width: 100%;
    min-width: 0;
    padding: 0;
    border: none;
    background: transparent;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  .instance-table-name-text {
    display: block;
    overflow: hidden;
    font-weight: 600;
    color: var(--app-text-strong);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .instance-table-sub {
    display: block;
    overflow: hidden;
    font-size: 0.75rem;
    color: var(--app-text-faint);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .instance-table-status {
    white-space: nowrap;
  }

  .instance-table-resources {
    min-width: 0;
  }

  .instance-table-version {
    font-size: 0.8125rem;
    color: var(--app-text-muted);
    white-space: nowrap;
  }

  .instance-table-action-row {
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    gap: 0.45rem;
    justify-content: flex-start;
    min-width: 0;
  }

  .instance-table-admin-link {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    justify-content: center;
    gap: 0.35rem;
    min-height: 2rem;
    padding: 0 0.65rem;
    border: 1px solid rgb(30 184 84 / 0.28);
    border-radius: 0.5rem;
    background: rgb(30 184 84 / 0.08);
    font-size: 0.78rem;
    font-weight: 600;
    color: #1eb854;
    text-decoration: none;
  }

  .instance-table-admin-link:hover {
    border-color: rgb(30 184 84 / 0.48);
    background: rgb(30 184 84 / 0.13);
  }

  .instance-table-power {
    text-align: right;
  }

  .instance-table-actions {
    min-width: 0;
  }

  .instance-table-actions :global(.backup-btn--compact) {
    min-width: 5.1rem;
    padding-inline: 0.55rem;
  }

  .instance-table-actions :global(.duplicate-btn--compact) {
    min-width: 6.1rem;
    padding-inline: 0.55rem;
  }
</style>
