<script lang="ts">
  import { globalInstancesStore, userStore } from '$util/stores'
  import DashboardMetrics from './DashboardMetrics.svelte'
  import InstanceList from './InstanceList.svelte'

  $: instanceCount = Object.values($globalInstancesStore).length
  $: canCreate = ($userStore?.subscription_quantity ?? 0) > 0
</script>

<svelte:head>
  <title>Dashboard - Gestion PocketBase</title>
</svelte:head>

<header class="dashboard-head">
  <div>
    <h1 class="dashboard-title">Dashboard</h1>
    <p class="dashboard-subtitle">
      {#if instanceCount === 0}
        Créez une instance PocketBase pour commencer.
      {:else}
        {instanceCount} instance{instanceCount === 1 ? '' : 's'}. Les offres seront limitées par le stockage, pas par le nombre d'instances.
      {/if}
    </p>
  </div>

  {#if canCreate}
    <wa-button href="/instances/new" variant="brand" class="dashboard-new-btn">
      <wa-icon slot="start" name="plus"></wa-icon>
      Nouvelle instance
    </wa-button>
  {/if}
</header>

<DashboardMetrics />

<InstanceList />

<style>
  .dashboard-head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.75rem;
  }

  .dashboard-title {
    margin: 0;
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--app-text-strong);
  }

  @media (min-width: 768px) {
    .dashboard-title {
      font-size: 1.875rem;
    }
  }

  .dashboard-subtitle {
    margin: 0.35rem 0 0;
    max-width: 36rem;
    font-size: 0.875rem;
    line-height: 1.45;
    color: var(--app-text-muted);
  }

  .dashboard-new-btn {
    flex-shrink: 0;
  }
</style>
