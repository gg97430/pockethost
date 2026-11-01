<script lang="ts">
  import FeatureTab from '$components/FeatureTab.svelte'
  import InstancePrivateDatabaseAccess from '$components/InstancePrivateDatabaseAccess.svelte'
  import InstanceDeveloperSnippet from '../InstanceDeveloperSnippet.svelte'
  import { instance } from '../store'
</script>

<FeatureTab title="Utilisation">
  {#key $instance.id}
    <div class="instance-usage">
      <InstancePrivateDatabaseAccess instanceId={$instance.id} />
      <InstanceDeveloperSnippet instance={$instance} />
    </div>
  {/key}
</FeatureTab>

<style>
  .instance-usage {
    display: grid;
    gap: 1rem;
  }
</style>
