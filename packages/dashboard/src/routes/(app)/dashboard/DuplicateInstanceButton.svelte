<script lang="ts">
  import { goto } from '$app/navigation'
  import { client } from '$src/pocketbase-client'
  import { upsertGlobalInstance } from '$util/stores'
  import { isInstanceShuttingDown } from '$util/instancePower'
  import type { InstanceFields } from 'pockethost/common'

  export let instance: InstanceFields
  export let compact = false

  let isDuplicating = false

  $: isSourceReady = !instance.power && instance.status?.toLowerCase() === 'idle' && !isInstanceShuttingDown(instance)
  $: title = isSourceReady ? 'Dupliquer la base' : "Eteignez l'instance avant de dupliquer sa base"

  const duplicate = async (event: MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()

    if (!isSourceReady || isDuplicating) return
    if (!window.confirm(`Dupliquer la base de ${instance.cname || instance.subdomain} ?`)) return

    isDuplicating = true
    try {
      const result = await client().duplicateInstance(instance.id)
      upsertGlobalInstance(result.instance)
      await goto(`/instances/${result.instance.id}`)
    } catch (error) {
      const message = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      window.alert(message || 'Impossible de dupliquer la base.')
    } finally {
      isDuplicating = false
    }
  }
</script>

<button
  type="button"
  class="duplicate-btn"
  class:duplicate-btn--compact={compact}
  disabled={!isSourceReady || isDuplicating}
  {title}
  aria-label="Dupliquer la base"
  onclick={duplicate}
>
  <wa-icon name={isDuplicating ? 'rotate' : 'copy'}></wa-icon>
  {#if !compact}
    <span>{isDuplicating ? 'Copie...' : 'Dupliquer la base'}</span>
  {:else}
    <span>{isDuplicating ? '...' : 'Dupliquer'}</span>
  {/if}
</button>

<style>
  .duplicate-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.4rem;
    min-height: 2rem;
    padding: 0 0.7rem;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
    color: var(--app-text-strong);
    font-size: 0.78rem;
    font-weight: 700;
    line-height: 1;
    white-space: nowrap;
    cursor: pointer;
    transition:
      background-color 120ms ease,
      border-color 120ms ease,
      color 120ms ease;
  }

  .duplicate-btn:hover:not(:disabled) {
    border-color: #1eb854;
    background: rgb(30 184 84 / 0.1);
    color: #15803d;
  }

  .duplicate-btn:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px rgb(30 184 84 / 0.2);
  }

  .duplicate-btn:disabled {
    opacity: 0.48;
    cursor: not-allowed;
  }

  .duplicate-btn--compact {
    min-width: 6.8rem;
    padding: 0 0.6rem;
  }
</style>
