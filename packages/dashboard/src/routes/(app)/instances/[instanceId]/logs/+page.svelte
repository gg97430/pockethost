<script lang="ts">
  import { assertExists } from 'pockethost/common'
  import FeatureTab from '$components/FeatureTab.svelte'
  import { instance } from '../store'
  import LoggingInner from './LoggingInner.svelte'

  assertExists($instance, `Expected instance here`)
  const { subdomain } = $instance
</script>

<svelte:head>
  <title>Logs de {subdomain} - Gestion PocketBase</title>
</svelte:head>

<FeatureTab title="Logs" documentation="/docs/logs">
  <svelte:fragment slot="summary">
    <p>
      Les logs de l'instance apparaissent ici en temps réel, y compris les <code>console.log</code> des hooks JavaScript.
    </p>
  </svelte:fragment>

  <LoggingInner />
</FeatureTab>
