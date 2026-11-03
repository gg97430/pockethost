<script lang="ts">
  import { onMount } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { webError } from '$lib/webApps'
  import { resourceFormatBytes } from '$lib/platformResources'
  type Database = {
    id: string
    name: string
    mode: 'adaptive' | 'fixed'
    reservationMiB: number
    maximumMiB: number
    usageBytes: number | null
    limitBytes: number | null
    decision: string
  }
  type State = {
    revision: string
    checkedAt: string
    totalBytes: number
    availableBytes: number
    reservedBytes: number
    adaptiveMaximumMiB: number
    maintenance: { paused: boolean; owner: string; expiresAt: number | null; error: string }
    databases: Database[]
  }
  let state: State | null = null,
    error = '',
    message = '',
    busy = '',
    editing = '',
    maximumMiB = 4096,
    mode: 'adaptive' | 'fixed' = 'adaptive',
    seconds = 300
  let refresh = false,
    disposed = false,
    open = false
  const request = (path: string, body?: unknown) =>
    client().client.send('/api/admin/controls' + path, {
      method: body === undefined ? 'GET' : 'PUT',
      ...(body === undefined ? {} : { body }),
      requestKey: null,
    })
  const load = async () => {
    if (refresh || disposed) return
    refresh = true
    try {
      const value = (await request('')) as State
      if (!disposed) state = value
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      refresh = false
    }
  }
  const maintenance = async () => {
    if (!state) return
    busy = 'maintenance'
    error = ''
    message = ''
    try {
      await request(
        '/maintenance',
        state.maintenance.paused
          ? { paused: false, owner: state.maintenance.owner }
          : { paused: true, seconds: Number(seconds) }
      )
      message = state.maintenance.paused
        ? 'Les nouvelles opérations peuvent reprendre.'
        : 'Les opérations actives terminent ; les nouveaux push restent en file.'
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const edit = (row: Database) => {
    editing = row.id
    maximumMiB =
      row.mode === 'fixed'
        ? row.reservationMiB || Math.round((row.limitBytes || 4096 * 1024 ** 2) / 1024 ** 2)
        : row.maximumMiB
    mode = row.mode
    message = ''
    error = ''
  }
  const save = async (event: SubmitEvent) => {
    event.preventDefault()
    if (!state) return
    busy = 'memory'
    error = ''
    message = ''
    try {
      await request('/memory', {
        id: editing,
        mode,
        maximumMiB: mode === 'adaptive' ? state.adaptiveMaximumMiB : Number(maximumMiB),
        revision: state.revision,
      })
      editing = ''
      message = 'Réglage enregistré sans redémarrage. Les mesures se mettent à jour au prochain contrôle.'
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  onMount(() => {
    disposed = false
    const timer = setInterval(() => {
      if (open && !document.hidden && !editing) void load()
    }, 5000)
    return () => {
      disposed = true
      clearInterval(timer)
    }
  })
</script>

<details
  class="web-panel operator-controls"
  bind:open
  ontoggle={(event) => {
    open = event.currentTarget.open
    if (open) void load()
  }}
>
  <summary>Réglages de RAM et pause des déploiements</summary>
  {#if error}<p class="web-alert web-alert--error" role="alert">{error}</p>{/if}
  {#if message}<p class="web-alert web-alert--success" role="status">{message}</p>{/if}
  {#if state}
    <div class="web-row">
      <div>
        <h3>Déploiements et tâches d’hébergement</h3>
        <p class="web-help">
          Les sites et bases restent accessibles. Les opérations actives terminent, les nouveaux push restent reçus.
        </p>
      </div>
      <span class="web-status" data-tone={state.maintenance.paused ? 'warning' : 'success'}
        >{state.maintenance.paused ? 'Nouvelles opérations en pause' : 'Admissions ouvertes'}</span
      >
    </div>
    {#if state.maintenance.error}<p class="web-alert" role="alert">{state.maintenance.error}</p>{/if}
    {#if state.maintenance.expiresAt && state.maintenance.paused}<p class="web-help">
        Reprise automatique à {new Intl.DateTimeFormat('fr-FR', {
          timeZone: 'Indian/Reunion',
          timeStyle: 'medium',
        }).format(new Date(state.maintenance.expiresAt * 1000))} · La Réunion
      </p>{/if}
    <div class="web-actions">
      {#if !state.maintenance.paused}<label class="web-field"
          ><span>Durée de la pause</span><select bind:value={seconds} disabled={!!busy}
            ><option value={120}>2 minutes</option><option value={300}>5 minutes</option><option value={600}
              >10 minutes</option
            ><option value={1800}>30 minutes</option></select
          ></label
        >{/if}
      <button
        class="web-button"
        type="button"
        onclick={maintenance}
        disabled={!!busy || (state.maintenance.paused && !state.maintenance.owner.startsWith('admin-'))}
        >{busy === 'maintenance'
          ? 'Application…'
          : state.maintenance.paused
            ? 'Reprendre les admissions'
            : 'Suspendre les nouvelles opérations'}</button
      >
    </div>
    {#if state.maintenance.paused && !state.maintenance.owner.startsWith('admin-')}<p class="web-help">
        Une publication de Kolab gère cette pause et sa reprise.
      </p>{/if}
    <div>
      <h3>Mémoire des bases</h3>
      <p class="web-help">
        Utilisée : consommation mesurée. Plafond : limite actuelle du conteneur. Réservée : budget conservé pour cette
        base, même au repos.
      </p>
      <p class="web-help">
        Serveur : {resourceFormatBytes(state.totalBytes)} · disponibles : {resourceFormatBytes(state.availableBytes)} · réservées
        aux bases : {resourceFormatBytes(state.reservedBytes)}
      </p>
    </div>
    <div class="web-table-wrap">
      <table>
        <thead
          ><tr><th>Base</th><th>Mode</th><th>Utilisée</th><th>Plafond actuel</th><th>Réservée</th><th>Réglage</th></tr
          ></thead
        ><tbody
          >{#each state.databases as row (row.id)}<tr
              ><td>{row.name}</td><td>{row.mode === 'fixed' ? 'Réservation fixe' : 'Allocation automatique'}</td><td
                >{row.usageBytes === null ? 'Sans mesure' : resourceFormatBytes(row.usageBytes)}</td
              ><td>{row.limitBytes === null ? 'Sans mesure' : resourceFormatBytes(row.limitBytes)}</td><td
                >{resourceFormatBytes(row.reservationMiB * 1024 ** 2)}</td
              ><td
                ><button type="button" class="web-button" onclick={() => edit(row)} disabled={!!busy}>Modifier</button
                ></td
              ></tr
            >{/each}</tbody
        >
      </table>
    </div>
    {#if editing}<form class="web-form" onsubmit={save}>
        <h3>{state.databases.find((row) => row.id === editing)?.name}</h3>
        <div class="web-form-grid">
          <label class="web-field"
            ><span>Mode</span><select bind:value={mode} disabled={!!busy}
              ><option value="adaptive">Allocation automatique</option><option value="fixed">Réservation fixe</option
              ></select
            ></label
          >{#if mode === 'fixed'}<label class="web-field"
              ><span>RAM réservée · Mio</span><input
                type="number"
                min="512"
                max="524288"
                step="64"
                required
                bind:value={maximumMiB}
                disabled={!!busy}
              /></label
            >{:else}<p class="web-help">
              La RAM augmente selon la charge, jusqu’à {resourceFormatBytes(state.adaptiveMaximumMiB * 1024 ** 2)},
              selon la capacité libre du serveur.
            </p>{/if}
        </div>
        <p class="web-help">
          1 024 Mio = 1 Gio. Le serveur vérifie la capacité et l’usage actuel avant de modifier la limite. Une baisse
          dangereuse est refusée.
        </p>
        <div class="web-actions">
          <button class="web-button web-button--primary" type="submit" disabled={!!busy}
            >{busy === 'memory' ? 'Enregistrement…' : 'Appliquer sans redémarrer'}</button
          ><button class="web-button" type="button" onclick={() => (editing = '')} disabled={!!busy}>Annuler</button>
        </div>
      </form>{/if}
  {:else}<p class="web-muted" role="status">Chargement des réglages du serveur…</p>{/if}
</details>

<style>
  .operator-controls {
    margin-block: 1rem;
    display: block;
  }
  summary {
    cursor: pointer;
    font-weight: 600;
  }
  .operator-controls[open] > :global(:not(summary)) {
    margin-top: 1rem;
  }
  h3 {
    font-size: 0.875rem;
    margin: 0 0 0.5rem;
  }
</style>
