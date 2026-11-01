<script lang="ts">
  import { afterNavigate } from '$app/navigation'
  import { page } from '$app/stores'
  import { onMount, tick } from 'svelte'
  import { userStore } from '$util/stores'
  import { client } from '$src/pocketbase-client'
  import ThemeToggle from '$components/ThemeToggle.svelte'
  import Logo from './Logo.svelte'
  import Avatar from './Avatar.svelte'
  import { workspaceFeatureNavigation } from '$lib/dashboard/workspaceNavigation'
  import { webNavStore } from '$lib/webAccessStore'
  import { PUBLIC_SERVER_IP } from '$lib/appEnv'

  let drawerOpen = false
  let isMobile = false
  let sidebar: HTMLElement
  let menuButton: HTMLButtonElement
  let ipCopied = false
  let ipCopyMessage = ''
  let ipCopyTimer: ReturnType<typeof setTimeout> | undefined
  $: pathname = $page.url.pathname
  $: section = pathname.startsWith('/applications')
    ? 'Applications web'
    : pathname.startsWith('/monitoring')
      ? 'Supervision'
      : pathname.startsWith('/admin')
        ? 'Administration'
        : pathname.startsWith('/activity')
          ? 'Journal d’administration'
          : pathname.startsWith('/account')
            ? 'Mon compte'
            : 'Bases de données'
  $: detail =
    pathname === '/instances/new'
      ? 'Nouvelle base'
      : pathname.startsWith('/instances/')
        ? 'Instance'
        : pathname === '/applications/settings'
          ? 'Configuration'
          : pathname === '/applications/recovery'
            ? 'Reprise du serveur'
            : pathname.match(/^\/applications\/[^/]+/)
              ? 'Application'
              : ''

  afterNavigate(() => {
    drawerOpen = false
  })
  onMount(() => {
    const media = matchMedia('(max-width: 767px)')
    const update = () => {
      isMobile = media.matches
      if (!isMobile) drawerOpen = false
    }
    update()
    media.addEventListener('change', update)
    return () => {
      media.removeEventListener('change', update)
      if (ipCopyTimer) clearTimeout(ipCopyTimer)
    }
  })
  const copyServerIp = async () => {
    if (ipCopyTimer) clearTimeout(ipCopyTimer)
    try {
      await navigator.clipboard.writeText(PUBLIC_SERVER_IP)
      ipCopied = true
      ipCopyMessage = 'IP publique copiée.'
    } catch {
      ipCopied = false
      ipCopyMessage = `Copie impossible. IP publique : ${PUBLIC_SERVER_IP}`
    }
    ipCopyTimer = setTimeout(() => {
      ipCopied = false
      ipCopyMessage = ''
    }, 2500)
  }
  const openMenu = async () => {
    drawerOpen = true
    await tick()
    sidebar?.querySelector<HTMLAnchorElement>('nav a')?.focus()
  }
  const closeMenu = () => {
    drawerOpen = false
    menuButton?.focus()
  }
  const logout = () => {
    client().logOut()
    window.location.href = '/login'
  }
  const handleKeys = (event: KeyboardEvent) => {
    if (!isMobile || !drawerOpen) return
    if (event.key === 'Escape') {
      event.preventDefault()
      closeMenu()
    }
    if (event.key !== 'Tab') return
    const nodes = Array.from(sidebar?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])') || [])
    const first = nodes[0],
      last = nodes[nodes.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first?.focus()
    }
  }
</script>

<svelte:window onkeydown={handleKeys} />

{#if drawerOpen && isMobile}
  <button class="workspace-backdrop" type="button" onclick={closeMenu} aria-label="Fermer la navigation"></button>
{/if}

<aside
  bind:this={sidebar}
  class="workspace-sidebar"
  class:workspace-sidebar--open={drawerOpen}
  inert={isMobile && !drawerOpen}
  aria-label="Navigation principale"
>
  <a class="workspace-brand" href="/dashboard" aria-label="Kolab Hosting — Mes bases"><Logo /></a>
  <div class="workspace-server">
    <span class="workspace-server-icon"><wa-icon name="server"></wa-icon></span>
    <div><strong>Mon espace</strong><span>{$page.url.hostname}</span></div>
    <wa-icon name="chevron-down"></wa-icon>
  </div>
  <nav class="workspace-nav">
    <span class="workspace-nav-label">Plateforme</span>
    <a
      href="/dashboard"
      class:active={pathname === '/dashboard' || (pathname.startsWith('/instances/') && pathname !== '/instances/new')}
      aria-current={pathname === '/dashboard' ? 'page' : undefined}
      ><wa-icon name="database"></wa-icon>Bases de données</a
    >
    {#if $webNavStore}
      <a
        href="/applications"
        data-sveltekit-preload-code="hover"
        class:active={pathname.startsWith('/applications')}
        aria-current={pathname === '/applications' ? 'page' : undefined}
        ><wa-icon name="layer-group"></wa-icon>Applications web</a
      >
    {/if}
    {#if $userStore?.superAdmin}
      <a
        href="/monitoring"
        data-sveltekit-preload-code="hover"
        class:active={pathname.startsWith('/monitoring')}
        aria-current={pathname === '/monitoring' ? 'page' : undefined}
        ><wa-icon name="heart-pulse"></wa-icon>Supervision</a
      >
    {/if}
    <a
      href="/instances/new"
      class:active={pathname === '/instances/new'}
      aria-current={pathname === '/instances/new' ? 'page' : undefined}><wa-icon name="plus"></wa-icon>Nouvelle base</a
    >
    {#if $workspaceFeatureNavigation}
      <div class="workspace-feature-context">
        <wa-icon name="database"></wa-icon><span>{$workspaceFeatureNavigation.title}</span>
      </div>
      {#each $workspaceFeatureNavigation.sections as group}
        {#if group.title}<span class="workspace-nav-label workspace-feature-label">{group.title}</span>{/if}
        {#each group.items as item (item.href)}
          <a
            href={item.href}
            class:active={item.isActive}
            class:workspace-link-danger={item.variant === 'danger'}
            aria-current={item.isActive ? 'page' : undefined}
            target={item.external ? '_blank' : undefined}
            rel={item.external ? 'noreferrer' : undefined}
          >
            {#if item.imageSrc}<img src={item.imageSrc} alt="" />{:else if item.icon}<wa-icon name={item.icon}
              ></wa-icon>{/if}
            <span class="workspace-nav-text">{item.label}</span>{#if item.external}<wa-icon
                class="workspace-external"
                name="arrow-up-right-from-square"
              ></wa-icon>{/if}
          </a>
        {/each}
      {/each}
    {/if}
    <span class="workspace-nav-label workspace-nav-label--spaced">Gestion</span>
    {#if $userStore?.superAdmin}
      <a
        href="/admin"
        class:active={pathname.startsWith('/admin')}
        aria-current={pathname === '/admin' ? 'page' : undefined}><wa-icon name="sliders"></wa-icon>Administration</a
      >
    {/if}
    <a
      href="/activity"
      class:active={pathname.startsWith('/activity')}
      aria-current={pathname === '/activity' ? 'page' : undefined}
      ><wa-icon name="clock-rotate-left"></wa-icon>Journal d’administration</a
    >
    <a
      href="/account"
      class:active={pathname.startsWith('/account')}
      aria-current={pathname.startsWith('/account') ? 'page' : undefined}><wa-icon name="gear"></wa-icon>Mon compte</a
    >
  </nav>
  <div class="workspace-sidebar-foot">
    <wa-icon name="database"></wa-icon><span>Kolab Hosting</span><span class="workspace-foot-dot">·</span><span
      >Console</span
    >
  </div>
</aside>

<header class="workspace-topbar">
  <div class="workspace-context">
    <button
      bind:this={menuButton}
      class="workspace-menu-button"
      type="button"
      onclick={openMenu}
      aria-label="Ouvrir la navigation"
      aria-expanded={drawerOpen}><wa-icon name="bars"></wa-icon></button
    >
    <span class="workspace-context-root">Plateforme</span><span class="workspace-context-separator">/</span><strong
      >{section}</strong
    >
    {#if detail}<span class="workspace-context-separator">/</span><span class="workspace-context-detail">{detail}</span
      >{/if}
  </div>
  <div class="workspace-account-tools">
    {#if PUBLIC_SERVER_IP}
      <button
        type="button"
        class="workspace-public-ip"
        onclick={copyServerIp}
        title="Copier l’IP publique du serveur"
        aria-label={ipCopied ? 'IP publique copiée' : `Copier l’IP publique du serveur : ${PUBLIC_SERVER_IP}`}
      >
        <span class="workspace-public-ip-label">IP publique</span>
        <span class="workspace-public-ip-value">{PUBLIC_SERVER_IP}</span>
        <wa-icon name={ipCopied ? 'check' : 'copy'} aria-hidden="true"></wa-icon>
      </button>
      <span class="sr-only" role="status">{ipCopyMessage}</span>
    {/if}
    <ThemeToggle />
    {#if $userStore}
      <wa-dropdown placement="bottom-end" class="nav-user-menu">
        <button slot="trigger" type="button" class="workspace-account-trigger" aria-label="Ouvrir le menu du compte"
          ><Avatar size={26} /><wa-icon name="chevron-down"></wa-icon></button
        >
        <div class="workspace-account-email">{$userStore.email}</div>
        <wa-dropdown-item><a href="/account">Paramètres du compte</a></wa-dropdown-item>
        <wa-dropdown-item><a href="/activity">Journal d’administration</a></wa-dropdown-item>
        <wa-dropdown-item><button type="button" onclick={logout}>Déconnexion</button></wa-dropdown-item>
      </wa-dropdown>
    {:else}
      <a href="/login" class="workspace-login">Connexion</a>
    {/if}
  </div>
</header>

<style>
  .workspace-sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 70;
    display: flex;
    flex-direction: column;
    width: 224px;
    border-right: 1px solid var(--app-border);
    background: var(--app-sidebar);
    color: var(--app-text);
  }
  .workspace-brand {
    display: flex;
    align-items: center;
    height: 56px;
    min-height: 56px;
    padding: 0 20px;
    border-bottom: 1px solid var(--app-border);
    text-decoration: none;
  }
  .workspace-server {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 16px 12px 17px;
    padding: 10px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    background: var(--app-surface);
  }
  .workspace-server-icon {
    display: grid;
    place-items: center;
    width: 29px;
    height: 29px;
    flex: none;
    border: 1px solid var(--app-border);
    border-radius: 5px;
    color: var(--app-text-muted);
    background: var(--app-bg);
  }
  .workspace-server div {
    min-width: 0;
    flex: 1;
  }
  .workspace-server strong {
    display: block;
    font-size: 12px;
    font-weight: 600;
    line-height: 1.5;
  }
  .workspace-server div span {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 10px;
    color: var(--app-text-faint);
    white-space: nowrap;
  }
  .workspace-server > wa-icon {
    font-size: 9px;
    color: var(--app-text-faint);
  }
  .workspace-nav {
    flex: 1;
    overflow: auto;
    padding: 0 10px;
  }
  .workspace-nav-label {
    display: block;
    padding: 0 10px 8px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--app-text-faint);
  }
  .workspace-nav-label--spaced {
    padding-top: 24px;
  }
  .workspace-nav a {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 35px;
    margin: 2px 0;
    padding: 7px 10px;
    border: 1px solid transparent;
    border-radius: 5px;
    color: var(--app-text-muted);
    font-size: 12px;
    font-weight: 500;
    line-height: 1.5;
    text-decoration: none;
    transition:
      background 120ms,
      color 120ms;
  }
  .workspace-nav a wa-icon {
    width: 15px;
    font-size: 14px;
    color: var(--app-text-faint);
  }
  .workspace-nav a:hover {
    background: var(--app-surface-hover);
    color: var(--app-text-strong);
  }
  .workspace-nav a.active {
    border-color: var(--app-accent-border);
    background: var(--app-accent-soft);
    color: var(--app-accent-text);
  }
  .workspace-nav a.active wa-icon {
    color: var(--app-accent-text);
  }
  .workspace-nav a:focus-visible {
    outline: 2px solid var(--app-accent);
    outline-offset: 1px;
  }
  .workspace-sidebar-foot {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 17px 20px;
    border-top: 1px solid var(--app-border);
    font-size: 10px;
    color: var(--app-text-faint);
  }
  .workspace-sidebar-foot wa-icon {
    font-size: 12px;
  }
  .workspace-foot-dot {
    margin-left: auto;
  }
  .workspace-topbar {
    position: fixed;
    inset: 0 0 auto 224px;
    z-index: 60;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    height: 56px;
    padding: 0 24px;
    border-bottom: 1px solid var(--app-border);
    background: var(--app-sidebar);
  }
  .workspace-context {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    font-size: 12px;
  }
  .workspace-context-root {
    color: var(--app-text-faint);
  }
  .workspace-context strong {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    font-weight: 500;
    white-space: nowrap;
  }
  .workspace-context-separator {
    color: var(--app-border-strong);
  }
  .workspace-context-detail {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--app-text-muted);
  }
  .workspace-menu-button {
    display: none;
  }
  .workspace-backdrop {
    position: fixed;
    inset: 0;
    z-index: 65;
    border: 0;
    border-radius: 0;
    background: rgb(0 0 0 / 0.65);
  }
  .workspace-account-tools {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
  }
  .workspace-public-ip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 32px;
    padding: 5px 9px;
    border: 1px solid var(--app-border);
    border-radius: 5px;
    background: var(--app-surface);
    color: var(--app-text);
    white-space: nowrap;
    cursor: pointer;
  }
  .workspace-public-ip:hover {
    border-color: var(--app-border-strong);
    background: var(--app-surface-hover);
  }
  .workspace-public-ip:focus-visible {
    outline: 2px solid var(--app-accent);
    outline-offset: 3px;
  }
  .workspace-public-ip-label {
    font-size: 11px;
    color: var(--app-text-muted);
  }
  .workspace-public-ip-value {
    font-family: ui-monospace, SFMono-Regular, 'SF Mono', Consolas, monospace;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .workspace-public-ip wa-icon {
    font-size: 12px;
    color: var(--app-text-muted);
  }
  .workspace-account-trigger {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--app-text-faint);
    cursor: pointer;
  }
  .workspace-account-trigger wa-icon {
    font-size: 9px;
  }
  .workspace-account-trigger:focus-visible {
    outline: 2px solid var(--app-accent);
    outline-offset: 4px;
    border-radius: 4px;
  }
  .workspace-account-email {
    max-width: 230px;
    padding: 8px 12px 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 11px;
    color: var(--app-text-muted);
    border-bottom: 1px solid var(--app-border);
  }
  .workspace-login {
    font-size: 12px;
    color: var(--app-accent-text);
  }
  @media (max-width: 767px) {
    .workspace-sidebar {
      transform: translateX(-100%);
      transition: transform 160ms ease;
    }
    .workspace-sidebar--open {
      transform: translateX(0);
    }
    .workspace-topbar {
      left: 0;
      padding: 0 16px;
    }
    .workspace-context {
      gap: 9px;
    }
    .workspace-context-root,
    .workspace-context-root + .workspace-context-separator,
    .workspace-context-detail {
      display: none;
    }
    .workspace-menu-button {
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 30px;
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--app-text-muted);
      cursor: pointer;
    }
    .workspace-context-separator {
      display: none;
    }
    .workspace-account-tools {
      gap: 6px;
    }
    .workspace-public-ip {
      min-height: 44px;
      gap: 6px;
      padding: 5px 7px;
    }
  }
  @media (max-width: 1100px) {
    .workspace-public-ip-label {
      display: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .workspace-sidebar,
    .workspace-nav a {
      transition: none;
    }
  }
  .workspace-feature-context {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 20px 5px 10px;
    padding: 11px 5px 9px;
    border-top: 1px solid var(--app-border);
    font-size: 11px;
    font-weight: 600;
    color: var(--app-text);
  }
  .workspace-feature-context wa-icon {
    font-size: 12px;
    color: var(--app-accent-text);
  }
  .workspace-feature-context span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .workspace-feature-label {
    padding-top: 16px;
    font-size: 9px;
  }
  .workspace-nav a img {
    width: 14px;
    height: 14px;
    opacity: 0.65;
  }
  .workspace-nav-text {
    min-width: 0;
    flex: 1;
  }
  .workspace-nav a .workspace-external {
    width: 10px;
    font-size: 9px;
  }
  .workspace-nav a.workspace-link-danger {
    color: #f87171;
  }
  .workspace-nav a.workspace-link-danger wa-icon {
    color: #f87171;
  }
  .workspace-nav a.workspace-link-danger.active {
    background: rgb(239 68 68/0.08);
    border-color: rgb(239 68 68/0.2);
  }
</style>
