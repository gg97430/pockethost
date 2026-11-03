<script lang="ts">
  import PlatformFleet from '$components/monitoring/PlatformFleet.svelte'
  import { userStore } from '$util/stores'
  import '$lib/webApps.css'
  $: canAccess = !!$userStore?.superAdmin && !$userStore?.suspension && !!$userStore?.verified
</script>

<svelte:head><title>Serveurs · Kolab Hosting</title></svelte:head>
<main class="webapps fleet-page">
  <div class="web-header">
    <div class="web-title">
      <span class="web-eyebrow">Exploitation</span>
      <h1>Les trois serveurs</h1>
      <p class="web-subtitle">Inventaire et capacité d’app3, app4 et app5 au même endroit.</p>
    </div>
  </div>
  {#if canAccess}<PlatformFleet />{:else}<p class="web-notice" role="alert">
      Cette page est réservée aux superadmins avec une adresse e-mail vérifiée.
    </p>{/if}
</main>

<style>
  .fleet-page {
    max-width: 1500px;
    margin: 0 auto;
    min-width: 0;
  }
</style>
