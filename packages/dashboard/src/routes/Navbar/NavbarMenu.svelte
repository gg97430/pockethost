<script lang="ts">
  import { client } from '$src/pocketbase-client'
  import UserLoggedIn from '$components/guards/UserLoggedIn.svelte'
  import UserLoggedOut from '$components/guards/UserLoggedOut.svelte'
  import ThemeToggle from '$components/ThemeToggle.svelte'
  import { userStore } from '$util/stores'
  import Avatar from './Avatar.svelte'

  const handleLogoutAndRedirect = async () => {
    const { logOut } = client()
    logOut()
    window.location.href = '/login'
  }

  export let isCollapsed = false
</script>

{#if isCollapsed}
  <div class="nav-menu-panel flex flex-col gap-1 rounded-lg p-2 shadow-xl w-52 z-[100] mt-3">
    <ThemeToggle showLabel />
    <UserLoggedIn>
      <a href="/dashboard" class="site-nav-link site-nav-link--menu">Dashboard</a>
      <a href="/instances/new" class="site-nav-link site-nav-link--menu">Nouvelle instance</a>
      {#if $userStore?.superAdmin}
        <a href="/admin" class="site-nav-link site-nav-link--menu">Administration</a>
      {/if}
      <a href="/account" class="site-nav-link site-nav-link--menu">Compte</a>
      <button type="button" class="site-nav-link site-nav-link--menu text-left" onclick={handleLogoutAndRedirect}>
        Déconnexion
      </button>
    </UserLoggedIn>
    <UserLoggedOut>
      <a href="/login" class="site-nav-link site-nav-link--menu">Connexion</a>
    </UserLoggedOut>
  </div>
{:else}
  <nav class="site-nav flex items-center gap-1">
    <ThemeToggle />
    <UserLoggedIn>
      <a href="/dashboard" class="site-nav-link">Dashboard</a>
      <a href="/instances/new" class="site-nav-link">Nouvelle instance</a>
      {#if $userStore?.superAdmin}
        <a href="/admin" class="site-nav-link">Administration</a>
      {/if}
    </UserLoggedIn>
    <UserLoggedIn>
      <wa-dropdown placement="bottom-end" class="nav-user-menu ml-1">
        <button slot="trigger" type="button" class="nav-user-menu-trigger" aria-label="Menu du compte">
          <Avatar size={32} />
        </button>
        <wa-dropdown-item>
          <a href="/account">Paramètres</a>
        </wa-dropdown-item>
        <wa-dropdown-item>
          <button type="button" onclick={handleLogoutAndRedirect}>Déconnexion</button>
        </wa-dropdown-item>
      </wa-dropdown>
    </UserLoggedIn>
    <UserLoggedOut>
      <a href="/login" class="site-nav-link site-nav-link--login">Connexion</a>
    </UserLoggedOut>
  </nav>
{/if}

<style>
  :global(wa-dropdown.nav-user-menu) {
    display: inline-flex;
    align-items: center;
    vertical-align: middle;
  }

  .nav-user-menu-trigger {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    padding: 0;
    margin: 0;
    border: none;
    background: transparent;
    cursor: pointer;
    line-height: 0;
    border-radius: 9999px;
    flex-shrink: 0;
  }

  .nav-user-menu-trigger:focus-visible {
    outline: 2px solid rgb(30 184 84 / 0.8);
    outline-offset: 2px;
  }
</style>
