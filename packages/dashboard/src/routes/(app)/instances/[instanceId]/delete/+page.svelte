<script lang="ts">
  import { goto } from '$app/navigation'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { globalInstancesStore } from '$util/stores'
  import { instance } from '../store'
  import { isInstanceFullyOff } from '$util/instancePower'

  $: ({ id, subdomain } = $instance)
  $: isFullyOff = isInstanceFullyOff($instance)

  let isButtonDisabled = false
  let errorMessage = ''

  const deletedPaths = ['pb_data/*', 'pb_public/*', 'pb_migrations/*', 'pb_static/*']

  const handleSave = async (e: Event) => {
    e.preventDefault()

    if (!isFullyOff) return

    isButtonDisabled = true

    const confirmed = confirm(
      `DERNIÈRE CHANCE - Voulez-vous vraiment supprimer cette instance ? Votre base de données, tous les fichiers locaux, les logs et le sous-domaine seront perdus.`
    )

    if (confirmed) {
      errorMessage = ''
      client()
        .deleteInstance({ id })
        .then(() => {
          globalInstancesStore.update((instances) => {
            const newInstances = { ...instances }
            delete newInstances[id]
            return newInstances
          })
          goto('/dashboard')
        })
        .catch((error) => {
          console.error(error)
          errorMessage = error.data.message || error.message
        })
    }

    isButtonDisabled = false
  }
</script>

<FeatureTab
  title="Supprimer l'instance"
  documentation="/docs/delete"
  powerOffMessage="L'instance doit être éteinte avant la suppression."
  {errorMessage}
>
  <svelte:fragment slot="summary">
    <p>La suppression de votre instance est immédiate et définitive. Tout ce qui suit sera supprimé.</p>
  </svelte:fragment>

  <wa-card class="wa-card-danger">
    <div class="wa-card-body wa-card-body--lg wa-stack-lg">
      <div>
        <p class="text-xs font-medium uppercase tracking-wide text-white/50 mb-3">Ce qui sera supprimé</p>
        <ul class="space-y-2">
          <li class="rounded-lg bg-white/5 border border-white/5 px-4 py-3 text-sm text-white">
            <span class="text-white/50">Sous-domaine</span>
            <span class="mx-2 text-white/30">·</span>
            <span class="font-medium">{subdomain}</span>
          </li>
          {#each deletedPaths as path}
            <li class="rounded-lg bg-error/5 border border-error/10 px-4 py-3">
              <code class="font-mono text-sm text-error/90">{path}</code>
            </li>
          {/each}
        </ul>
      </div>

      <p class="text-sm text-white/50 leading-relaxed">
        Si vous stockez des fichiers sur S3, vous devez les supprimer séparément.
      </p>

      <div class="pt-2 border-t border-white/10">
        <form onsubmit={handleSave}>
          <wa-button type="submit" variant="danger" disabled={!isFullyOff || isButtonDisabled}>
            <wa-icon slot="start" name="trash"></wa-icon>
            Supprimer l'instance
          </wa-button>
        </form>
      </div>
    </div>
  </wa-card>
</FeatureTab>
