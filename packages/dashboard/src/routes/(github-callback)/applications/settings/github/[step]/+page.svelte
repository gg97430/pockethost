<script lang="ts">
  import { onMount } from 'svelte'
  import { afterNavigate, replaceState } from '$app/navigation'
  import { page } from '$app/stores'
  import { userStore } from '$util/stores'
  import {
    webRequest,
    webError,
    githubDestination,
    type WebGitHubConnectResult,
    type WebGitHubInstallStart,
  } from '$lib/webApps'
  import { webGithubCompletionMessage, webGithubConnectionHref } from '$lib/webGithubConnections'
  import { webLocalPlatformAdmin } from '$lib/webAccessView'
  import '$lib/webApps.css'

  let payload: { step: string; state: string; code: string; installationId: string; providerError: string } | null =
    null
  let mounted = false
  let started = false
  let busy = false
  let error = ''
  let connected = false
  let repositoryCount = 0
  let account = ''
  let connectionLabel = ''
  let connectionId = ''
  let mode: 'add' | 'replace' | undefined
  let installUrl = ''
  let disposed = false
  $: authorized = webLocalPlatformAdmin($userStore)
  $: if (mounted && payload && authorized && !started) void complete()

  const complete = async () => {
    if (!payload || started) return
    started = true
    busy = true
    try {
      if (payload.providerError)
        throw new Error('La connexion a été refusée ou annulée sur GitHub. Vous pouvez relancer l’assistant.')
      if (!payload.state || !['manifest', 'install'].includes(payload.step))
        throw new Error('Ce retour GitHub est incomplet. Relancez l’assistant depuis les paramètres.')
      if (payload.step === 'manifest') {
        if (!payload.code) throw new Error('La création de la GitHub App a été interrompue. Relancez l’assistant.')
        const value = await webRequest<WebGitHubInstallStart>('/github/connect/manifest', {
          method: 'POST',
          body: { state: payload.state, code: payload.code },
        })
        installUrl = githubDestination(value.installUrl, 'install')
        if (!disposed) window.location.assign(installUrl)
      } else {
        if (!payload.installationId)
          throw new Error('Aucune installation GitHub n’a été sélectionnée. Reprenez la connexion dans les paramètres.')
        const value = await webRequest<WebGitHubConnectResult>('/github/connect/install', {
          method: 'POST',
          body: { state: payload.state, installationId: payload.installationId },
        })
        if (disposed) return
        if (!value.connected) throw new Error('GitHub n’a pas confirmé cette connexion. Reprenez l’assistant.')
        connected = true
        repositoryCount = value.repositoryCount
        account = value.installation?.account || ''
        connectionLabel = value.label || ''
        connectionId = value.connectionId || ''
        mode = value.mode
      }
    } catch (e) {
      if (!disposed) error = webError(e)
    } finally {
      payload = null
      if (!disposed) busy = false
    }
  }
  afterNavigate(() => {
    if (mounted || disposed) return
    const params = new URLSearchParams(window.location.search)
    const captured = {
      step: $page.params.step || '',
      state: params.get('state') || '',
      code: params.get('code') || '',
      installationId: params.get('installation_id') || '',
      providerError: params.get('error') || '',
    }
    // The callback also mounts without an authenticated layout, so expired sessions never leave codes in the URL.
    replaceState(window.location.pathname, {})
    payload = captured
    mounted = true
  })
  onMount(() => () => {
    disposed = true
    payload = null
  })
</script>

<svelte:head
  ><title>Connexion GitHub - Applications web</title><meta name="referrer" content="no-referrer" /></svelte:head
>
<div class="webapps github-callback">
  <a class="web-back" href="/applications/settings">← Connexion GitHub</a>
  <section class="web-panel web-panel--accent">
    <span class="web-eyebrow">Applications web · GitHub</span>
    {#if !mounted || busy}
      <h1>{payload?.step === 'manifest' ? 'Préparation de l’installation' : 'Vérification de la connexion'}</h1>
      <p role="status">GitHub est vérifié avant l’activation de la connexion. Patientez quelques instants…</p>
    {:else if !authorized}
      <h1>Reconnectez-vous au dashboard</h1>
      <p>
        Votre session administrateur est requise pour terminer cette connexion. Les informations temporaires ont été
        retirées de l’adresse.
      </p>
      <p class="web-help">
        Après connexion, relancez ou reprenez l’assistant dans Applications web → Connexion GitHub.
      </p>
      <div class="web-actions">
        <a class="web-button web-button--primary" href="/login">Connexion au dashboard</a><a
          class="web-button"
          href="/applications/settings">Revenir aux paramètres</a
        >
      </div>
    {:else if error}
      <h1>La connexion reste à terminer</h1>
      <div class="web-alert web-alert--error" role="alert">{error}</div>
      <p class="web-help">
        Les connexions déjà actives restent utilisées. Reprenez l’assistant depuis les paramètres ; ne rechargez pas ce
        lien temporaire.
      </p>
      <div><a class="web-button web-button--primary" href="/applications/settings">Reprendre la connexion</a></div>
    {:else if connected}
      <h1>{mode === 'replace' ? 'Connexion GitHub mise à jour' : 'GitHub est connecté'}</h1>
      <p role="status">
        {webGithubCompletionMessage({ label: connectionLabel, account, mode, repositoryCount })}
      </p>
      <p class="web-help">
        {account ? `Installation vérifiée sur le compte ${account}. ` : ''}{mode === 'replace'
          ? 'Les prochains déploiements des applications rattachées utilisent la nouvelle GitHub App.'
          : 'Vos autres connexions GitHub ne sont pas modifiées.'}
      </p>
      <p>Choisissez un dépôt et sa branche pour créer votre prochaine application.</p>
      <div class="web-actions">
        <a class="web-button web-button--primary" href="/applications?create=1">Choisir un dépôt</a><a
          class="web-button"
          href={webGithubConnectionHref(connectionId)}>Voir la connexion</a
        >
      </div>
    {:else if installUrl}
      <h1>Votre GitHub App est prête</h1>
      <p>Terminez la sélection des dépôts sur GitHub pour activer la connexion.</p>
      <div>
        <a class="web-button web-button--primary" href={installUrl} rel="noreferrer">Choisir les dépôts sur GitHub ↗</a>
      </div>
    {/if}
  </section>
</div>

<style>
  .github-callback {
    width: min(100% - 2rem, 48rem);
    margin: 3rem auto;
  }
  .github-callback h1 {
    font-size: clamp(1.6rem, 4vw, 2.25rem);
  }
</style>
