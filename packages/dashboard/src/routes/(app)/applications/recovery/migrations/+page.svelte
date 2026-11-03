<script lang="ts">
  import RecoveryNavigation from '$components/webApps/RecoveryNavigation.svelte'
  import ProjectMigration from '$components/webApps/ProjectMigration.svelte'
</script>

<svelte:head><title>Migration de projet · Kolab Hosting</title></svelte:head>
<div class="recovery-migrations">
  <div class="web-header">
    <div class="web-title">
      <span class="web-eyebrow">Déplacer un projet</span>
      <h1>Migration de projet</h1>
      <p class="web-subtitle">Examiner le plan, reconstruire la copie et préparer sa mise en service.</p>
    </div>
  </div>
  <RecoveryNavigation active="migrations" />
  <ProjectMigration />
</div>

<style>
  .recovery-migrations {
    display: grid;
    gap: 1.2rem;
    min-width: 0;
  }
</style>
