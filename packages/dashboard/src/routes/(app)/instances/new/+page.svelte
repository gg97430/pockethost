<script lang="ts">
  import { client } from '$src/pocketbase-client'
  import {
    globalInstancesStore,
    globalInstancesStoreReady,
    isWorkspaceAccessAllowed,
    versions,
    userStore,
  } from '$util/stores'
  import Creator from './Creator.svelte'
  import Paywall from './Paywall.svelte'

  let refreshing = false
  let catalogError = ''
  $: maxInstances = $userStore?.subscription_quantity || 0
  $: instanceCount = Object.values($globalInstancesStore).filter((instance) => instance.uid === $userStore?.id).length
  $: quotaReached = !$userStore?.superAdmin && instanceCount >= maxInstances
  $: loading = !$isWorkspaceAccessAllowed || !$globalInstancesStoreReady
  const reloadVersions = async () => {
    refreshing = true
    catalogError = ''
    try {
      const result = await client().client.send<{ versions: string[] }>('/api/versions', { requestKey: null })
      versions.set(result.versions)
      if (!result.versions.length) catalogError = 'Le catalogue doit être synchronisé par un administrateur.'
    } catch {
      catalogError = 'Le catalogue des versions est indisponible. Réessayez.'
    } finally {
      refreshing = false
    }
  }
</script>

<svelte:head><title>Nouvelle base - Kolab Hosting</title></svelte:head>

<div class="flex items-center justify-center pt-14">
  <div class="max-w-md px-4 w-full">
    {#if loading}
      <p role="status">Chargement de votre compte…</p>
    {:else if quotaReached}
      <Paywall />
    {:else if !$versions.length}
      <wa-card>
        <div class="wa-card-body grid gap-3">
          <h2 class="text-xl font-bold">Versions PocketBase indisponibles</h2>
          <p>Le catalogue des versions n’est pas encore prêt. Votre quota de bases n’est pas en cause.</p>
          {#if catalogError}<p role="alert">{catalogError}</p>{/if}
          <wa-button variant="brand" loading={refreshing} disabled={refreshing} onclick={reloadVersions}
            >Actualiser les versions</wa-button
          >
        </div>
      </wa-card>
    {:else}
      <Creator />
    {/if}
  </div>
</div>
