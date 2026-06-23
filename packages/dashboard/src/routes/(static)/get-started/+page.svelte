<script lang="ts">
  import LoginForm from '$src/routes/(static)/get-started/LoginForm.svelte'
</script>

<svelte:head>
  <title>Connexion - Gestion PocketBase</title>
</svelte:head>

<div class="w-full flex items-center justify-center px-4 md:px-16 py-10 md:py-16">
  <div class="auth-card w-full max-w-md">
    <LoginForm allowRegister={false} />
  </div>
</div>
