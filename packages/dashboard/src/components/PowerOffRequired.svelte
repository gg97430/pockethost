<script lang="ts">
  import AlertBar from '$components/AlertBar.svelte'
  import { instance } from '$src/routes/(app)/instances/[instanceId]/store'
  import { isInstanceShuttingDown } from '$util/instancePower'

  interface Props {
    action?: string
    poweredOffMessage?: string
  }

  let {
    action,
    poweredOffMessage = action
      ? `Votre instance doit être éteinte pour ${action}.`
      : "Votre instance doit d'abord être éteinte.",
  }: Props = $props()

  const power = $derived($instance?.power ?? false)
  const isShuttingDown = $derived($instance ? isInstanceShuttingDown($instance) : false)
</script>

{#if power && !isShuttingDown}
  <AlertBar message={poweredOffMessage} type="error" />
{:else if isShuttingDown}
  <AlertBar message="L'instance est en cours d'arrêt. Attendez qu'elle soit complètement arrêtée." type="warning" />
{/if}
