<script lang="ts">
  import FeatureTab from '$components/FeatureTab.svelte'
  import { client } from '$src/pocketbase-client'
  import { instance } from '../store'
  import Toggle from '../Toggle.svelte'
  import { isInstanceFullyOff, isInstanceShuttingDown } from '$util/instancePower'

  const { updateInstance } = client()

  $: ({ id, dev } = $instance)
  $: isFullyOff = isInstanceFullyOff($instance)
  $: isShuttingDown = isInstanceShuttingDown($instance)

  let errorMessage = ''

  const handleChange = (isChecked: boolean) => {
    if (!isFullyOff) return

    updateInstance({ id, fields: { dev: isChecked } })
      .then(() => 'saved')
      .catch((error) => {
        errorMessage = error.data.message || error.message
      })
  }
</script>

<FeatureTab title="Mode dev" documentation="/docs/dev-mode" powerOffAction="modifier le mode dev" {errorMessage}>
  <svelte:fragment slot="summary">
    <p>
      À partir de PocketBase v0.20.1, votre instance affiche les sorties de débogage dans les logs. Les performances
      sont réduites lorsque le mode dev est actif.
    </p>
  </svelte:fragment>

  <wa-card class="border border-white/10 bg-[#111111]/80 shadow-lg overflow-hidden">
    <div class="wa-card-body wa-card-body--lg">
      <Toggle
        onChange={handleChange}
        checked={!!dev}
        onClass="warning"
        disabled={!isFullyOff}
        loading={isShuttingDown}
      />
    </div>
  </wa-card>
</FeatureTab>
