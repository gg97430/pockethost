<script lang="ts">
  import { browser } from '$app/environment'
  import { goto } from '$app/navigation'
  import { page } from '$app/stores'
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
    goto($isUserLoggedIn ? '/dashboard' : '/login')
  }
</script>

<Meta />

<svelte:head>
  <title>Gestion PocketBase</title>
  {@html a11yDark}
</svelte:head>

<div class="app-shell min-h-screen">
  {#if isAuthOnlyPage && !isBlockedPublicPage}
    <div class="auth-theme-toggle">
      <ThemeToggle />
    </div>
  {/if}

  {#if !isAuthOnlyPage && !isBlockedPublicPage}
    <MothershipStatus />
    <Navbar />

    <div class="px-4 md:px-20">
      <VerifyAccountBar />
    </div>
  {/if}

  {#if !isBlockedPublicPage}
    <div class="w-full" use:proseCodeBlocks>
      <slot />
    </div>
  {/if}
</div>

<style>
  .auth-theme-toggle {
    position: fixed;
    top: 1rem;
    right: 1rem;
    z-index: 50;
  }
</style>
