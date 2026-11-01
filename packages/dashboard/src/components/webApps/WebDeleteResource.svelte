<script lang="ts">
  import { webError, webRequest } from '$lib/webApps'
  export let kind: 'app' | 'project' = 'app'
  export let resourceId: string
  export let name: string
  export let siteCount = 0
  export let blocked = false
  export let onaccepted: () => void | Promise<void> = () => {}
  export let oncancel: (() => void) | undefined = undefined
  let confirmation = '',
    acknowledged = false,
    busy = false,
    error = ''
  const submit = async (event: SubmitEvent) => {
    event.preventDefault()
    if (busy || blocked || confirmation !== name || !acknowledged) return
    busy = true
    error = ''
    try {
      await webRequest(kind === 'project' ? `/projects/${resourceId}` : `/${resourceId}`, {
        method: 'DELETE',
        body: { confirmation, deleteContents: true },
      })
      await onaccepted()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = false
    }
  }
</script>

<section
  class="web-panel deletion-panel"
  aria-label={kind === 'project' ? 'Supprimer le projet' : 'Supprimer l’application'}
>
  <div class="deletion-heading">
    <span class="deletion-mark" aria-hidden="true">!</span>
    <div>
      <h2>Supprimer {kind === 'project' ? 'le projet' : 'l’application'}</h2>
      <p class="web-help">Cette action est définitive.</p>
    </div>
  </div>
  <p>
    {#if kind === 'project'}Le projet « {name} » et tous ses sites et environnements seront supprimés{siteCount
        ? ` (${siteCount} actuellement)`
        : ''}.
    {:else}L’application « {name} » sera retirée de l’hébergement.{/if}
    Les services seront arrêtés, puis les domaines, variables, journaux, volumes et sauvegardes associés seront supprimés.
  </p>
  <p class="web-help">Le dépôt Git et les bases de données externes sont conservés.</p>
  {#if blocked}<p class="web-alert" role="status">
      Attendez la fin du déploiement ou annulez-le avant de supprimer cette application.
    </p>{/if}
  <form class="web-form" onsubmit={submit}>
    <label class="web-field"
      ><span>Pour confirmer, saisissez <strong>{name}</strong></span>
      <input
        bind:value={confirmation}
        autocomplete="off"
        spellcheck="false"
        disabled={busy || blocked}
        required
        aria-label="Nom exact à confirmer"
      />
    </label>
    <label class="web-check"
      ><input type="checkbox" bind:checked={acknowledged} disabled={busy || blocked} />
      Je confirme la suppression des données hébergées et des sauvegardes associées.
    </label>
    {#if error}<p class="web-alert web-alert--error" role="alert">{error}</p>{/if}
    <div class="web-actions">
      <button
        type="submit"
        class="web-button web-button--danger"
        disabled={busy || blocked || confirmation !== name || !acknowledged}
      >
        {busy
          ? 'Demande en cours…'
          : kind === 'project'
            ? 'Supprimer le projet et ses sites'
            : 'Supprimer définitivement l’application'}
      </button>
      {#if oncancel}<button class="web-button" type="button" onclick={oncancel} disabled={busy}>Annuler</button>{/if}
    </div>
    <p class="web-help">
      Le nettoyage se poursuit en arrière-plan. Son état reste visible dans la liste des applications.
    </p>
  </form>
</section>

<style>
  .deletion-panel {
    border-color: color-mix(in srgb, var(--app-danger, #f87171) 35%, var(--web-border));
    max-width: 48rem;
  }
  .deletion-heading {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .deletion-heading > div {
    display: grid;
    gap: 0.25rem;
  }
  .deletion-mark {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border: 1px solid color-mix(in srgb, var(--app-danger, #f87171) 40%, transparent);
    border-radius: 6px;
    color: var(--app-danger, #f87171);
    font-weight: 650;
  }
  .web-button--danger:not(:disabled) {
    border-color: color-mix(in srgb, var(--app-danger, #f87171) 45%, var(--web-border));
  }
</style>
