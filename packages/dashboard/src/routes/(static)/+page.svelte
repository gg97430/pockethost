<script lang="ts">
  import { browser } from '$app/environment'
  import { goto } from '$app/navigation'
  import { isAuthStateInitialized, isUserLoggedIn } from '$util/stores'
  import LoginForm from './get-started/LoginForm.svelte'

  $: if (browser && $isAuthStateInitialized && $isUserLoggedIn) {
    goto('/dashboard')
  }
</script>

<svelte:head>
  <title>Connexion - Gestion PocketBase</title>
</svelte:head>

<main class="auth-only-page">
  <div class="auth-card w-full max-w-md">
    <LoginForm allowRegister={false} />
  </div>
</main>

<style>
  .auth-only-page {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;
  }
</style>
