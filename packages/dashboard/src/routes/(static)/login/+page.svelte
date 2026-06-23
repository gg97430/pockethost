<script lang="ts">
  import { browser } from '$app/environment'
  import { goto } from '$app/navigation'
  import { page } from '$app/stores'
  import { isAuthStateInitialized, isUserLoggedIn } from '$util/stores'
  import LoginForm from '../get-started/LoginForm.svelte'

  $: emailChanged = browser && $page.url.searchParams.get('emailChanged') === '1'
  $: if (browser && $isAuthStateInitialized && $isUserLoggedIn) {
    goto('/dashboard')
  }
</script>

<div class="w-full min-h-screen flex flex-col items-center justify-center px-4 py-8">
  {#if emailChanged}
    <div class="w-full max-w-md mb-4">
      <wa-callout variant="success" class="wa-callout-padded">
        Email mis à jour. Connectez-vous avec votre nouvelle adresse.
      </wa-callout>
    </div>
  {/if}
  <div class="auth-card w-full max-w-md">
    <LoginForm allowRegister={false} />
  </div>
</div>
