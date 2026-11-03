<script lang="ts">
  import RecoveryNavigation from '$components/webApps/RecoveryNavigation.svelte'
  import WebProjectRecovery from '$components/webApps/WebProjectRecovery.svelte'
</script>

<svelte:head><title>Reprise des projets · Kolab Hosting</title></svelte:head>
<div class="recovery-projects">
  <div class="web-header">
    <div class="web-title">
      <span class="web-eyebrow">Protection des données</span>
      <h1>Reprise des projets</h1>
      <p class="web-subtitle">Reconstruire les copies et vérifier leurs dépendances ensemble.</p>
    </div>
  </div>
  <RecoveryNavigation active="projects" />
  <WebProjectRecovery />
</div>

<style>
  .recovery-projects {
    display: grid;
    gap: 1.2rem;
    min-width: 0;
  }
</style>
