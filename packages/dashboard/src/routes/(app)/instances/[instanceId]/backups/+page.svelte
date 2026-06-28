<script lang="ts">
  import { goto } from '$app/navigation'
  import { onMount } from 'svelte'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client, type InstanceBackup, type UploadProgress } from '$src/pocketbase-client'
  import { instance } from '../store'

  let backups: InstanceBackup[] = []
  let isLoading = true
  let action = ''
  let errorMessage = ''
  let successMessage = ''
  let archiveFile: File | null = null
  let serverPath = ''
  let fileInput: HTMLInputElement | undefined
  let uploadProgress: UploadProgress | null = null
  let uploadPhase = ''

  $: ({ id, subdomain, cname, power } = $instance)
  $: displayName = cname || subdomain
  $: isBusy = !!action
  $: selectedArchiveLabel = archiveFile
    ? `${archiveFile.name} - ${formatBytes(archiveFile.size)}`
    : '1. Choisir un ZIP, TGZ ou TAR.GZ'
  $: uploadProgressLabel = uploadProgress
    ? uploadProgress.total > 0
      ? `${formatBytes(uploadProgress.loaded)} / ${formatBytes(uploadProgress.total)}`
      : `${formatBytes(uploadProgress.loaded)} envoyés`
    : ''
  $: uploadChunksLabel =
    uploadProgress?.totalChunks && uploadProgress.totalChunks > 1
      ? `${uploadProgress.uploadedChunks || 0}/${uploadProgress.totalChunks} morceaux`
      : ''
  $: uploadPhaseLabel =
    uploadPhase === 'starting'
      ? 'Préparation de l’upload'
      : uploadPhase === 'assembling'
        ? 'Upload terminé, assemblage serveur en cours...'
        : uploadPhase === 'processing'
          ? 'Vérification serveur en cours...'
          : uploadPhase === 'uploading'
            ? uploadChunksLabel
              ? 'Upload par morceaux en cours'
              : 'Upload en cours'
            : ''

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
    if (kind === 'import') return 'Importée'
    return 'Manuelle'
  }

  const suggestedRestoreSubdomain = () => {
    const base = (subdomain || displayName || 'instance')
      .toLowerCase()
      .replace(/[^a-z0-9-]/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-+|-+$/g, '')
    const normalized = base.match(/^[a-z]/) ? base : `base-${base}`
    return `${normalized.slice(0, 31).replace(/-+$/g, '')}-restore`
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

  const importArchive = async () => {
    if (isBusy || !archiveFile) return

    const confirmed = window.confirm(
      `Importer ${archiveFile.name} comme sauvegarde de ${displayName} ?\n\nFormats acceptes: dossier pb_data complet, ou fichiers PocketBase a la racine du ZIP comme data.db.`
    )
    if (!confirmed) return

    action = 'import:file'
    errorMessage = ''
    successMessage = ''
    uploadProgress = { loaded: 0, total: archiveFile.size, percent: 0 }
    uploadPhase = 'uploading'
    try {
      const result = await client().importInstanceBackup(id, {
        file: archiveFile,
        onProgress: (progress) => {
          uploadProgress = progress
          uploadPhase = progress.phase || (progress.percent >= 100 ? 'processing' : 'uploading')
        },
      })
      backups = [result.backup, ...backups.filter((backup) => backup.id !== result.backup.id)]
      archiveFile = null
      if (fileInput) fileInput.value = ''
      successMessage = 'Archive importée'
    } catch (error) {
      errorMessage = error instanceof Error ? client().parseError(error)[0] || error.message : `${error}`
      await loadBackups()
    } finally {
      action = ''
      uploadProgress = null
      uploadPhase = ''
    }
  }

  const importServerArchive = async () => {
    const path = serverPath.trim()
    if (isBusy || !path) return

    const confirmed = window.confirm(
      `Importer l'archive serveur suivante pour ${displayName} ?\n\n${path}\n\nLe fichier doit être dans le dossier serveur autorisé.`
    )
    if (!confirmed) return

    action = 'import:server'
    errorMessage = ''
    successMessage = ''
    try {
      const result = await client().importInstanceBackup(id, { serverPath: path })
      backups = [result.backup, ...backups.filter((backup) => backup.id !== result.backup.id)]
      serverPath = ''
      successMessage = 'Archive serveur importée'
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

  const restoreBackupToNewInstance = async (backup: InstanceBackup) => {
    if (isBusy || backup.status !== 'ready') return

    const subdomain = window.prompt(
      `Nom de la nouvelle instance à créer depuis ${backup.filename || backup.id} ?\n\nElle sera créée éteinte, sans écraser ${displayName}.`,
      suggestedRestoreSubdomain()
    )
    if (subdomain === null) return

    action = `restore-new:${backup.id}`
    errorMessage = ''
    successMessage = ''
    try {
      const result = await client().restoreInstanceBackupToNewInstance(id, backup.id, { subdomain: subdomain.trim() })
      successMessage = 'Nouvelle instance restaurée'
      await goto(`/instances/${result.instance.id}`)
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
    <div class="backup-cta">
      <button type="button" class="backup-import-zip-cta" disabled={isBusy} onclick={() => fileInput?.click()}>
        <wa-icon name="file-zipper"></wa-icon>
        Importer ZIP à restaurer
      </button>
      <button type="button" class="backup-create-btn" disabled={isBusy} onclick={createBackup}>
        <wa-icon name={action === 'create' ? 'rotate' : 'floppy-disk'}></wa-icon>
        {action === 'create' ? 'Sauvegarde...' : 'Créer une sauvegarde'}
      </button>
    </div>
  </svelte:fragment>

  <section class="backup-import">
    <div class="backup-import-copy">
      <strong>Importer une sauvegarde ZIP à restaurer</strong>
      <span>
        Réservé superadmin. Étape 1 : importez le ZIP. Étape 2 : cliquez <strong>Restaurer</strong> sur la ligne créée.
        Le ZIP peut contenir <code>pb_data</code>, ou directement les fichiers SQLite comme <code>data.db</code>.
      </span>
    </div>

    <div class="backup-import-grid">
      <label class="backup-file">
        <input
          bind:this={fileInput}
          type="file"
          accept=".zip,.tgz,.tar.gz,application/zip,application/gzip"
          disabled={isBusy}
          onchange={(event) => {
            archiveFile = event.currentTarget.files?.[0] || null
          }}
        />
        <span>{selectedArchiveLabel}</span>
      </label>
      <button type="button" class="backup-import-btn" disabled={isBusy || !archiveFile} onclick={importArchive}>
        <wa-icon name={action === 'import:file' ? 'rotate' : 'upload'}></wa-icon>
        {action === 'import:file' ? 'Import...' : '2. Importer le ZIP'}
      </button>
    </div>

    {#if uploadProgress}
      <div class="backup-upload-progress" aria-live="polite">
        <div class="backup-upload-progress__row">
          <strong>{uploadPhaseLabel}</strong>
          <span
            >{uploadProgress.percent}% - {uploadProgressLabel}{uploadChunksLabel ? ` - ${uploadChunksLabel}` : ''}</span
          >
        </div>
        <div
          class="backup-upload-progress__track"
          role="progressbar"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-valuenow={uploadProgress.percent}
        >
          <span style={`width: ${uploadProgress.percent}%`}></span>
        </div>
      </div>
    {/if}

    <div class="backup-import-grid">
      <input
        class="backup-server-path"
        bind:value={serverPath}
        disabled={isBusy}
        placeholder="Chemin serveur, ex. /home/ubuntu/.local/share/pockethost/data/imports/backup.zip"
      />
      <button
        type="button"
        class="backup-import-btn backup-import-btn--server"
        disabled={isBusy || !serverPath.trim()}
        onclick={importServerArchive}
      >
        <wa-icon name={action === 'import:server' ? 'rotate' : 'server'}></wa-icon>
        {action === 'import:server' ? 'Import...' : 'Importer ZIP serveur'}
      </button>
    </div>
  </section>

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
              <span>Télécharger</span>
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
              <span>Restaurer</span>
            </button>
            <button
              type="button"
              class="backup-action backup-action--restore-new"
              disabled={isBusy || backup.status !== 'ready'}
              onclick={() => restoreBackupToNewInstance(backup)}
              title="Restaurer dans une nouvelle instance"
              aria-label="Restaurer cette sauvegarde dans une nouvelle instance"
            >
              <wa-icon name={action === `restore-new:${backup.id}` ? 'rotate' : 'copy'}></wa-icon>
              <span>Nouvelle instance</span>
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
              <span>Supprimer</span>
            </button>
          </div>
        </article>
      {/each}
    </div>
  {/if}
</FeatureTab>

<style>
  .backup-cta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    justify-content: flex-end;
  }

  .backup-import-zip-cta {
    display: inline-flex;
    min-height: 2.5rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px solid rgb(37 99 235 / 0.5);
    border-radius: 0.5rem;
    background: rgb(37 99 235 / 0.13);
    padding: 0 1rem;
    color: #2563eb;
    font-size: 0.9rem;
    font-weight: 900;
    line-height: 1;
    cursor: pointer;
    transition:
      background-color 120ms ease,
      border-color 120ms ease,
      box-shadow 120ms ease;
  }

  .backup-import-zip-cta:hover:not(:disabled) {
    border-color: rgb(37 99 235 / 0.7);
    background: rgb(37 99 235 / 0.19);
    box-shadow: 0 10px 22px rgb(37 99 235 / 0.14);
  }

  .backup-import-zip-cta:disabled {
    opacity: 0.6;
    cursor: wait;
  }

  .backup-import {
    display: grid;
    gap: 0.75rem;
    margin-bottom: 1rem;
    border: 1px solid rgb(14 165 233 / 0.22);
    border-radius: 0.65rem;
    background: linear-gradient(135deg, rgb(14 165 233 / 0.1), transparent 55%), var(--app-surface);
    padding: 1rem;
    box-shadow: var(--app-shadow-sm);
  }

  .backup-import-copy {
    display: grid;
    gap: 0.25rem;
  }

  .backup-import-copy strong {
    color: var(--app-text-strong);
    font-size: 0.95rem;
    font-weight: 900;
  }

  .backup-import-copy span {
    color: var(--app-text-muted);
    font-size: 0.82rem;
    line-height: 1.45;
  }

  .backup-import-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 0.65rem;
  }

  .backup-file {
    display: flex;
    min-height: 2.5rem;
    align-items: center;
    border: 1px dashed rgb(14 165 233 / 0.35);
    border-radius: 0.5rem;
    background: rgb(14 165 233 / 0.06);
    padding: 0 0.75rem;
    color: var(--app-text);
    font-size: 0.86rem;
    font-weight: 700;
    cursor: pointer;
  }

  .backup-file input {
    display: none;
  }

  .backup-server-path {
    min-height: 2.5rem;
    min-width: 0;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface);
    padding: 0 0.75rem;
    color: var(--app-text);
    font: inherit;
    font-size: 0.86rem;
  }

  .backup-server-path:focus-visible {
    outline: none;
    border-color: rgb(14 165 233 / 0.55);
    box-shadow: 0 0 0 3px rgb(14 165 233 / 0.16);
  }

  .backup-import-btn {
    display: inline-flex;
    min-height: 2.5rem;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    border: 1px solid rgb(14 165 233 / 0.45);
    border-radius: 0.5rem;
    background: rgb(14 165 233 / 0.12);
    padding: 0 0.85rem;
    color: #0284c7;
    font-size: 0.84rem;
    font-weight: 900;
    cursor: pointer;
    transition:
      background-color 120ms ease,
      border-color 120ms ease;
  }

  .backup-import-btn:hover:not(:disabled) {
    border-color: rgb(14 165 233 / 0.68);
    background: rgb(14 165 233 / 0.18);
  }

  .backup-import-btn--server {
    border-color: rgb(99 102 241 / 0.38);
    background: rgb(99 102 241 / 0.1);
    color: #4f46e5;
  }

  .backup-import-btn:disabled,
  .backup-file:has(input:disabled),
  .backup-server-path:disabled {
    opacity: 0.58;
    cursor: not-allowed;
  }

  .backup-upload-progress {
    display: grid;
    gap: 0.45rem;
    border: 1px solid rgb(14 165 233 / 0.24);
    border-radius: 0.55rem;
    background: rgb(14 165 233 / 0.07);
    padding: 0.75rem;
  }

  .backup-upload-progress__row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.45rem;
    color: var(--app-text);
    font-size: 0.8rem;
    font-weight: 750;
  }

  .backup-upload-progress__row strong {
    color: var(--app-text-strong);
    font-weight: 900;
  }

  .backup-upload-progress__row span {
    color: var(--app-text-muted);
  }

  .backup-upload-progress__track {
    position: relative;
    height: 0.65rem;
    overflow: hidden;
    border-radius: 999px;
    background: rgb(14 165 233 / 0.13);
  }

  .backup-upload-progress__track span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, #0ea5e9, #22c55e);
    box-shadow: 0 0 18px rgb(14 165 233 / 0.35);
    transition: width 160ms ease;
  }

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
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
    justify-content: flex-end;
  }

  .backup-action {
    display: inline-flex;
    min-width: 7.5rem;
    height: 2.25rem;
    align-items: center;
    justify-content: center;
    gap: 0.45rem;
    border: 1px solid var(--app-border);
    border-radius: 0.5rem;
    background: var(--app-surface-soft);
    padding: 0 0.75rem;
    color: var(--app-text-strong);
    font-size: 0.78rem;
    font-weight: 850;
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

  .backup-action--restore {
    border-color: rgb(59 130 246 / 0.42);
    background: rgb(59 130 246 / 0.1);
    color: #2563eb;
  }

  .backup-action--restore-new {
    border-color: rgb(14 165 233 / 0.42);
    background: rgb(14 165 233 / 0.1);
    color: #0284c7;
  }

  .backup-action--restore-new:hover:not(:disabled) {
    border-color: rgb(14 165 233 / 0.58);
    background: rgb(14 165 233 / 0.16);
    color: #0369a1;
  }

  .backup-action--danger:hover:not(:disabled) {
    border-color: rgb(239 68 68 / 0.45);
    background: rgb(239 68 68 / 0.1);
    color: #ef4444;
  }

  .backup-action--danger {
    min-width: 6.75rem;
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
    .backup-import-grid {
      grid-template-columns: 1fr;
    }

    .backup-row {
      grid-template-columns: 1fr;
    }

    .backup-actions {
      justify-content: flex-start;
    }

    .backup-action {
      min-width: 0;
      flex: 1 1 9rem;
    }
  }
</style>
