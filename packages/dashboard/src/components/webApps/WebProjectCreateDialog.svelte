<script lang="ts">
  import { webError } from '$lib/webApps'
  import { webProjectCreate } from '$lib/webProjectGroupsApi'
  import type { WebProjectGroupProject } from '../../../../pockethost/src/common/webProjectGroups'
  export let open = false
  /** Appelé après la création (le dialogue est déjà fermé). */
  export let oncreated: (project: WebProjectGroupProject) => void | Promise<void> = () => {}

  let name = '',
    busy = false,
    error = ''

  const close = () => {
    if (busy) return
    open = false
    name = ''
    error = ''
  }
  const submit = async (event?: Event) => {
    event?.preventDefault()
    if (busy || !name.trim()) return
    busy = true
    error = ''
    try {
      const { project } = await webProjectCreate(name.trim())
      busy = false
      close()
      await oncreated(project)
    } catch (e) {
      error = webError(e)
    } finally {
      busy = false
    }
  }
</script>

<wa-dialog
  label="Nouveau projet"
  {open}
  onwa-hide={(event: Event) => {
    // Pendant la création, Échap ou un clic hors du dialogue ne le ferment pas.
    if (busy) event.preventDefault()
    else close()
  }}
>
  <!-- Un seul champ : Entrée soumet le formulaire sans bouton d'envoi interne. -->
  <form class="web-form" onsubmit={submit}>
    <label class="web-field"
      ><span>Nom du projet</span><input
        bind:value={name}
        maxlength="80"
        required
        placeholder="Client Avenir"
        disabled={busy}
        autocomplete="off"
      /></label
    >
    <p class="web-help">Un projet regroupe des sites web. Ses membres obtiennent leurs droits sur tous ses sites.</p>
    {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  </form>
  <wa-button slot="footer" variant="neutral" onclick={close} disabled={busy}>Annuler</wa-button>
  <wa-button slot="footer" variant="brand" onclick={() => submit()} disabled={busy || !name.trim()}
    >{busy ? 'Création…' : 'Créer le projet'}</wa-button
  >
</wa-dialog>
