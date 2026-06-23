<script lang="ts">
  import { goto } from '$app/navigation'
  import { INSTANCE_ADMIN_URL } from '$lib/appEnv'
  import { client } from '$src/pocketbase-client'
  import { patchGlobalInstance } from '$util/stores'
  import InstanceFavoriteButton from '$components/InstanceFavoriteButton.svelte'
  import Toggle from '../instances/[instanceId]/Toggle.svelte'
  import InstanceRuntimeBadge from '$components/InstanceRuntimeBadge.svelte'
  import { isInstanceShuttingDown } from '$util/instancePower'
  import type { InstanceFields } from 'pockethost/common'
  import DuplicateInstanceButton from './DuplicateInstanceButton.svelte'
  import BackupInstanceButton from './BackupInstanceButton.svelte'

  export let instance: InstanceFields
  export let isFavorite = false
  export let onToggleFavorite: () => void = () => {}

  $: isShuttingDown = isInstanceShuttingDown(instance)

  const { updateInstance } = client()

  const handlePowerChange = (power: boolean) => {
    patchGlobalInstance(instance.id, { power })

    updateInstance({ id: instance.id, fields: { power } }).catch(() => {
      patchGlobalInstance(instance.id, { power: !power })
    })
  }

  const openAdmin = (e: Event) => {
    e.stopPropagation()
  }

  const openInstance = () => {
    goto(`/instances/${instance.id}`)
  }

  const onKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      openInstance()
    }
  }
</script>

<!-- div, not button — avoids WA focus ring + invalid <a> inside <button> -->
<div
  class="instance-card"
  role="link"
  tabindex="0"
  onclick={openInstance}
  onkeydown={onKeydown}
>
  <div class="w-full flex flex-row items-center justify-between gap-4 p-4 min-h-[5.5rem]">
    <div class="flex flex-col items-start gap-2 flex-1 min-w-0 relative">
      <div class="flex items-center gap-1.5 w-full min-w-0">
        <div onclick={(e) => e.stopPropagation()}>
          <InstanceFavoriteButton {isFavorite} onToggle={onToggleFavorite} />
        </div>
        <span class="instance-card-title">
          {instance.cname ? instance.cname : instance.subdomain}
        </span>
      </div>

      <div class="flex flex-wrap items-center gap-1.5 min-w-0">
        <a
          href={INSTANCE_ADMIN_URL(instance)}
          target="_blank"
          onclick={openAdmin}
          class="instance-card-admin"
          title="Ouvrir l'admin"
          rel="noopener noreferrer"
        >
          <img src="/images/pocketbase-logo.svg" alt="PocketBase Logo" class="w-4 h-4" /> Admin
        </a>
        <p class="instance-card-version">
          <span>v{instance.version}</span>
        </p>
        <BackupInstanceButton {instance} compact />
        <DuplicateInstanceButton {instance} compact />
      </div>
      <div class="flex items-center min-h-5">
        <InstanceRuntimeBadge {instance} />
      </div>
    </div>

    <div class="flex flex-shrink-0 self-center min-w-[10.5rem] justify-end" onclick={(e) => e.stopPropagation()}>
      <Toggle
        checked={instance.power}
        loading={isShuttingDown}
        disabled={isShuttingDown}
        onChange={handlePowerChange}
      />
    </div>
  </div>
</div>

<style>
  .instance-card {
    flex: 1 1 0;
    width: 100%;
    border: 1px solid var(--app-border);
    border-radius: 0.75rem;
    background: var(--app-surface);
    box-shadow: var(--app-shadow-sm);
    color: var(--app-text);
    text-align: left;
    cursor: pointer;
    outline: none;
    transition:
      border-color 140ms ease,
      background-color 140ms ease,
      box-shadow 140ms ease,
      transform 140ms ease;
  }

  .instance-card:hover {
    border-color: var(--app-border-strong);
    background: var(--app-surface-hover);
    box-shadow: var(--app-shadow);
  }

  .instance-card:focus-visible {
    box-shadow: 0 0 0 3px rgb(30 184 84 / 0.22);
  }

  .instance-card-title {
    min-width: 0;
    flex: 1 1 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--app-text-strong);
    font-size: 1.25rem;
    font-weight: 700;
    text-align: start;
  }

  .instance-card-admin {
    display: inline-flex;
    flex-shrink: 0;
    align-items: center;
    gap: 0.5rem;
    border-radius: 999px;
    padding: 0.125rem 0.5rem 0.125rem 0;
    color: #1eb854;
    font-size: 0.75rem;
    font-weight: 700;
    text-decoration: none;
  }

  .instance-card-admin:hover {
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  .instance-card-version {
    flex-shrink: 0;
    border: 1px solid var(--app-border);
    border-radius: 999px;
    background: var(--app-surface-soft);
    padding: 0.125rem 0.5rem;
    color: var(--app-text-muted);
    font-size: 0.75rem;
    font-weight: 600;
  }
</style>
