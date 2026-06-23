<script>
  const sidebarWidth = '300px'
  import Logo from '$src/routes/Navbar/Logo.svelte'
  import DocLink from './DocLink.svelte'
  import { onMount } from 'svelte'
  export let data

  let sidebarOpen = false
  let windowWidth = 0

  $: if (typeof document !== 'undefined' && windowWidth < 768) {
    document.body.style.overflow = sidebarOpen ? 'hidden' : ''
  }

  onMount(() => {
    windowWidth = window.innerWidth
    const resizeListener = () => (windowWidth = window.innerWidth)
    window.addEventListener('resize', resizeListener)
    return () => window.removeEventListener('resize', resizeListener)
  })

  function closeSidebar() {
    if (windowWidth < 768) sidebarOpen = false
  }

  $: ({ title, description } = data.meta || {})
</script>

<svelte:head>
  <title>{title}</title>
  <meta name="description" content={description} />

  <meta property="og:url" content={data.url} />
  <meta property="og:type" content="website" />
  <meta property="og:title" content={title} />
  {#if description}
    <meta property="og:description" content={description} />
  {/if}

  {#if title}
    <meta property="og:image" content={'https://cdn.cheto.app/og/joioes1x8zagn0v?name=' + encodeURIComponent(title)} />
  {/if}

  <meta name="twitter:card" content="summary_large_image" />
  <meta property="twitter:url" content={data.url} />
  <meta name="twitter:title" content={title} />
  {#if description}
    <meta name="twitter:description" content={description} />
  {/if}

  {#if title}
    <meta name="twitter:image" content={'https://cdn.cheto.app/og/joioes1x8zagn0v?name=' + encodeURIComponent(title)} />
  {/if}
</svelte:head>

<div class="px-4 md:px-20">
  <div class="md:mt-8 flex gap-6 z-10 relative flex-col md:flex-row">
    <div
      class="flex md:hidden items-center sticky top-0 bg-gradient-to-b from-[#111111] to-[#111111]/40 shadow-md justify-start gap-6 py-3 border-y border-white/10 z-40"
    >
      <button onclick={() => (sidebarOpen = !sidebarOpen)}>
        <wa-icon name="bars"></wa-icon>
      </button>
      <h2 class="text-lg font-bold text-white truncate">Docs</h2>
    </div>

    {#if sidebarOpen && windowWidth < 768}
      <div class="fixed inset-0 bg-black bg-opacity-50 z-40 backdrop-blur-sm" onclick={closeSidebar}></div>
    {/if}

    <div
      class="flex w-[300px] shrink-0 flex-col fixed md:sticky overflow-y-auto top-0 left-0 md:top-8 md:h-[calc(100vh-4rem)] h-screen bg-[#111111] md:bg-transparent px-4 md:px-0 pb-10 md:pb-0 z-50 transform transition-transform duration-300"
      class:-translate-x-full={!sidebarOpen && windowWidth < 768}
      onclick={closeSidebar}
      role="presentation"
    >
      <nav class="flex flex-col gap-1 text-white mb-6 pl-0">
        <div class="md:hidden flex justify-start text-white font-bold text-xl items-center flex-row py-2">
          <Logo className="px-0 mr-4" /> Docs
        </div>

        <div class="text-sm font-semibold text-white/60 uppercase tracking-wide pt-4 pb-1">Vue d'ensemble</div>
        <DocLink path="introduction" title="Introduction" />
        <DocLink path="getting-started" title="Démarrage" />

        <div class="text-sm font-semibold text-white/60 uppercase tracking-wide pt-4 pb-1">Gestion des instances</div>
        <DocLink path="create" title="Créer" />
        <DocLink path="accessing" title="Connexion" />
        <DocLink path="power" title="Alimentation" />
        <DocLink path="rename-instance" title="Renommer" />
        <DocLink path="delete" title="Supprimer" />
        <DocLink path="limits" title="Limites" />
        <DocLink path="smtp" title="Email sortant" />
        <DocLink path="versions" title="Changer de version" />
        <DocLink path="s3" title="Stockage S3" />
        <DocLink path="custom-binaries" title="Binaires personnalisés" />
        <DocLink path="custom-domain" title="Domaine personnalisé" />
        <DocLink path="backup-restore" title="Sauvegarde/restauration" />

        <div class="text-sm font-semibold text-white/60 uppercase tracking-wide pt-4 pb-1">Utilisation quotidienne</div>
        <DocLink path="logs" title="Logs" />
        <DocLink path="dev-mode" title="Mode dev" />
        <DocLink path="secrets" title="Secrets" />
        <DocLink path="webhooks" title="Webhooks" />
        <DocLink path="ftp" title="Accès fichiers SFTP" />
        <DocLink path="phio" title="phio CLI" />
        <DocLink path="static-assets" title="Assets statiques" />
        <DocLink path="admin-sync" title="Synchro admin" />
        <DocLink path="auto-vacuum" title="Auto Vacuum" />
        <DocLink path="js" title="Étendre avec JS" />

        <div class="text-sm font-semibold text-white/60 uppercase tracking-wide pt-4 pb-1">Guide de programmation</div>
        <DocLink path="programming" title="Frontends et hooks JS" />
        <DocLink path="admin-extensions" title="Extensions UI admin" />
        <DocLink path="server-side-pocketbase-antipattern" title="PocketBase côté serveur est un anti-pattern" />

        <div class="text-sm font-semibold text-white/60 uppercase tracking-wide pt-4 pb-1">Annexes</div>
        <DocLink path="account-creation" title="Création de compte" />
        <DocLink path="pricing-ethos" title="Philosophie tarifaire" />
        <DocLink path="faq" title="FAQ" />
        <DocLink path="gs-gmail" title="Configuration Gmail Google Suite" />
        <DocLink path="ses" title="Configuration Amazon SES" />
        <DocLink path="self-hosting" title="Auto-hébergement" />
      </nav>
    </div>

    <div class="docs-content prose max-w-prose w-full min-w-0 text-white/90">
      <slot />
    </div>
  </div>
</div>
