<script lang="ts">
  import { onMount } from 'svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client, type InstanceBackup } from '$src/pocketbase-client'
  import { instance } from '../store'

  let backups: InstanceBackup[] = []
  let isLoading = true
  let action = ''
  let errorMessage = ''
  let successMessage = ''

  $: ({ id, subdomain, cname, power } = $instance)
  $: displayName = cname || subdomain
  $: isBusy = !!action

  const formatBytes = (bytes: number) => {
    if (!bytes) return '0 o'
    const units = ['o', 'Ko', 'Mo', 'Go', 'To']
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
    const value = bytes / 1024 ** index
    return `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: value >= 10 ? 0 : 1 }).format(value)} ${units[index]}`
  }

  const formatDate = (value: string) => {
    if (!value) return '-'
    return new Intl.DateTimeFormat('fr-FR', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  }

  const statusLabel = (status: InstanceBackup['status']) => {
    if (status === 'ready') return 'Prête'
    if (status === 'running') return 'En cours'
    return 'Échec'
  }

  const kindLabel = (kind: InstanceBackup['kind']) => {
    if (kind === 'pre-restore') return 'Avant restauration'
    return 'Manuelle'
  }

  const loadBackups = async () => {
    isLoading = true
    errorMessage = ''
    try {
      backups = (await client().listInstanceBackups(id)).backups
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
    } finally {
      isLoading = false
    }
  }

  onMount(() => {
    void loadBackups()
  })

  const createBackup = async () => {
    if (isBusy) return

    const confirmed = window.confirm(
      `Créer une sauvegarde complète de ${displayName} ?${power ? "\n\nL'instance sera arrêtée puis redémarrée automatiquement." : ''}`
    )
    if (!confirmed) return

    action = 'create'
    errorMessage = ''
    successMessage = ''
    try {
      const result = await client().createInstanceBackup(id)
      backups = [result.backup, ...backups.filter((backup) => backup.id !== result.backup.id)]
      successMessage = 'Sauvegarde créée'
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackups()
    } finally {
      action = ''
    }
  }

  const downloadBackup = async (backup: InstanceBackup) => {
    if (isBusy || backup.status !== 'ready') return

    action = `download:${backup.id}`
    errorMessage = ''
    successMessage = ''
    try {
      await client().downloadInstanceBackup(id, backup)
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
    } finally {
      action = ''
    }
  }

  const restoreBackup = async (backup: InstanceBackup) => {
    if (isBusy || backup.status !== 'ready') return

    const confirmed = window.confirm(
      `Restaurer ${displayName} depuis cette sauvegarde ?\n\nUne sauvegarde de sécurité sera créée avant restauration. Si l'instance est active, elle sera arrêtée puis redémarrée après succès.`
    )
    if (!confirmed) return

    action = `restore:${backup.id}`
    errorMessage = ''
    successMessage = ''
    try {
      await client().restoreInstanceBackup(id, backup.id)
      successMessage = 'Instance restaurée'
      await loadBackups()
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackups()
    } finally {
      action = ''
    }
  }

  const deleteBackup = async (backup: InstanceBackup) => {
    if (isBusy) return

    const confirmed = window.confirm(`Supprimer définitivement la sauvegarde ${backup.filename || backup.id} ?`)
    if (!confirmed) return

    action = `delete:${backup.id}`
    errorMessage = ''
    successMessage = ''
    try {
      await client().deleteInstanceBackup(id, backup.id)
      backups = backups.filter((item) => item.id !== backup.id)
      successMessage = 'Sauvegarde supprimée'
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
    } finally {
      action = ''
    }
  }
</script>

<FeatureTab title="Sauvegardes" bind:errorMessage {successMessage} successFlash>
  <svelte:fragment slot="summary">
    <p>
      Les sauvegardes compressées incluent <code>pb_data</code>, <code>pb_public</code>, <code>pb_migrations</code> et
      <code>pb_hooks</code>. Les logs sont exclus.
    </p>
  </svelte:fragment>

  <svelte:fragment slot="cta">
    <button type="button" class="backup-create-btn" disabled={isBusy} onclick={createBackup}>
      <wa-icon name={action === 'create' ? 'rotate' : 'floppy-disk'}></wa-icon>
      {action === 'create' ? 'Sauvegarde...' : 'Créer une sauvegarde'}
    </button>
  </svelte:fragment>

  {#if isLoading}
    <div class="backup-empty">Chargement des sauvegardes...</div>
  {:else if backups.length === 0}
    <div class="backup-empty">
      <wa-icon name="box-archive"></wa-icon>
      <span>Aucune sauvegarde pour cette instance.</span>
    </div>
  {:else}
    <div class="backup-list">
      {#each backups as backup (backup.id)}
        <article class="backup-row" class:backup-row--failed={backup.status === 'failed'}>
          <div class="backup-main">
            <div class="backup-title-row">
              <span class="backup-title">{backup.filename || backup.id}</span>
              <span class="backup-status backup-status--{backup.status}">{statusLabel(backup.status)}</span>
            </div>
            <div class="backup-meta">
              <span>{formatDate(backup.created)}</span>
              <span>{kindLabel(backup.kind)}</span>
              <span>{formatBytes(backup.compressedBytes)} compressés</span>
              <span>{formatBytes(backup.sizeBytes)} source</span>
            </div>
            {#if backup.checksum}
              <code class="backup-checksum">sha256:{backup.checksum.slice(0, 16)}...</code>
            {/if}
            {#if backup.remoteKey}
              <p class="backup-remote">Copie R2/S3 : {backup.remoteKey}</p>
            {:else if backup.remoteError}
              <p class="backup-warning">Copie R2/S3 non faite : {backup.remoteError}</p>
            {/if}
            {#if backup.error}
              <p class="backup-error">{backup.error}</p>
            {/if}
          </div>

          <div class="backup-actions">
            <button
              type="button"
              class="backup-action"
              disabled={isBusy || backup.status !== 'ready'}
              onclick={() => downloadBackup(backup)}
              title="Télécharger"
              aria-label="Télécharger la sauvegarde"
            >
              <wa-icon name={action === `download:${backup.id}` ? 'rotate' : 'download'}></wa-icon>
            </button>
            <button
              type="button"
              class="backup-action backup-action--restore"
              disabled={isBusy || backup.status !== 'ready'}
              onclick={() => restoreBackup(backup)}
              title="Restaurer"
              aria-label="Restaurer cette sauvegarde"
            >
              <wa-icon name={action === `restore:${backup.id}` ? 'rotate' : 'rotate-left'}></wa-icon>
            </button>
            <button
              type="button"
              class="backup-action backup-action--danger"
              disabled={isBusy}
              onclick={() => deleteBackup(backup)}
              title="Supprimer"
              aria-label="Supprimer cette sauvegarde"
            >
              <wa-icon name={action === `delete:${backup.id}` ? 'rotate' : 'trash'}></wa-icon>
            </button>
          </div>
        </article>
      {/each}
    </div>
  {/if}
</FeatureTab>

<style>
  .backup-list {
    display: grid;
    gap: 0.75rem;
  }

  .backup-create-btn {
    display: inline-flex;
    min-height: 2.5rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px solid #1eb854;
    border-radius: 0.5rem;
    background: #1eb854;
    padding: 0 1rem;
    color: white;
    font-size: 0.9rem;
    font-weight: 800;
    line-height: 1;
    box-shadow: 0 10px 22px rgb(30 184 84 / 0.18);
    cursor: pointer;
    transition:
      background-color 120ms ease,
      border-color 120ms ease,
      box-shadow 120ms ease;
  }

  .backup-create-btn:hover:not(:disabled) {
    border-color: #16a34a;
    background: #16a34a;
    box-shadow: 0 12px 26px rgb(30 184 84 / 0.24);
  }

  .backup-create-btn:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px rgb(30 184 84 / 0.24);
  }

  .backup-create-btn:disabled {
    opacity: 0.6;
    cursor: wait;
  }

  .backup-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 1rem;
    align-items: center;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .backup-row--failed {
    border-color: rgb(239 68 68 / 0.35);
  }

  .backup-main {
    min-width: 0;
  }

  .backup-title-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    min-width: 0;
  }

  .backup-title {
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--app-text-strong);
    font-size: 0.94rem;
    font-weight: 800;
  }

  .backup-status {
    flex-shrink: 0;
    border: 1px solid var(--app-border);
    border-radius: 999px;
    padding: 0.15rem 0.55rem;
    font-size: 0.7rem;
    font-weight: 800;
    line-height: 1.2;
  }

  .backup-status--ready {
    border-color: rgb(30 184 84 / 0.35);
    color: #1eb854;
    background: rgb(30 184 84 / 0.1);
  }

  .backup-status--running {
    border-color: rgb(245 158 11 / 0.38);
    color: #d97706;
    background: rgb(245 158 11 / 0.12);
  }

  .backup-status--failed {
    border-color: rgb(239 68 68 / 0.35);
    color: #ef4444;
    background: rgb(239 68 68 / 0.1);
  }

  .backup-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem 0.75rem;
    margin-top: 0.35rem;
    color: var(--app-text-muted);
    font-size: 0.78rem;
    font-weight: 600;
  }

  .backup-checksum {
    display: inline-block;
    margin-top: 0.55rem;
    color: var(--app-text-faint);
    font-size: 0.75rem;
  }

  .backup-remote,
  .backup-warning,
  .backup-error {
    margin-top: 0.55rem;
    overflow-wrap: anywhere;
    font-size: 0.78rem;
    line-height: 1.4;
  }

  .backup-remote {
    color: var(--app-text-muted);
  }

  .backup-warning {
    color: #d97706;
  }

  .backup-error {
    color: #ef4444;
  }

  .backup-actions {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }

  .backup-action {
    display: inline-flex;
    width: 2.25rem;
    height: 2.25rem;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
    color: var(--app-text-strong);
    cursor: pointer;
    transition:
      border-color 120ms ease,
      background-color 120ms ease,
      color 120ms ease;
  }

  .backup-action:hover:not(:disabled) {
    border-color: #1eb854;
    background: rgb(30 184 84 / 0.1);
    color: #15803d;
  }

  .backup-action--restore:hover:not(:disabled) {
    border-color: rgb(59 130 246 / 0.5);
    background: rgb(59 130 246 / 0.1);
    color: #2563eb;
  }

  .backup-action--danger:hover:not(:disabled) {
    border-color: rgb(239 68 68 / 0.45);
    background: rgb(239 68 68 / 0.1);
    color: #ef4444;
  }

  .backup-action:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  .backup-empty {
    display: flex;
    min-height: 8rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px dashed var(--app-border);
    border-radius: 0.5rem;
    color: var(--app-text-muted);
    font-size: 0.9rem;
    font-weight: 700;
  }

  @media (max-width: 720px) {
    .backup-row {
      grid-template-columns: 1fr;
    }

    .backup-actions {
      justify-content: flex-start;
    }
  }
</style>
