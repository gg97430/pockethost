<script lang="ts">
  import { browser } from '$app/environment'
  import { goto } from '$app/navigation'
  import { page } from '$app/stores'
  import MandatoryTotpGate from '$components/account/MandatoryTotpGate.svelte'
  import Navbar from '$src/routes/Navbar/Navbar.svelte'
  import VerifyAccountBar from '$components/VerifyAccountBar.svelte'
  import Meta from '$components/guards/Meta.svelte'
  import ThemeToggle from '$components/ThemeToggle.svelte'
  import '../app.css'
  import '$lib/webawesome'
  import { onMount } from 'svelte'
  import { init, isAuthStateInitialized, isUserLoggedIn } from '$util/stores'
  import { initTheme } from '$lib/theme'
  import MothershipStatus from './MothershipStatus.svelte'
  import { proseCodeBlocks } from '$lib/proseCodeBlocks'
  import a11yDark from 'svelte-highlight/styles/seti-ui'

  const blockedPublicPrefixes = ['/pricing', '/blog', '/docs', '/support', '/about', '/privacy', '/terms', '/3.0']

  onMount(() => {
    initTheme()
    init()
  })

  $: pathname = $page.url.pathname.replace(/\/$/, '') || '/'
  $: isAuthOnlyPage = pathname === '/' || pathname === '/login' || pathname.startsWith('/login/')
  $: isBlockedPublicPage = blockedPublicPrefixes.some((path) => pathname === path || pathname.startsWith(`${path}/`))
  $: if (browser && $isAuthStateInitialized && isBlockedPublicPage) {
    goto($isUserLoggedIn ? '/home' : '/login')
  }
</script>

<Meta />

<svelte:head>
  <title>Kolab Hosting</title>
  {@html a11yDark}
</svelte:head>

<MandatoryTotpGate>
  <div class="app-shell min-h-screen">
    {#if isAuthOnlyPage && !isBlockedPublicPage}
      <div class="auth-theme-toggle">
        <ThemeToggle />
      </div>
    {/if}

    {#if !isAuthOnlyPage && !isBlockedPublicPage}<Navbar />{/if}

    {#if !isBlockedPublicPage}
      <div class="w-full" class:workspace-main={!isAuthOnlyPage} use:proseCodeBlocks>
        {#if !isAuthOnlyPage}
          <MothershipStatus />
          <div class="workspace-notices"><VerifyAccountBar /></div>
        {/if}
        <slot />
      </div>
    {/if}
  </div>
</MandatoryTotpGate>

<style>
  .workspace-main {
    min-width: 0;
    min-height: 100vh;
    margin-left: 224px;
    padding-top: 56px;
    width: calc(100% - 224px);
  }
  .workspace-notices {
    padding: 0 24px;
  }
  @media (max-width: 767px) {
    .workspace-main {
      margin-left: 0;
      width: 100%;
    }
    .workspace-notices {
      padding: 0 16px;
    }
  }
  .auth-theme-toggle {
    position: fixed;
    top: 1rem;
    right: 1rem;
    z-index: 50;
  }
</style>
