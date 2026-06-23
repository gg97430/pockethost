<script lang="ts">
  import { goto } from '$app/navigation'
  import { client } from '$src/pocketbase-client'
  import type { InstanceFields } from 'pockethost/common'

  export let instance: InstanceFields
  export let compact = false

  let isBackingUp = false

  $: displayName = instance.cname || instance.subdomain

  const backup = async (event: MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()

    if (isBackingUp) return

    const confirmed = window.confirm(
      `Créer une sauvegarde complète de ${displayName} ?${instance.power ? "\n\nL'instance sera arrêtée puis redémarrée automatiquement." : ''}`
    )
    if (!confirmed) return

    isBackingUp = true
    try {
      await client().createInstanceBackup(instance.id)
      const openList = window.confirm('Sauvegarde créée. Ouvrir la liste des sauvegardes ?')
      if (openList) await goto(`/instances/${instance.id}/backups`)
    } catch (error) {
      const message = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      window.alert(message || 'Impossible de créer la sauvegarde.')
    } finally {
      isBackingUp = false
    }
  }
</script>

<button
  type="button"
  class="backup-btn"
  class:backup-btn--compact={compact}
  disabled={isBackingUp}
  title="Sauvegarder l'instance"
  aria-label="Sauvegarder l'instance"
  onclick={backup}
>
  <wa-icon name={isBackingUp ? 'rotate' : 'floppy-disk'}></wa-icon>
  {#if !compact}
    <span>{isBackingUp ? 'Sauvegarde...' : 'Sauvegarder'}</span>
  {:else}
    <span>{isBackingUp ? '...' : 'Sauveg.'}</span>
  {/if}
</button>

<style>
  .backup-btn {
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

  .backup-btn:hover:not(:disabled) {
    border-color: rgb(59 130 246 / 0.5);
    background: rgb(59 130 246 / 0.1);
    color: #2563eb;
  }

  .backup-btn:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px rgb(59 130 246 / 0.2);
  }

  .backup-btn:disabled {
    opacity: 0.48;
    cursor: wait;
  }

  .backup-btn--compact {
    min-width: 5.6rem;
    padding: 0 0.6rem;
  }
</style>
