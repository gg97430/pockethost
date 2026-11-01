<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { webRequest, webError, webDate, webCommit } from '$lib/webApps'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'
  export let appId: string
  export let onchanged: () => void | Promise<void> = () => {}
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  $: canManage = capabilities['backups.manage']
  type Backup = {
    id: string
    status: string
    trigger: string
    commit: string
    imageBytes: number
    totalImageBytes?: number
    imageCount?: number
    archiveBytes: number
    checksum: string
    error: string
    created: string
    finishedAt: string
    downloadable: boolean
    restorable: boolean
    recoverable: boolean
    localAvailable: boolean
    s3State: string
    s3Error: string
    s3CheckedAt: string
    s3Bytes: number
    s3TotalBytes: number
  }
  type Settings = {
    enabled: boolean
    hourUTC: number
    minuteUTC: number
    retention: number
    nextRunAt: string
    lastRunAt: string
    lastError: string
  }
  type List = {
    items: Backup[]
    page: number
    perPage: number
    totalItems: number
    totalPages: number
    settings: Settings
  }
  type Preview = {
    revision: string
    expiresAt: string
    previewToken: string
    backup: Backup
    appName: string
    configurationFields: string[]
    environmentChanges: { added: string[]; updated: string[]; removed: string[] }
    storedConfigurationIncludesPendingChanges: boolean
    warning: string
  }
  let value: List | null = null
  let page = 1,
    busy = '',
    error = '',
    message = '',
    hour = '03:00',
    enabled = false,
    retention = 3,
    dirty = false
  let preview: Preview | null = null,
    deleting = ''
  let disposed = false,
    loadedFor = '',
    mounted = false,
    request = 0
  const bytes = (n: number) =>
    n >= 1024 ** 3
      ? `${(n / 1024 ** 3).toFixed(1)} Gio`
      : n >= 1024 ** 2
        ? `${(n / 1024 ** 2).toFixed(1)} Mio`
        : `${Math.ceil(n / 1024)} Kio`
  const localLabel = (item: Backup) =>
    item.downloadable
      ? 'Locale prête'
      : {
          queued: 'En attente',
          running: 'Création locale…',
          failed: 'Échec local',
          deleting: 'Suppression…',
          deleted: 'Supprimée',
          ready: 'Locale absente ou illisible',
        }[item.status] || item.status
  const s3Label = (item: Backup) =>
    ({
      ready: 'S3 protégée',
      uploading: 'Copie S3 en cours',
      pending: 'S3 en attente',
      failed: 'Échec S3',
      disabled: 'S3 à configurer',
      deleted: 'S3 supprimée',
    })[item.s3State] || 'S3 en attente'
  const percent = (item: Backup) =>
    item.s3TotalBytes > 0 ? Math.min(100, Math.round((item.s3Bytes / item.s3TotalBytes) * 100)) : undefined
  const load = async (silent = false) => {
    const generation = ++request
    if (!silent) busy = 'load'
    try {
      const result = await webRequest<List>(`/${appId}/backups`, { query: { page } })
      if (disposed || generation !== request) return
      value = result
      if (!dirty) {
        enabled = result.settings.enabled
        hour = `${String(result.settings.hourUTC).padStart(2, '0')}:${String(result.settings.minuteUTC).padStart(2, '0')}`
        retention = result.settings.retention
      }
      if (!silent) error = ''
    } catch (e) {
      if (!disposed && generation === request) error = webError(e)
    } finally {
      if (!disposed && generation === request && !silent) busy = ''
    }
  }
  $: if (mounted && appId && loadedFor !== appId) {
    loadedFor = appId
    page = 1
    dirty = false
    preview = null
    void load()
  }
  const manual = async () => {
    busy = 'create'
    error = ''
    message = ''
    try {
      await webRequest(`/${appId}/backups`, { method: 'POST' })
      page = 1
      await load(true)
      message = 'Sauvegarde demandée. Le suivi des copies locale et S3 se met à jour automatiquement.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const saveSettings = async () => {
    busy = 'settings'
    error = ''
    message = ''
    const [hourUTC, minuteUTC] = hour.split(':').map(Number)
    try {
      await webRequest(`/${appId}/backups/settings`, {
        method: 'PUT',
        body: { enabled, hourUTC, minuteUTC, retention },
      })
      dirty = false
      await load(true)
      message = 'Planification et rétention enregistrées.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const inspect = async (item: Backup) => {
    busy = 'preview'
    error = ''
    message = ''
    preview = null
    deleting = ''
    try {
      preview = await webRequest<Preview>(`/${appId}/backups/${item.id}/restore/preview`, { method: 'POST' })
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const restore = async () => {
    if (!preview) return
    busy = 'restore'
    error = ''
    message = ''
    try {
      await webRequest(`/${appId}/backups/${preview.backup.id}/restore`, {
        method: 'POST',
        body: { revision: preview.revision, expiresAt: preview.expiresAt, previewToken: preview.previewToken },
      })
      preview = null
      message =
        'Restauration en attente. La version actuelle reste publiée jusqu’au contrôle de santé de la version restaurée.'
      await onchanged()
      await load(true)
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const remove = async (item: Backup) => {
    busy = 'delete'
    error = ''
    message = ''
    try {
      await webRequest(`/${appId}/backups/${item.id}`, { method: 'DELETE' })
      deleting = ''
      await load(true)
      message = 'Suppression demandée pour les copies locale et S3.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const recover = async (item: Backup) => {
    busy = 'recover'
    error = ''
    message = ''
    preview = null
    try {
      await webRequest(`/${appId}/backups/${item.id}/recover`, { method: 'POST' })
      await load(true)
      message = 'Récupération de l’archive depuis S3 demandée. Elle pourra ensuite être vérifiée et restaurée.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const download = async (item: Backup) => {
    busy = 'download'
    error = ''
    try {
      const response = await fetch(`/api/web-apps/${appId}/backups/${item.id}/download`, {
        headers: { Authorization: client().client.authStore.token },
        cache: 'no-store',
      })
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}))
        throw new Error(failure.message || 'Téléchargement de la sauvegarde impossible.')
      }
      const url = URL.createObjectURL(await response.blob()),
        link = document.createElement('a')
      link.href = url
      link.download = `web-${appId}-${item.id}.phwb`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      message = 'Archive chiffrée téléchargée. Conservez les clés de récupération séparément.'
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const changePage = async (next: number) => {
    page = next
    preview = null
    deleting = ''
    await load()
  }
  onMount(() => {
    mounted = true
    disposed = false
    const timer = setInterval(() => {
      if (!document.hidden && !busy) void load(true)
    }, 5000)
    return () => {
      disposed = true
      request++
      clearInterval(timer)
      preview = null
    }
  })
</script>

<section class="web-panel backup-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Récupération du site</span>
      <h2>Sauvegardes de l’application</h2>
    </div>
    {#if capabilities['backups.create']}<button
        class="web-button web-button--primary"
        type="button"
        onclick={manual}
        disabled={!!busy}>Sauvegarder maintenant</button
      >{/if}
  </div>
  <p class="web-help">
    Les sauvegardes conservent la version publiée, ses variables et les réglages enregistrés, y compris les
    modifications en attente. Une copie locale et une copie chiffrée sur S3 sont suivies séparément.
  </p>
  <div class="web-alert" role="note">
    Les bases de données et services externes, les fichiers utilisateurs et le cache Next.js ne sont pas inclus. La
    copie locale dépend des images conservées sur ce serveur. La copie S3 inclut les images de tous les services et
    nécessite des clés de récupération conservées séparément.
  </div>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}
  {#if canManage}<div class="backup-schedule">
      <label class="web-check"
        ><input type="checkbox" bind:checked={enabled} onchange={() => (dirty = true)} disabled={!!busy} /> Sauvegarde quotidienne</label
      >
      <label class="web-field"
        ><span>Horaire quotidien (UTC)</span><input
          type="time"
          bind:value={hour}
          oninput={() => (dirty = true)}
          disabled={!!busy}
        /></label
      >
      <label class="web-field"
        ><span>Sauvegardes à conserver</span><input
          type="number"
          min="1"
          max="14"
          step="1"
          bind:value={retention}
          oninput={() => (dirty = true)}
          disabled={!!busy}
        /></label
      >
      <button class="web-button" type="button" onclick={saveSettings} disabled={!!busy || !dirty}
        >Enregistrer la planification</button
      >
    </div>{/if}
  {#if value}<p class="web-help">
      {value.settings.enabled
        ? `Prochaine sauvegarde : ${webDate(value.settings.nextRunAt)} (votre heure locale).`
        : 'Planification désactivée.'} Dernier déclenchement planifié : {webDate(value.settings.lastRunAt)}. La
      rétention s’applique aussi aux sauvegardes manuelles.
    </p>{/if}
  {#if value?.settings.lastError}<div class="web-alert web-alert--error" role="alert" data-notice="off">
      {value.settings.lastError}
    </div>{/if}
  <p class="web-help">
    Les images conservées partagent un budget de 6 Gio. Une sauvegarde est suspendue sous 15 Gio d’espace libre ; la
    version actuelle reste disponible.
  </p>
</section>

{#if preview && canManage}<section class="web-panel backup-preview" aria-live="polite">
    <div class="web-row">
      <h3>Vérifier la restauration</h3>
      <button class="web-button" type="button" onclick={() => (preview = null)} disabled={!!busy}>Annuler</button>
    </div>
    <p>
      <strong>{preview.appName}</strong> · version {webCommit(preview.backup.commit)} · {webDate(
        preview.backup.created
      )}
    </p>
    <div class="web-alert web-alert--error" data-notice="off">{preview.warning}</div>
    <p class="web-help">
      Réglages modifiés : {preview.configurationFields.join(', ') || 'aucun'}. Variables : {preview.environmentChanges
        .added.length} ajout(s), {preview.environmentChanges.updated.length} modification(s), {preview
        .environmentChanges.removed.length} suppression(s). Les valeurs restent masquées.
    </p>
    {#if preview.environmentChanges.removed.length}<p class="web-help">
        Variables retirées : {preview.environmentChanges.removed.join(', ')}.
      </p>{/if}{#if preview.storedConfigurationIncludesPendingChanges}<p class="web-help">
        Cette sauvegarde contient des réglages en attente : ils seront rétablis dans le dashboard, tandis que l’image
        démarre avec les réglages de sa version publiée.
      </p>{/if}<button class="web-button web-button--primary" type="button" onclick={restore} disabled={!!busy}
      >Confirmer la restauration de cette application</button
    >
  </section>{/if}

<section class="web-panel backup-panel" aria-busy={busy === 'load'}>
  <div class="web-row">
    <h3>Archives et copies</h3>
    <button class="web-button" type="button" onclick={() => load()} disabled={!!busy}>Actualiser</button>
  </div>
  {#if !value}<p class="web-muted">Chargement des sauvegardes…</p>{:else if !value.items.length}<p class="web-muted">
      Aucune sauvegarde. Publiez une première version du site, puis créez une archive.
    </p>{/if}
  {#each value?.items || [] as item}<article class="backup-card">
      <div class="web-row">
        <div>
          <strong>{webDate(item.created)}</strong>
          <p class="web-help">
            {item.trigger === 'scheduled'
              ? 'Quotidienne'
              : item.trigger === 'platform'
                ? 'Plateforme (reprise)'
                : 'Manuelle'} · commit <code>{webCommit(item.commit)}</code>
          </p>
        </div>
        <div class="backup-badges">
          <span
            class="web-status"
            data-tone={item.downloadable ? 'success' : item.status === 'failed' ? 'danger' : 'neutral'}
            >{localLabel(item)}</span
          ><span
            class="web-status"
            data-tone={item.s3State === 'ready'
              ? 'success'
              : ['failed', 'disabled'].includes(item.s3State)
                ? 'warning'
                : 'neutral'}>{s3Label(item)}</span
          >
        </div>
      </div>
      {#if item.s3State === 'uploading'}<div class="backup-progress">
          <progress max="100" value={percent(item)} aria-label="Progression de la copie S3"></progress><span
            >{percent(item) === undefined ? 'Transfert S3 en cours…' : `${percent(item)} %`} · {bytes(
              item.s3Bytes
            )}{item.s3TotalBytes ? ` / ${bytes(item.s3TotalBytes)}` : ''}</span
          >
        </div>{/if}
      {#if item.error}<p class="web-alert web-alert--error" data-notice="off">{item.error}</p>{/if}{#if item.s3Error}<p
          class="web-alert"
        >
          {item.s3Error}
        </p>{/if}
      {#if item.archiveBytes}<p class="web-help">
          Archive chiffrée : {bytes(item.archiveBytes)} · {item.imageCount || 1}
          {(item.imageCount || 1) > 1 ? 'images conservées' : 'image conservée'} : {bytes(
            item.totalImageBytes || item.imageBytes
          )}{#if item.s3CheckedAt}
            · copie S3 contrôlée le {webDate(item.s3CheckedAt)}{/if}
        </p>{/if}
      {#if item.checksum && item.downloadable}<details class="web-details">
          <summary>Empreinte d’intégrité SHA-256</summary><code class="backup-checksum">{item.checksum}</code>
        </details>{/if}
      {#if canManage}<div class="web-actions">
          <button
            class="web-button"
            type="button"
            onclick={() => download(item)}
            disabled={!!busy || !item.downloadable}>Télécharger l’archive chiffrée</button
          ><button class="web-button" type="button" onclick={() => inspect(item)} disabled={!!busy || !item.restorable}
            >Préparer une restauration</button
          >{#if item.recoverable}<button
              class="web-button"
              type="button"
              onclick={() => recover(item)}
              disabled={!!busy}>Récupérer l’archive depuis S3</button
            >{/if}{#if ['ready', 'failed'].includes(item.status)}<button
              class="web-button"
              type="button"
              onclick={() => {
                deleting = item.id
                preview = null
              }}
              disabled={!!busy}>Supprimer</button
            >{/if}
        </div>{/if}
      {#if deleting === item.id && canManage}<div class="web-alert">
          <p>Supprimer les copies de cette sauvegarde et libérer les images qu’aucune autre sauvegarde n’utilise ?</p>
          <div class="web-actions">
            <button class="web-button" type="button" onclick={() => remove(item)} disabled={!!busy}
              >Confirmer la suppression</button
            ><button class="web-button" type="button" onclick={() => (deleting = '')} disabled={!!busy}
              >Conserver</button
            >
          </div>
        </div>{/if}
    </article>{/each}
  {#if value}<div class="web-pagination">
      <button class="web-button" type="button" onclick={() => changePage(page - 1)} disabled={!!busy || page <= 1}
        >Précédent</button
      ><span>Page {value.page} / {value.totalPages} · {value.totalItems} archive(s) et historique</span><button
        class="web-button"
        type="button"
        onclick={() => changePage(page + 1)}
        disabled={!!busy || page >= value.totalPages}>Suivant</button
      >
    </div>{/if}
</section>

<style>
  .backup-panel,
  .backup-preview {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .backup-panel p,
  .backup-preview p {
    margin: 0;
  }
  .backup-schedule {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr auto;
    gap: 1rem;
    align-items: end;
  }
  .backup-card {
    display: grid;
    gap: 0.9rem;
    border-top: 1px solid var(--web-border);
    padding-top: 1.2rem;
    min-width: 0;
  }
  .backup-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .backup-checksum {
    display: block;
    overflow-wrap: anywhere;
    font-size: 0.75rem;
    margin-top: 0.7rem;
  }
  .backup-progress {
    display: grid;
    gap: 0.5rem;
    font-size: 0.75rem;
    color: var(--web-muted);
  }
  progress {
    width: 100%;
    height: 0.6rem;
    accent-color: var(--web-accent);
  }
  .web-pagination {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.7rem;
    font-size: 0.8rem;
  }
  @media (max-width: 1000px) {
    .backup-schedule {
      grid-template-columns: 1fr 1fr;
    }
  }
  @media (max-width: 550px) {
    .backup-schedule {
      grid-template-columns: 1fr;
    }
    .backup-card .web-actions {
      display: grid;
      grid-template-columns: 1fr;
    }
    .backup-card .web-button {
      white-space: normal;
    }
  }
</style>
