<script lang="ts">
  import { onMount } from 'svelte'
  import { page } from '$app/stores'
  import { userStore } from '$util/stores'
  import { refreshWebAccess, webAccessReady, webAccessStore, webNavStore } from '$lib/webAccessStore'
  import { webLocalPlatformAdmin } from '$lib/webAccessView'
  import WebNoticeHost from '$components/webApps/WebNoticeHost.svelte'
  import '$lib/webApps.css'

  $: platformOnly = /^\/applications\/(settings|recovery)(\/|$)/.test($page.url.pathname)
  // Le serveur reste juge ; avant la réponse de GET me, la session locale suffit pour un superadmin.
  $: platform = $webAccessStore ? $webAccessStore.isPlatformAdmin : webLocalPlatformAdmin($userStore)
  $: allowed = platformOnly ? platform : $webNavStore
  let root: HTMLElement | undefined
  onMount(() => {
    void refreshWebAccess()
  })
</script>

<div class="webapps" data-sveltekit-preload-code="hover" bind:this={root}>
  {#if allowed}
    <slot />
  {:else if !$webAccessReady}
    <section class="web-panel web-empty" role="status"><p>Vérification de vos accès…</p></section>
  {:else}
    <section class="web-panel web-empty">
      <h1>Applications web</h1>
      <p>
        {platformOnly
          ? 'Cette page est réservée aux administrateurs de la plateforme.'
          : 'Vous n’êtes membre d’aucun projet. Demandez à un administrateur du projet de vous inviter avec l’adresse de votre compte.'}
      </p>
      <a class="web-button" href={platformOnly && $webNavStore ? '/applications' : '/dashboard'}
        >{platformOnly && $webNavStore ? 'Revenir aux applications' : 'Revenir au dashboard'}</a
      >
    </section>
  {/if}
  <WebNoticeHost {root} />
</div>
