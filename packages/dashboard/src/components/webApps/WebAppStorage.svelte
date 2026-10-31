<script lang="ts">
  import { onMount } from 'svelte'
  import { webRequest, webError, webDate } from '$lib/webApps'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'
  export let appId: string
  export let onchanged: () => void | Promise<void> = () => {}
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  type Volume = { name: string; target: string; sizeMiB: number; readOnly: boolean }
  type Measured = Volume & { usedBytes: number; overLimit: boolean; activeVolume: string }
  type Value = {
    volumes: Volume[]
    revision: string
    retention: number
    runtime: { available: boolean; checkedAt: string; volumes: Measured[] }
    limits: { maxVolumes: number; maxVolumeMiB: number; maxTotalMiB: number; hardQuota: false }
    snapshotRequiresPause: boolean
  }
  type Job = {
    id: string
    name: string
    operation: string
    status: string
    created: string
    startedAt: string
    finishedAt: string
    error: string
    result: { bytes?: number; dataBytes?: number; files?: number; backupId?: string; restartRequired?: boolean }
  }
  type Preview = {
    previewId: string
    previewToken: string
    expiresAt: string
    backup: { id: string; name: string; created: string; dataBytes: number }
    willPause: boolean
  }
  let value: Value | null = null,
    volumes: Volume[] = [],
    retention = 7,
    dirty = false,
    busy = '',
    error = '',
    message = '',
    disposed = false,
    refreshing = false,
    historyBusy = false
  let jobs: Job[] = [],
    page = 1,
    totalPages = 1,
    totalItems = 0,
    preview: Preview | null = null
  const labels: Record<string, string> = {
    queued: 'En attente',
    running: 'En cours',
    ready: 'Terminée',
    failed: 'Échec',
    deleting: 'Suppression par rétention',
    deleted: 'Supprimée',
    preview: 'Aperçu',
  }
  const operations: Record<string, string> = { backup: 'Snapshot', restore: 'Restauration', delete: 'Rétention' }
  const bytes = (value: number | undefined) =>
    value === undefined
      ? '—'
      : value >= 1024 ** 3
        ? `${(value / 1024 ** 3).toFixed(2)} Gio`
        : value >= 1024 ** 2
          ? `${(value / 1024 ** 2).toFixed(1)} Mio`
          : `${Math.ceil(value / 1024)} Kio`
  const forget = () => {
    dirty = true
    preview = null
    message = ''
  }
  const load = async (force = false) => {
    if (refreshing || disposed) return
    refreshing = true
    try {
      const next = await webRequest<Value>(`/${appId}/storage`)
      if (!disposed) {
        value = next
        if (force || !dirty) {
          volumes = next.volumes.map((volume) => ({ ...volume }))
          retention = next.retention
          dirty = false
        }
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      refreshing = false
    }
  }
  const history = async (next = page) => {
    if (historyBusy || disposed) return
    historyBusy = true
    try {
      const result = await webRequest<{ items: Job[]; page: number; totalPages: number; totalItems: number }>(
        `/${appId}/storage/history`,
        { query: { page: next, perPage: 20 } }
      )
      if (!disposed) {
        jobs = result.items
        page = result.page
        totalPages = result.totalPages
        totalItems = result.totalItems
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      historyBusy = false
    }
  }
  const add = () => {
    volumes = [...volumes, { name: '', target: '/data', sizeMiB: 256, readOnly: false }]
    forget()
  }
  const save = async (event: SubmitEvent) => {
    event.preventDefault()
    if (!value) return
    busy = 'save'
    error = ''
    message = ''
    try {
      value = await webRequest<Value>(`/${appId}/storage`, {
        method: 'PATCH',
        body: {
          revision: value.revision,
          volumes: volumes.map((volume) => ({ ...volume, sizeMiB: Number(volume.sizeMiB) })),
          retention: Number(retention),
        },
      })
      volumes = value.volumes.map((volume) => ({ ...volume }))
      dirty = false
      message =
        'Définitions enregistrées. Un déploiement applique les montages ; retirer une définition n’efface pas les données du volume.'
      await onchanged()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const snapshot = async (name: string) => {
    busy = name
    error = ''
    message = ''
    try {
      await webRequest(`/${appId}/storage/${encodeURIComponent(name)}/backup`, { method: 'POST', body: {} })
      message = 'Snapshot ajouté à la file. Les services seront temporairement mis en pause pendant sa création.'
      await history(1)
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const inspect = async (job: Job) => {
    busy = job.id
    error = ''
    message = ''
    preview = null
    try {
      preview = await webRequest<Preview>(`/${appId}/storage/${encodeURIComponent(job.name)}/restore/preview`, {
        method: 'POST',
        body: { backupId: job.id },
      })
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
      await webRequest(`/${appId}/storage/${encodeURIComponent(preview.backup.name)}/restore`, {
        method: 'POST',
        body: { backupId: preview.backup.id, previewId: preview.previewId, previewToken: preview.previewToken },
      })
      preview = null
      message = 'Restauration ajoutée à la file. Un nouveau volume sera préparé et le redémarrage sera contrôlé.'
      await history(1)
      await onchanged()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  $: inFlight = jobs.some((job) => ['queued', 'running'].includes(job.status))
  onMount(() => {
    disposed = false
    void load()
    void history(1)
    const timer = setInterval(() => {
      if (!document.hidden) {
        void load()
        void history()
      }
    }, 10000)
    return () => {
      disposed = true
      preview = null
      clearInterval(timer)
    }
  })
</script>

<section class="web-panel storage-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Fichiers de l’application</span>
      <h2>Volumes persistants</h2>
    </div>
    {#if dirty}<span class="web-status" data-tone="warning">Modifications non enregistrées</span>{/if}
  </div>
  <p class="web-help">
    Conservez les fichiers du site entre les déploiements. Ces volumes appartiennent à l’application ; les données
    PocketBase gardent leur propre stockage et leur sauvegarde.
  </p>
  <div class="web-alert" role="note">
    La croissance des volumes est surveillée : ce sont des limites d’usage, pas des quotas disque matériels. Le site
    peut être arrêté si une limite est franchie. Les snapshots cohérents nécessitent une pause temporaire des services.
  </div>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}{#if message}<div
      class="web-alert web-alert--success"
      role="status"
    >
      {message}
    </div>{/if}
  {#if !value}<p class="web-muted" role="status">Chargement du stockage…</p>{:else}<form
      class="web-form"
      onsubmit={save}
      oninput={forget}
      onchange={forget}
    >
      <fieldset class="web-fieldset" disabled={!capabilities['storage.manage']}>
        {#each volumes as volume, index}<article class="volume-editor">
            <div class="web-form-grid">
              <label class="web-field"
                ><span>Nom du volume</span><input
                  bind:value={volume.name}
                  required
                  pattern={'[a-z][a-z0-9-]{0,30}'}
                  maxlength="31"
                  disabled={!!busy}
                  placeholder="uploads"
                /><span class="web-help">Le nom identifie les données. Un nouveau nom crée un volume distinct.</span
                ></label
              ><label class="web-field"
                ><span>Chemin dans le conteneur</span><input
                  bind:value={volume.target}
                  required
                  disabled={!!busy}
                  placeholder="/app/uploads"
                /><span class="web-help">/data, /mnt, /app/data, /app/uploads ou /app/storage, avec sous-dossiers.</span
                ></label
              ><label class="web-field"
                ><span>Limite surveillée · Mio</span><input
                  type="number"
                  bind:value={volume.sizeMiB}
                  min="16"
                  max={value.limits.maxVolumeMiB}
                  step="1"
                  required
                  disabled={!!busy}
                /></label
              ><label class="web-check"
                ><input type="checkbox" bind:checked={volume.readOnly} disabled={!!busy} /> Montage en lecture seule</label
              >
            </div>
            <div>
              <button
                class="web-button"
                type="button"
                onclick={() => {
                  volumes = volumes.filter((_, i) => i !== index)
                  forget()
                }}
                disabled={!!busy}>Retirer la définition</button
              >
            </div>
          </article>{/each}
        {#if !volumes.length}<div class="storage-empty">
            <strong>Aucun volume défini.</strong>
            <p class="web-help">
              Un site qui stocke tous ses fichiers dans PocketBase peut rester sans volume applicatif.
            </p>
          </div>{/if}
        <div class="web-actions">
          <button
            class="web-button"
            type="button"
            onclick={add}
            disabled={!!busy || volumes.length >= value.limits.maxVolumes}>Ajouter un volume</button
          ><span class="web-help"
            >{volumes.length}/{value.limits.maxVolumes} volumes · {volumes.reduce(
              (total, volume) => total + Number(volume.sizeMiB || 0),
              0
            )} / {value.limits.maxTotalMiB} Mio définis</span
          >
        </div>
        <label class="web-field storage-retention"
          ><span>Snapshots à conserver par volume</span><input
            type="number"
            bind:value={retention}
            min="1"
            max="14"
            step="1"
            required
            disabled={!!busy}
          /></label
        >
      </fieldset>
      <p class="web-help">
        Les copies S3 sont chiffrées. Retirer un montage ne déplace et ne supprime pas ses fichiers. La version publiée
        garde ses montages jusqu’au prochain déploiement.
      </p>
      <div class="web-actions">
        {#if capabilities['storage.manage']}<button
            class="web-button web-button--primary"
            type="submit"
            disabled={!!busy || !dirty}
            >{busy === 'save' ? 'Enregistrement…' : 'Enregistrer les volumes et la rétention'}</button
          >{/if}<button class="web-button" type="button" onclick={() => load(true)} disabled={!!busy}>Recharger</button>
      </div>
    </form>{/if}
</section>
{#if value?.volumes.length}<section class="web-panel storage-panel">
    <div class="web-row">
      <h2>Volumes de la version publiée</h2>
      <span class="web-help">Relevé : {webDate(value.runtime.checkedAt)}</span>
    </div>
    {#each value.volumes as volume}{@const measured = value.runtime.volumes?.find((item) => item.name === volume.name)}
      <article class="volume-state">
        <div class="web-row">
          <div>
            <h3>{volume.name}</h3>
            <code>{volume.target}</code>
          </div>
          <span
            class="web-status"
            data-tone={measured?.overLimit ? 'error' : measured?.activeVolume ? 'success' : 'neutral'}
            >{measured?.overLimit
              ? 'Limite dépassée'
              : measured?.activeVolume
                ? 'Monté'
                : 'À monter au prochain déploiement'}</span
          >
        </div>
        <p class="web-help">
          Utilisation : {bytes(measured?.usedBytes)} / {volume.sizeMiB} Mio{volume.readOnly ? ' · lecture seule' : ''}.
        </p>
        {#if measured?.activeVolume && capabilities['storage.manage']}<div>
            <button
              class="web-button"
              type="button"
              onclick={() => snapshot(volume.name)}
              disabled={!!busy || inFlight || dirty}>Créer un snapshot · pause temporaire</button
            >
          </div>{/if}
      </article>{/each}
  </section>{/if}
{#if preview && capabilities['storage.manage']}<section class="web-panel storage-preview" aria-live="polite">
    <div class="web-row">
      <h2>Vérifier la restauration du volume</h2>
      <button class="web-button" type="button" onclick={() => (preview = null)} disabled={!!busy}>Annuler</button>
    </div>
    <p>
      <strong>{preview.backup.name}</strong> · snapshot du {webDate(preview.backup.created)} · {bytes(
        preview.backup.dataBytes
      )}
    </p>
    <div class="web-alert">
      Les services seront temporairement mis en pause. Les fichiers seront restaurés dans un nouveau volume, puis le
      redémarrage du site sera contrôlé. Les données de l’ancienne version restent disponibles.
    </div>
    <p class="web-help">
      Aperçu valable jusqu’au {webDate(preview.expiresAt)}. Aucune base PocketBase n’est restaurée par cette opération.
    </p>
    <div>
      <button
        class="web-button web-button--primary"
        type="button"
        onclick={restore}
        disabled={!!busy || Date.parse(preview.expiresAt) <= Date.now()}
        >{busy === 'restore' ? 'Mise en file…' : 'Confirmer la restauration avec pause des services'}</button
      >
    </div>
  </section>{/if}
<section class="web-panel storage-panel">
  <div class="web-row">
    <h2>Snapshots et restaurations</h2>
    <button class="web-button" type="button" onclick={() => history()} disabled={historyBusy}>Actualiser</button>
  </div>
  <div class="web-table-wrap">
    <table>
      <thead><tr><th>Volume</th><th>Opération</th><th>État</th><th>Date</th><th>Contenu</th><th>Actions</th></tr></thead
      ><tbody
        >{#each jobs as job}<tr
            ><td><strong>{job.name}</strong></td><td>{operations[job.operation] || job.operation}</td><td
              ><span
                class="web-status"
                data-tone={job.status === 'ready' ? 'success' : job.status === 'failed' ? 'error' : 'neutral'}
                >{labels[job.status] || job.status}</span
              ></td
            ><td>{webDate(job.finishedAt || job.startedAt || job.created)}</td><td
              >{bytes(job.result.dataBytes ?? job.result.bytes)}{#if job.result.files !== undefined}<div
                  class="web-help"
                >
                  {job.result.files} fichier(s)
                </div>{/if}</td
            ><td
              >{#if job.operation === 'backup' && job.status === 'ready' && capabilities['storage.manage']}<button
                  class="web-button"
                  type="button"
                  onclick={() => inspect(job)}
                  disabled={!!busy || inFlight || dirty || !value?.volumes.some((volume) => volume.name === job.name)}
                  >Préparer une restauration</button
                >{:else}—{/if}</td
            ></tr
          >{#if job.error}<tr
              ><td colspan="6"><p class="web-alert web-alert--error" data-notice="off">{job.error}</p></td></tr
            >{/if}{:else}<tr
            ><td colspan="6">{historyBusy ? 'Chargement…' : 'Aucune opération de stockage enregistrée.'}</td></tr
          >{/each}</tbody
      >
    </table>
  </div>
  <nav class="web-pagination" aria-label="Pages des opérations de stockage">
    <button class="web-button" type="button" onclick={() => history(page - 1)} disabled={page === 1 || historyBusy}
      >Précédent</button
    ><span>Page {page} / {Math.max(1, totalPages)} · {totalItems} opération(s)</span><button
      class="web-button"
      type="button"
      onclick={() => history(page + 1)}
      disabled={page >= totalPages || historyBusy}>Suivant</button
    >
  </nav>
</section>

<style>
  .storage-panel,
  .storage-preview {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .storage-panel p,
  .storage-preview p {
    margin: 0;
  }
  .volume-editor,
  .volume-state {
    display: grid;
    gap: 1rem;
    padding: 1rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    min-width: 0;
  }
  .volume-state h3 {
    font-size: 0.8125rem;
    margin: 0 0 0.4rem;
  }
  .storage-retention {
    max-width: 24rem;
  }
  .storage-empty {
    padding: 0.5rem 0;
  }
  .storage-empty strong {
    font-size: 0.8125rem;
  }
  .storage-empty p {
    margin-top: 0.5rem;
  }
  .storage-preview {
    border-color: #b7a36b80;
  }
  .storage-panel td[colspan] {
    white-space: normal;
  }
  .volume-state code {
    overflow-wrap: anywhere;
  }
  @media (max-width: 650px) {
    .volume-editor,
    .volume-state {
      padding: 0.8rem;
    }
  }
</style>
