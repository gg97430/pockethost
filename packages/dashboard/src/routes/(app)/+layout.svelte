<script>
  import UserLoggedIn from '$components/guards/UserLoggedIn.svelte'
  import UserLoggedOut from '$components/guards/UserLoggedOut.svelte'
  import { userStore } from '$util/stores'

  $: maxInstances = $userStore?.subscription_quantity
  $: isSuperAdmin = !!$userStore?.superAdmin
</script>

<div class="app-content-shell">
  <UserLoggedIn>
    {#if maxInstances === 0 && !isSuperAdmin}
      <wa-callout variant="warning" class="wa-callout-padded py-2 mt-2">
        <span class="flex-1 text-sm flex text-white text-start items-center justify-start gap-4">
          Ce compte n'a pas encore de quota d'instances. Ajustez `subscription_quantity` dans l'admin PocketBase.
        </span>
      </wa-callout>
    {/if}
    <slot />
  </UserLoggedIn>
  <UserLoggedOut>
    <div class="flex flex-col items-center justify-center py-16 md:py-24 text-center">
      <div class="mb-5 flex h-14 w-14 items-center justify-center rounded-full border border-white/10 bg-white/5">
        <wa-icon name="lock" class="text-xl text-neutral-400"></wa-icon>
      </div>
      <h2 class="mb-2 text-lg font-semibold text-white">Connexion requise</h2>
      <p class="mb-6 max-w-sm text-sm text-neutral-400">
        Connectez-vous pour gérer vos instances PocketBase et les paramètres de votre compte.
      </p>
      <wa-button href="/login" variant="brand">Connexion</wa-button>
    </div>
  </UserLoggedOut>
</div>

<style>
  .app-content-shell {
    position: relative;
    z-index: 10;
    width: min(100%, 110rem);
    margin-inline: auto;
    padding: 1.5rem 1rem 0;
  }

  @media (min-width: 768px) {
    .app-content-shell {
      padding-top: 2.5rem;
      padding-inline: clamp(2rem, 4vw, 5rem);
    }
  }
</style>
