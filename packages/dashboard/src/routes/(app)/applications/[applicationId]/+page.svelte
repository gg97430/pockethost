<script lang="ts">
  import { onMount, tick } from 'svelte'
  import WebCloudflareDns from '$components/webApps/WebCloudflareDns.svelte'
  import { page as route } from '$app/stores'
  import WebAppNavigation from '$components/webApps/WebAppNavigation.svelte'
  import WebDeleteResource from '$components/webApps/WebDeleteResource.svelte'
  import WebAppStatus from '$components/webApps/WebAppStatus.svelte'
  import WebAppEnvironment from '$components/webApps/WebAppEnvironment.svelte'
  import WebAppTerminal from '$components/webApps/WebAppTerminal.svelte'
  import WebAppHealth from '$components/webApps/WebAppHealth.svelte'
  import WebAppChecks from '$components/webApps/WebAppChecks.svelte'
  import WebAppScheduledTasks from '$components/webApps/WebAppScheduledTasks.svelte'
  import WebAppBackups from '$components/webApps/WebAppBackups.svelte'
  import WebAppRuntimeLogs from '$components/webApps/WebAppRuntimeLogs.svelte'
  import WebAppNotifications from '$components/webApps/WebAppNotifications.svelte'
  import WebAppEnvironments from '$components/webApps/WebAppEnvironments.svelte'
  import WebAppRuntime from '$components/webApps/WebAppRuntime.svelte'
  import WebAppStorage from '$components/webApps/WebAppStorage.svelte'
  import WebAppCi from '$components/webApps/WebAppCi.svelte'

  import WebGitHubSource from '$components/webApps/WebGitHubSource.svelte'
  import WebGitSource from '$components/webApps/WebGitSource.svelte'
  import { webGitRepository, webGitRepositoryLabel } from '$lib/webGit'
  import { webAppGithubConnectionId, webGithubConnectionPayload, WEB_GITHUB_PRINCIPAL } from '$lib/webGithubConnections'
  import WebProjectMembers from '$components/webApps/WebProjectMembers.svelte'
  import { goto } from '$app/navigation'
  import { webAccessStore } from '$lib/webAccessStore'
  import { webAppCapabilities, webAppTabs, webRoleLabel } from '$lib/webAccessView'
  import {
    webRequest,
    webError,
    webDate,
    webCommit,
    webLabels,
    updateWebLogBuffer,
    type WebApp,
    type WebCapabilities,
    type WebDeployment,
    type WebAppDomain,
    type WebAppEnvironmentDraft,
    type WebRuntime,
    type WebLogChunk,
    type WebWebhookDelivery,
  } from '$lib/webApps'

  let app: WebApp | null = null
  let capabilities: WebCapabilities | null = null
  let deployments: WebDeployment[] = []
  let recentDeployments: WebDeployment[] = []
  let domains: WebAppDomain[] = []
  let tab = 'deployments'
  let page = 1
  let hasMore = false
  let loading = true
  let refreshing = false
  let mutation = ''
  let error = ''
  let loadError = ''
  let message = ''
  let renaming = false
  let renamedName = ''
  let renameInput: HTMLInputElement | undefined
  let mounted = false
  let disposed = false
  let observedId = ''
  let initialized = false
  let name = '',
    sourceRepository = '',
    branch = '',
    rootDirectory = '',
    healthPath = '/',
    autoDeploy = true,
    // Source du code : GitHub App ou Dépôt Git (clé de déploiement). Changer de source est réservé à la plateforme.
    draftSource: 'github' | 'git' = 'github',
    draftGitSourceId = '',
    // Connexion GitHub App (plateforme uniquement) : 'default' = connexion principale.
    draftGithubConnectionId = WEB_GITHUB_PRINCIPAL
  let env: WebAppEnvironmentDraft[] = []
  let environmentState: {
    pendingChanges: boolean
    requiresRebuild: boolean
    runtimeOnlyChanges: boolean
    canApplyRuntime: boolean
  } | null = null
  let hostname = ''
  let domainBusy = ''
  let domainError = ''
  let domainsLoaded = false
  let domainsLoading = false
  let domainRequest = 0
  let selectedLog: WebDeployment | null = null
  let logText = ''
  let logTruncated = false
  let logsBusy = false
  let logError = ''
  let logCursor: string | undefined
  let logAuto = true
  let logFollow = true
  let logHasMore = false
  let logRequest = 0
  let logElement: HTMLTextAreaElement | undefined
  let copiedLog = false
  let programmaticScroll = false
  let runtime: WebRuntime | null = null
  let runtimeBusy = false
  let runtimeError = ''
  let webhookRows: WebWebhookDelivery[] = []
  let webhookPage = 1
  let webhookHasMore = false
  let webhookBusy = false
  let webhookError = ''
  let confirmation: { action: 'stop' | 'rollback' | 'cancel' | 'retry'; deployment?: WebDeployment } | null = null
  let removingDomain = ''
  $: applicationId = $route.params.applicationId
  // Droits sur cette application : le serveur les joint à la réponse (`access`) et reste seul juge.
  $: can = webAppCapabilities(app?.access, $webAccessStore, app?.access?.projectId)
  $: projectId = app?.access?.projectId || ''
  $: projectName = $webAccessStore?.projects.find((p) => p.id === projectId)?.name || ''
  $: tabs = webAppTabs(can, !!projectId).filter(
    (item) => !app?.deletion || item.id === 'delete' || item.id === 'deployments'
  )
  // Lien profond (`?tab=` des alertes) : appliqué une fois l'application et ses droits connus.
  let requestedTab = ''
  let requestedFor = ''
  $: if (applicationId && applicationId !== requestedFor) {
    requestedFor = applicationId
    requestedTab = $route.url.searchParams.get('tab') || ''
  }
  $: if (app && requestedTab && tabs.length) {
    const wanted = requestedTab
    requestedTab = ''
    if (tabs.some((item) => item.id === wanted)) setTab(wanted)
  }
  $: if (app && tabs.length && !tabs.some((item) => item.id === tab)) tab = tabs[0]!.id
  $: canDeploy = can['app.deploy'] && !app?.deletion
  $: canConfigure = can['app.configure'] && !app?.deletion
  $: gitApp = app?.source === 'git'
  // Worker sans git/openssh : un déploiement « Dépôt Git » échouerait à la récupération du code.
  $: gitUnavailable = gitApp && app?.sourceDeployable !== false && capabilities?.gitAvailable === false
  let branchChoices: string[] = []
  let branchesFor = ''
  $: current = recentDeployments.find((d) => ['queued', 'running'].includes(d.status)) || recentDeployments[0]
  $: inFlight = recentDeployments.some((d) => ['queued', 'running'].includes(d.status))
  $: if (mounted && applicationId !== observedId) {
    observedId = applicationId || ''
    initialized = false
    renaming = false
    app = null
    deployments = []
    recentDeployments = []
    domains = []
    domainsLoaded = false
    domainsLoading = false
    domainError = ''
    ++domainRequest
    selectedLog = null
    logCursor = undefined
    ++logRequest
    logsBusy = false
    runtime = null
    runtimeError = ''
    webhookRows = []
    webhookPage = 1
    webhookError = ''
    confirmation = null
    environmentState = null
    page = 1
    loading = true
    void load()
  }
  $: configDirty =
    !!app &&
    (name.trim() !== app.name ||
      draftSource !== (app.source || 'github') ||
      (draftSource === 'git'
        ? draftGitSourceId !== (app.gitSourceId || '')
        : sourceRepository.trim() !== app.repository) ||
      (draftSource === 'github' && !!can['app.repository'] && githubConnectionChanged) ||
      branch.trim() !== app.branch ||
      rootDirectory.trim() !== app.rootDirectory ||
      healthPath.trim() !== app.healthPath ||
      autoDeploy !== app.autoDeploy)
  $: githubConnectionChanged =
    !!app &&
    (draftGithubConnectionId || WEB_GITHUB_PRINCIPAL) !== (webAppGithubConnectionId(app) || WEB_GITHUB_PRINCIPAL)
  const hydrate = (value: WebApp) => {
    name = value.name
    draftSource = value.source === 'git' ? 'git' : 'github'
    draftGitSourceId = value.gitSourceId || ''
    draftGithubConnectionId = webAppGithubConnectionId(value) || WEB_GITHUB_PRINCIPAL
    sourceRepository = value.repository
    branch = value.branch
    rootDirectory = value.rootDirectory
    healthPath = value.healthPath || '/'
    autoDeploy = value.autoDeploy
    env = (value.env || []).map((v) => ({ ...v, value: '', remove: false }))
    initialized = true
  }
  const load = async () => {
    if (refreshing || disposed || !applicationId) return
    refreshing = true
    const id = applicationId
    try {
      const result = await webRequest<{
        app: WebApp
        deployments: WebDeployment[]
        hasMore: boolean
        capabilities?: WebCapabilities
      }>(`/${id}`)
      if (disposed || applicationId !== id) return
      app = result.app
      capabilities = result.capabilities ?? null
      loadError = ''
      recentDeployments = result.deployments
      if (!initialized) hydrate(result.app)
      if (page === 1) {
        deployments = result.deployments
        hasMore = result.hasMore
      }
      void loadRuntime()
      if (selectedLog) {
        const latest = result.deployments.find((d) => d.id === selectedLog?.id)
        if (latest) selectedLog = latest
        if (logAuto) void readLogs()
      }
      if (
        tab === 'domains' ||
        domains.some(
          (domain) =>
            ['verified', 'pending_certificate'].includes(domain.status) ||
            ['pending_validation', 'pending_https'].includes(domain.cloudflare?.status || '')
        )
      )
        void loadDomains()
      if (tab === 'webhooks' && webhookPage === 1) void loadWebhooks(1)
    } catch (e) {
      if (!disposed && applicationId === id) loadError = webError(e)
    } finally {
      refreshing = false
      loading = false
    }
  }
  const historyPage = async (next: number) => {
    if (refreshing) return
    refreshing = true
    error = ''
    try {
      const value = await webRequest<{ items: WebDeployment[]; hasMore: boolean }>(`/${applicationId}/deployments`, {
        query: { page: next },
      })
      deployments = value.items
      hasMore = value.hasMore
      page = next
    } catch (e) {
      error = webError(e)
    } finally {
      refreshing = false
    }
  }
  const perform = async (
    action: 'deploy' | 'stop' | 'start' | 'rollback',
    deploymentId?: string,
    fullRebuild = false
  ) => {
    mutation = action
    error = ''
    message = ''
    try {
      await webRequest(`/${applicationId}/${action}`, {
        method: 'POST',
        body: deploymentId ? { deploymentId } : fullRebuild ? { fullRebuild: true } : {},
      })
      confirmation = null
      page = 1
      await load()
      message =
        action === 'deploy'
          ? fullRebuild
            ? 'La reconstruction complète (sans cache) est dans la file de traitement.'
            : 'Le déploiement est dans la file de traitement.'
          : action === 'rollback'
            ? 'Le retour à cette version est dans la file de traitement.'
            : action === 'stop'
              ? 'La demande d’arrêt est dans la file de traitement.'
              : 'La demande de démarrage est dans la file de traitement.'
    } catch (e) {
      error = webError(e)
    } finally {
      mutation = ''
    }
  }
  const beginRename = async () => {
    renamedName = app?.name || ''
    renaming = true
    await tick()
    renameInput?.focus()
    renameInput?.select()
  }
  const rename = async (event: SubmitEvent) => {
    event.preventDefault()
    if (!app || !canConfigure || mutation || !renamedName.trim()) return
    const id = applicationId
    const previousName = app.name
    mutation = 'rename'
    error = ''
    message = ''
    try {
      const value = await webRequest<{ app: WebApp }>(`/${id}`, {
        method: 'PATCH',
        body: { name: renamedName.trim() },
      })
      if (applicationId !== id) return
      app = value.app
      if (name === previousName) name = value.app.name
      renaming = false
      message = 'Application renommée.'
    } catch (e) {
      if (applicationId === id) error = webError(e)
    } finally {
      mutation = ''
    }
  }
  const save = async (event: SubmitEvent) => {
    event.preventDefault()
    mutation = 'save'
    error = ''
    message = ''
    try {
      const value = await webRequest<{ app: WebApp }>(`/${applicationId}`, {
        method: 'PATCH',
        body: {
          name: name.trim(),
          // Changer de dépôt ou de source est réservé à la plateforme : les autres rôles n'envoient pas ces champs.
          // Pour un Dépôt Git, le serveur déduit repository ('git:'+canonical) de la source.
          ...(can['app.repository']
            ? draftSource === 'git'
              ? { source: 'git', gitSourceId: draftGitSourceId }
              : {
                  source: 'github',
                  repository: sourceRepository.trim(),
                  repositoryId: sourceRepository.trim() === app?.repository ? app.repositoryId : 0,
                  // Rattacher l'application à une autre connexion relève de app.repository (plateforme).
                  ...(githubConnectionChanged
                    ? { githubConnectionId: webGithubConnectionPayload(draftGithubConnectionId) }
                    : {}),
                }
            : {}),
          branch: branch.trim(),
          rootDirectory: rootDirectory.trim(),
          healthPath: healthPath.trim(),
          autoDeploy,
        },
      })
      app = value.app
      hydrate(value.app)
      message = 'Configuration enregistrée. Elle sera utilisée au prochain déploiement.'
    } catch (e) {
      error = webError(e)
    } finally {
      mutation = ''
    }
  }
  const environmentChanged = (value: {
    pendingChanges: boolean
    requiresRebuild: boolean
    runtimeOnlyChanges: boolean
    canApplyRuntime: boolean
  }) => {
    environmentState = {
      pendingChanges: value.pendingChanges,
      requiresRebuild: value.requiresRebuild,
      runtimeOnlyChanges: value.runtimeOnlyChanges,
      canApplyRuntime: value.canApplyRuntime,
    }
    void load()
  }
  const applyRuntime = async () => {
    mutation = 'apply-runtime'
    error = ''
    message = ''
    try {
      await webRequest(`/${applicationId}/apply-runtime`, { method: 'POST', body: {} })
      page = 1
      message = 'La nouvelle configuration d’exécution sera contrôlée avant de remplacer le conteneur publié.'
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      mutation = ''
    }
  }
  const loadRuntime = async () => {
    if (runtimeBusy || disposed || !applicationId) return
    runtimeBusy = true
    const id = applicationId
    try {
      const value = await webRequest<WebRuntime>(`/${id}/runtime`)
      if (!disposed && applicationId === id) {
        runtime = ['application-container', 'application-services'].includes(value.scope) ? value : null
        runtimeError = ''
      }
    } catch (e) {
      if (!disposed && applicationId === id) runtimeError = webError(e)
    } finally {
      runtimeBusy = false
    }
  }
  const deploymentAction = async (action: 'cancel' | 'retry', deployment: WebDeployment) => {
    mutation = action
    error = ''
    message = ''
    try {
      await webRequest(`/${applicationId}/deployments/${deployment.id}/${action}`, { method: 'POST', body: {} })
      confirmation = null
      page = 1
      await load()
      message =
        action === 'cancel'
          ? 'L’annulation a été demandée. Une version déjà publiée continue de répondre.'
          : 'Le même commit et sa configuration ont été remis dans la file de traitement.'
    } catch (e) {
      error = webError(e)
    } finally {
      mutation = ''
    }
  }
  const readLogs = async (deployment = selectedLog, reset = false) => {
    if (!deployment || logsBusy) return
    logsBusy = true
    logError = ''
    const id = deployment.id,
      request = ++logRequest,
      appId = applicationId
    try {
      for (let chunk = 0; chunk < 3; chunk++) {
        const previousCursor = reset && chunk === 0 ? undefined : logCursor
        const value = await webRequest<WebLogChunk>(`/${appId}/deployments/${id}/logs`, {
          query: previousCursor ? { cursor: previousCursor } : {},
        })
        if (disposed || request !== logRequest || selectedLog?.id !== id || applicationId !== appId) return
        if (logFollow) programmaticScroll = true
        const buffer = updateWebLogBuffer(
          { text: logText, cursor: logCursor, truncated: logTruncated, hasMore: logHasMore },
          value,
          previousCursor
        )
        logText = buffer.text
        logCursor = buffer.cursor
        logHasMore = buffer.hasMore
        logTruncated = buffer.truncated
        if (value.text) copiedLog = false
        if (!value.hasMore || !value.cursor || previousCursor === value.cursor) break
      }
      if (logFollow) {
        await tick()
        if (logElement) logElement.scrollTop = logElement.scrollHeight
      }
    } catch (e) {
      if (!disposed && request === logRequest && selectedLog?.id === id) logError = webError(e)
    } finally {
      if (request === logRequest) {
        logsBusy = false
        requestAnimationFrame(() => {
          if (request === logRequest) programmaticScroll = false
        })
      }
    }
  }
  const selectLogs = (deployment: WebDeployment) => {
    ++logRequest
    logsBusy = false
    programmaticScroll = false
    selectedLog = deployment
    logText = ''
    logCursor = undefined
    logTruncated = false
    logHasMore = false
    logError = ''
    copiedLog = false
    logFollow = true
    void readLogs(deployment, true)
  }
  const closeLogs = () => {
    ++logRequest
    selectedLog = null
    logCursor = undefined
    logsBusy = false
  }
  const copyLogs = async () => {
    try {
      await navigator.clipboard.writeText(logText)
      copiedLog = true
    } catch {
      logError = 'La copie automatique est indisponible. Sélectionnez le journal pour le copier.'
    }
  }
  const downloadLogs = () => {
    const url = URL.createObjectURL(new Blob([logText], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `deploiement-${selectedLog?.id || 'logs'}.txt`
    link.click()
    URL.revokeObjectURL(url)
  }
  const memory = (value?: number) =>
    value == null ? '—' : `${(value / 1048576).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} Mio`
  const duration = (milliseconds?: number) =>
    milliseconds == null ? '—' : `${(milliseconds / 1000).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} s`
  const loadDomains = async () => {
    if (domainBusy || domainsLoading || disposed || !applicationId) return
    const id = applicationId
    const request = ++domainRequest
    domainsLoading = true
    try {
      const value = await webRequest<{ items: WebAppDomain[] }>(`/${id}/domains`)
      if (disposed || applicationId !== id || request !== domainRequest) return
      domains = value.items
      domainsLoaded = true
      domainError = ''
    } catch (e) {
      if (!disposed && applicationId === id && request === domainRequest) domainError = webError(e)
    } finally {
      if (request === domainRequest) domainsLoading = false
    }
  }
  const loadWebhooks = async (next: number) => {
    if (webhookBusy || disposed) return
    webhookBusy = true
    const id = applicationId
    try {
      const value = await webRequest<{ items: WebWebhookDelivery[]; page: number; hasMore: boolean }>(
        `/${id}/webhooks`,
        { query: { page: next } }
      )
      if (!disposed && applicationId === id) {
        webhookRows = value.items
        webhookPage = value.page
        webhookHasMore = value.hasMore
        webhookError = ''
      }
    } catch (e) {
      if (!disposed && applicationId === id) webhookError = webError(e)
    } finally {
      webhookBusy = false
    }
  }
  // Branches du dépôt de l'application pour un admin de projet (la plateforme passe par WebGitHubSource).
  const loadBranchChoices = async () => {
    // Dépôt Git : WebGitSource liste les branches (ls-remote avec la clé de déploiement).
    if (
      !app ||
      app.source === 'git' ||
      can['platform.github'] ||
      !canConfigure ||
      !app.repositoryId ||
      branchesFor === app.id
    )
      return
    branchesFor = app.id
    try {
      const value = await webRequest<{ items: { name: string }[] }>(
        `/repositories/${encodeURIComponent(String(app.repositoryId))}/branches`,
        { query: { appId: app.id, page: 1, perPage: 100 } }
      )
      branchChoices = value.items.map((item) => item.name)
    } catch {
      branchChoices = []
    }
  }
  const setTab = (value: string) => {
    tab = value
    if (value === 'domains') void loadDomains()
    if (value === 'webhooks') void loadWebhooks(webhookPage)
    if (value === 'settings') void loadBranchChoices()
  }
  const addDomain = async (event: SubmitEvent) => {
    event.preventDefault()
    domainBusy = 'add'
    domainError = ''
    try {
      await webRequest(`/${applicationId}/domains`, {
        method: 'POST',
        body: { hostname: hostname.trim().toLowerCase() },
      })
      hostname = ''
    } catch (e) {
      domainError = webError(e)
    } finally {
      domainBusy = ''
      await loadDomains()
    }
  }
  const domainAction = async (domain: WebAppDomain, remove = false) => {
    domainBusy = domain.id
    domainError = ''
    try {
      await webRequest(`/${applicationId}/domains/${domain.id}${remove ? '' : '/verify'}`, {
        method: remove ? 'DELETE' : 'POST',
        ...(remove ? {} : { body: {} }),
      })
      removingDomain = ''
    } catch (e) {
      domainError = webError(e)
    } finally {
      domainBusy = ''
      await loadDomains()
    }
  }
  const steps = ['En attente', 'Code', 'Construction', 'Démarrage', 'Contrôle', 'Publication']
  const stageIndex = (deployment?: WebDeployment) => {
    if (!deployment) return -1
    if (deployment.status === 'ready') return 6
    if (deployment.status === 'queued') return 0
    const keys: Record<string, number> = { queue: 0, fetch: 1, build: 2, start: 3, health: 4, publish: 5, complete: 6 }
    if (deployment.stageKey && deployment.stageKey in keys) return keys[deployment.stageKey]!
    const stages: Record<string, number> = {
      'Récupération du commit': 1,
      'Récupération du code': 1,
      'Récupération GitHub': 1,
      'Construction Next.js': 2,
      'Démarrage de la nouvelle version': 3,
      'Démarrage du conteneur': 3,
      Démarrage: 3,
      'Contrôle de santé': 4,
      Publication: 5,
      'Publication vérifiée': 5,
      Terminé: 6,
      fetching: 1,
      checkout: 1,
      fetch: 1,
      building: 2,
      build: 2,
      starting: 3,
      starting_container: 3,
      checking: 4,
      health: 4,
      healthcheck: 4,
      publishing: 5,
      publish: 5,
      complete: 6,
    }
    return stages[deployment.stage] ?? -1
  }
  onMount(() => {
    disposed = false
    mounted = true
    const timer = setInterval(() => {
      if (!document.hidden) void load()
    }, 4000)
    return () => {
      mounted = false
      disposed = true
      clearInterval(timer)
    }
  })
</script>

<svelte:head><title>{app?.name || 'Application web'} - Kolab Hosting</title></svelte:head>
<a class="web-back" href="/applications">← Toutes les applications</a>
{#if loading}<div class="web-panel web-empty" role="status"><p>Chargement de l’application…</p></div>
{:else if !app}<div class="web-panel web-empty">
    <h1>Application indisponible</h1>
    <p>{loadError || error || 'Cette application n’est pas accessible.'}</p>
  </div>
{:else}
  <header class="web-header">
    <div class="web-title">
      <span class="web-eyebrow">{app.runtimeKind === 'worker' ? 'Worker' : 'Application web'}</span>
      <div class="web-actions">
        {#if renaming}
          <form class="web-actions" aria-label="Renommer l’application" onsubmit={rename}>
            <label class="web-field">
              <input
                aria-label="Nom de l’application"
                bind:this={renameInput}
                bind:value={renamedName}
                required
                maxlength="80"
                disabled={!!mutation}
                onkeydown={(event) => {
                  if (event.key === 'Escape' && !mutation) renaming = false
                }}
              />
            </label>
            <button
              class="web-button web-button--primary"
              type="submit"
              disabled={!!mutation || !renamedName.trim() || renamedName.trim() === app.name}
              >{mutation === 'rename' ? 'Enregistrement…' : 'Enregistrer'}</button
            >
            <button class="web-button" type="button" disabled={!!mutation} onclick={() => (renaming = false)}
              >Annuler</button
            >
          </form>
        {:else}
          <h1>{app.name}</h1>
          {#if canConfigure}<button class="web-button" disabled={!!mutation} onclick={beginRename}>Renommer</button
            >{/if}
        {/if}
        <WebAppStatus
          status={app.status}
          online={app.status === 'running'}
          label={app.status === 'running' ? 'En ligne' : ''}
        />
      </div>
      {#if app.access?.role && app.access.role !== 'platform'}<span class="web-help"
          >{projectName ? `${projectName} · ` : ''}Votre rôle : {webRoleLabel(app.access.role)}</span
        >{/if}
      <p class="web-subtitle">
        {#if app.repository}{webGitRepositoryLabel(app.repository)} <span aria-hidden="true">/</span>
          <code>{app.branch}</code>{:else}Image immuable{/if}
      </p>
    </div>
    <div class="web-actions">
      {#if app.status === 'running'}{#if app.runtimeKind !== 'worker'}<a
            class="web-button"
            href={app.url || `https://${app.generatedDomain}`}
            target="_blank"
            rel="noopener noreferrer">Ouvrir le site ↗</a
          >{/if}{#if canDeploy}<button
            class="web-button"
            onclick={() => (confirmation = { action: 'stop' })}
            disabled={!!mutation || inFlight}>Arrêter</button
          >{/if}{:else if app.activeDeployment && canDeploy}<button
          class="web-button"
          onclick={() => perform('start')}
          disabled={!!mutation || inFlight}>Démarrer</button
        >{/if}{#if canDeploy}<button
          class="web-button web-button--primary"
          onclick={() => perform('deploy')}
          title={gitUnavailable ? 'Le serveur web ne dispose pas de git/openssh.' : undefined}
          disabled={!!mutation || inFlight || gitUnavailable}
          >{mutation === 'deploy' ? 'Mise en file…' : 'Déployer la branche'}</button
        ><button
          class="web-button"
          onclick={() => perform('deploy', undefined, true)}
          title="Ignore l’image déjà construite et le cache de construction : à utiliser si un build semble corrompu."
          disabled={!!mutation || inFlight || gitUnavailable}>Reconstruire sans cache</button
        >{/if}
    </div>
  </header>
  {#if gitUnavailable && canDeploy}<div class="web-alert" role="status">
      Le serveur web ne dispose pas de git/openssh. Installez git et openssh-client sur le worker pour déployer ce
      dépôt.
    </div>{/if}
  {#if error || loadError}<div class="web-alert web-alert--error" role="alert">{error || loadError}</div>{/if}
  {#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}
  {#if app.deletion}<div class="web-alert" role="status">
      {app.deletion.error ||
        app.deletion.stage ||
        'Suppression en cours…'}{#if app.deletion.status === 'failed' && can['app.delete']}
        <button class="web-button" onclick={() => setTab('delete')}>Réessayer le nettoyage</button>{/if}
    </div>{/if}
  {#if app.lastError}<div class="web-alert" role="status">{app.lastError}</div>{/if}
  {#if confirmation}<div class="web-confirm" role="region" aria-label="Confirmation de l’action">
      <h3>
        {confirmation.action === 'stop'
          ? 'Arrêter cette application ?'
          : confirmation.action === 'cancel'
            ? 'Annuler ce déploiement ?'
            : confirmation.action === 'retry'
              ? 'Relancer cet essai ?'
              : `Revenir au commit ${webCommit(confirmation.deployment?.commit)} ?`}
      </h3>
      <p>
        {confirmation.action === 'stop'
          ? 'Le site ne répondra plus jusqu’à son prochain démarrage.'
          : confirmation.action === 'cancel'
            ? 'Cet essai sera annulé avant publication. Une version déjà publiée continuera de répondre.'
            : confirmation.action === 'retry'
              ? 'La relance reprend le même commit et la configuration de cet essai. Les changements de configuration ultérieurs ne sont pas appliqués.'
              : 'Cette version et sa configuration seront redémarrées et contrôlées avant la bascule. Les données externes ne sont pas restaurées.'}
      </p>
      <div class="web-actions">
        <button
          class="web-button web-button--primary"
          onclick={() => {
            if (!confirmation) return
            if (confirmation.action === 'cancel' || confirmation.action === 'retry') {
              if (confirmation.deployment) void deploymentAction(confirmation.action, confirmation.deployment)
            } else void perform(confirmation.action, confirmation.deployment?.id)
          }}
          disabled={!!mutation}>{mutation ? 'Mise en file…' : 'Confirmer'}</button
        ><button class="web-button" onclick={() => (confirmation = null)} disabled={!!mutation}>Annuler</button>
      </div>
    </div>{/if}
  <div class="web-application-layout">
    <WebAppNavigation items={tabs} selected={tab} onselect={setTab} />
    <div class="web-main">
      <div
        class="web-tab-content"
        id={`web-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`web-tab-${tab}`}
        tabindex="0"
      >
        {#if tab === 'deployments'}
          <div class="web-panel web-panel--accent">
            <div class="web-row">
              <h2>{inFlight ? 'Déploiement en cours' : 'Dernier déploiement'}</h2>
              {#if current}<WebAppStatus status={current.status} />{/if}
            </div>
            {#if current}<div class="web-row">
                <p>
                  <code>{webCommit(current.commit)}</code> · {webLabels[current.action] || current.action} · {webDate(
                    current.created
                  )}
                </p>
                <span class="web-help">{current.elapsedSeconds || 0} s</span>
              </div>
              <ol class="web-progress" aria-label="Étapes du déploiement">
                {#each steps as step, index}<li
                    data-state={index < stageIndex(current)
                      ? 'done'
                      : index === stageIndex(current)
                        ? 'current'
                        : 'pending'}
                    aria-current={index === stageIndex(current) ? 'step' : undefined}
                  >
                    {step}
                  </li>{/each}
              </ol>
              {#if current.phases?.length}<ul class="web-phase-list" aria-label="Durées mesurées des étapes">
                  {#each current.phases as phase}<li>
                      <span>{phase.label}</span><strong>{duration(phase.durationMs)}</strong
                      >{#if !phase.finishedAt}<span class="web-help">en cours</span>{/if}
                    </li>{/each}
                </ul>{/if}
              {#if current.cancelRequested && current.status === 'running'}<p class="web-help" role="status">
                  Annulation demandée. Le worker termine le nettoyage de cet essai.
                </p>{/if}
              {#if current.heartbeatAt && current.status === 'running'}<p class="web-help">
                  Dernier signal du worker : {webDate(current.heartbeatAt)}
                </p>{/if}
              {#if current.error}<div class="web-alert web-alert--error" role="alert" data-notice="off">
                  {current.error}
                </div>{/if}
              <div class="web-actions">
                {#if can['logs.read']}<button class="web-button" onclick={() => selectLogs(current)}
                    >Voir les logs</button
                  >{/if}{#if canDeploy && current.cancellable && !current.cancelRequested}<button
                    class="web-button web-button--danger"
                    onclick={() => (confirmation = { action: 'cancel', deployment: current })}
                    disabled={!!mutation}>Annuler le déploiement</button
                  >{/if}{#if canDeploy && current.retryable}<button
                    class="web-button"
                    onclick={() => (confirmation = { action: 'retry', deployment: current })}
                    disabled={!!mutation || inFlight}>Relancer cet essai</button
                  >{/if}
              </div>{:else}<p class="web-muted">
                Aucune version publiée. Lancez le premier déploiement de la branche configurée.
              </p>{/if}
          </div>
          {#if selectedLog}<div class="web-panel">
              <div class="web-row">
                <div class="web-log-heading">
                  <h2>Logs · {webCommit(selectedLog.commit)}</h2>
                  <p class="web-muted">
                    {webLabels[selectedLog.action] || selectedLog.action} · {webDate(selectedLog.created)}
                  </p>
                </div>
                <div class="web-actions">
                  <button class="web-button" onclick={() => readLogs()} disabled={logsBusy}
                    >{logsBusy ? 'Chargement…' : 'Actualiser'}</button
                  ><button class="web-button" onclick={closeLogs}>Fermer</button>
                </div>
              </div>
              {#if logError}<div class="web-alert web-alert--error" role="alert">{logError}</div>{/if}
              <div class="web-row">
                <div class="web-actions">
                  <label class="web-check"><input type="checkbox" bind:checked={logAuto} /> Actualiser les logs</label
                  ><label class="web-check"
                    ><input type="checkbox" bind:checked={logFollow} /> Suivre les nouvelles lignes</label
                  >
                </div>
                <div class="web-actions">
                  <button class="web-button" onclick={copyLogs} disabled={!logText}
                    >{copiedLog ? 'Copié' : 'Copier'}</button
                  ><button class="web-button" onclick={downloadLogs} disabled={!logText}
                    >Télécharger les lignes affichées</button
                  >
                </div>
              </div>
              <textarea
                bind:this={logElement}
                onscroll={() => {
                  if (
                    !programmaticScroll &&
                    logElement &&
                    logElement.scrollHeight - logElement.scrollTop - logElement.clientHeight > 48
                  )
                    logFollow = false
                }}
                class="web-log"
                readonly
                aria-label="Journal du déploiement"
                rows="14"
                value={logText || (logsBusy ? 'Chargement des logs…' : 'Aucune ligne disponible pour le moment.')}
              ></textarea>
              {#if logHasMore}<div>
                  <button class="web-button" onclick={() => readLogs()} disabled={logsBusy}
                    >Charger les lignes suivantes</button
                  >
                </div>{/if}
              {#if logTruncated}<p class="web-help">
                  Les anciennes lignes sont retirées pour limiter la mémoire. La copie et le téléchargement portent sur
                  les lignes affichées.
                </p>{/if}
            </div>{/if}
          <div class="web-panel">
            <h2>Historique</h2>
            <div class="web-table-wrap">
              <table>
                <thead
                  ><tr
                    ><th>Version</th><th>État</th><th>Date</th><th>Durée</th><th
                      ><span class="web-muted">Actions</span></th
                    ></tr
                  ></thead
                ><tbody
                  >{#each deployments as deployment}<tr
                      ><td
                        ><code>{webCommit(deployment.commit)}</code>{#if deployment.active}<span class="web-help">
                            · publiée</span
                          >{/if}
                        <div class="web-help">{webLabels[deployment.action] || deployment.action}</div></td
                      ><td><WebAppStatus status={deployment.status} /></td><td>{webDate(deployment.created)}</td><td
                        >{deployment.elapsedSeconds || 0} s</td
                      ><td
                        ><div class="web-actions">
                          {#if can['logs.read']}<button class="web-button" onclick={() => selectLogs(deployment)}
                              >Logs</button
                            >{/if}{#if canDeploy && deployment.cancellable && !deployment.cancelRequested}<button
                              class="web-button web-button--danger"
                              onclick={() => (confirmation = { action: 'cancel', deployment })}
                              disabled={!!mutation}>Annuler</button
                            >{/if}{#if canDeploy && deployment.retryable}<button
                              class="web-button"
                              onclick={() => (confirmation = { action: 'retry', deployment })}
                              disabled={!!mutation || inFlight}>Relancer</button
                            >{/if}{#if canDeploy && deployment.status === 'ready' && !deployment.active && deployment.available !== false && ['deploy', 'start', 'rollback', 'apply-runtime', 'restore-backup', 'promote', 'ci-deploy'].includes(deployment.action)}<button
                              class="web-button"
                              onclick={() => (confirmation = { action: 'rollback', deployment })}
                              disabled={!!mutation || inFlight}>Restaurer cette version</button
                            >{/if}
                        </div></td
                      ></tr
                    >{:else}<tr><td colspan="5">Aucun déploiement enregistré.</td></tr>{/each}</tbody
                >
              </table>
            </div>
            {#if page > 1 || hasMore}<nav class="web-pagination" aria-label="Pages des déploiements">
                <button class="web-button" onclick={() => historyPage(page - 1)} disabled={page === 1 || refreshing}
                  >Précédent</button
                ><span>Page {page}</span><button
                  class="web-button"
                  onclick={() => historyPage(page + 1)}
                  disabled={!hasMore || refreshing}>Suivant</button
                >
              </nav>{/if}
          </div>
        {:else if tab === 'domains'}
          <div class="web-panel">
            <div class="web-row">
              <h2>Adresse générée</h2>
              <WebAppStatus
                status={app.status === 'running' ? 'active' : 'stopped'}
                label={app.status === 'running' ? 'Site en ligne' : 'Adresse réservée'}
              />
            </div>
            <code>{app.generatedDomain}</code>
            <p class="web-help">
              Cette adresse reste attachée à l’application lors des déploiements et des retours arrière.
            </p>
          </div>
          {#if can['domains.manage']}<div class="web-panel">
              <h2>Ajouter un domaine personnalisé</h2>
              <form class="web-form" onsubmit={addDomain}>
                <label class="web-field"
                  ><span>Nom de domaine</span><input
                    bind:value={hostname}
                    required
                    placeholder="www.monsite.fr"
                    disabled={!!domainBusy}
                    autocomplete="off"
                  /><span class="web-help"
                    >Saisissez un nom d’hôte sans protocole, chemin ni wildcard. Ajoutez chaque adresse séparément, par
                    exemple <code>monsite.fr</code> et <code>www.monsite.fr</code>.</span
                  ></label
                >
                <div>
                  <button class="web-button web-button--primary" type="submit" disabled={!!domainBusy}
                    >{domainBusy === 'add' ? 'Ajout…' : 'Ajouter le domaine'}</button
                  >
                </div>
              </form>
              {#if domainError}<div class="web-alert web-alert--error" role="alert">{domainError}</div>{/if}
            </div>{:else if domainError}<div class="web-alert web-alert--error" role="alert">{domainError}</div>{/if}
          <div class="web-panel">
            <div class="web-row">
              <h2>Domaines personnalisés</h2>
              <button class="web-button" onclick={loadDomains} disabled={!!domainBusy || domainsLoading}
                >Actualiser</button
              >
            </div>
            {#if !domainsLoaded}<p role="status">Chargement des domaines…</p>{:else if !domains.length}<p
                class="web-muted"
              >
                Aucun domaine personnalisé.
              </p>{/if}{#each domains as domain (domain.id)}<article class="web-domain">
                <div class="web-row">
                  <h3>{domain.hostname}</h3>
                  <WebAppStatus status={domain.status} />
                </div>
                {#if can['platform.settings']}<WebCloudflareDns appId={app.id} {domain} onapplied={loadDomains} />{/if}
                {#if domain.status !== 'active'}<p class="web-help">
                    Créez ces enregistrements chez votre fournisseur DNS. Pour le premier contrôle, désactivez le proxy
                    DNS éventuel et laissez l’adresse pointer directement vers le serveur.
                  </p>
                  <div class="web-dns-grid">
                    {#if domain.ownershipRequired !== false}<div class="web-dns-record">
                        <strong>TXT · Vérification de propriété</strong><code>{domain.ownershipName}</code><code
                          >{domain.ownershipValue}</code
                        >
                      </div>{/if}
                    <div class="web-dns-record">
                      <strong>A · Adresse du serveur</strong><code>{domain.hostname} → {domain.dnsTarget}</code>
                    </div>
                  </div>{/if}{#if domain.certificateExpiresAt}<p class="web-help">
                    Certificat valable jusqu’au {webDate(domain.certificateExpiresAt)}.
                  </p>{/if}{#if domain.lastError}<div class="web-alert" role="status">
                    {domain.lastError}
                  </div>{/if}{#if removingDomain === domain.id}<div class="web-confirm">
                    <p>
                      Retirer <strong>{domain.hostname}</strong> de cette application ? L’adresse générée restera disponible.
                    </p>
                    <div class="web-actions">
                      <button
                        class="web-button web-button--danger"
                        onclick={() => domainAction(domain, true)}
                        disabled={!!domainBusy}>Confirmer le retrait</button
                      ><button class="web-button" onclick={() => (removingDomain = '')} disabled={!!domainBusy}
                        >Annuler</button
                      >
                    </div>
                  </div>{:else if can['domains.manage']}<div class="web-actions">
                    <button class="web-button" onclick={() => domainAction(domain)} disabled={!!domainBusy}
                      >{domainBusy === domain.id ? 'Contrôle…' : 'Vérifier DNS et HTTPS'}</button
                    ><button class="web-button" onclick={() => (removingDomain = domain.id)} disabled={!!domainBusy}
                      >Retirer</button
                    >
                  </div>{/if}
              </article>{/each}
          </div>
        {:else if tab === 'environment'}
          <div class="web-panel">
            {#key app.id}<WebAppEnvironment
                appId={app.id}
                refreshKey={app.activeDeployment}
                bind:rows={env}
                disabled={!!mutation}
                capabilities={can}
                onchanged={environmentChanged}
              />{/key}
            {#if environmentState?.pendingChanges && canDeploy}<div class="web-alert" role="status">
                <strong>Les valeurs enregistrées attendent leur application.</strong>
                <p class="web-help">
                  {environmentState.requiresRebuild
                    ? 'Une variable de construction a changé : reconstruisez le site pour produire ses nouveaux fichiers.'
                    : 'Les variables d’exécution peuvent être appliquées en réutilisant l’image publiée.'}
                </p>
                <div class="web-actions">
                  {#if environmentState.canApplyRuntime}<button
                      class="web-button web-button--primary"
                      type="button"
                      onclick={applyRuntime}
                      disabled={!!mutation || inFlight}>Appliquer sans reconstruire</button
                    >{/if}<button
                    class="web-button"
                    type="button"
                    onclick={() => perform('deploy')}
                    disabled={!!mutation || inFlight}>Reconstruire et déployer</button
                  >
                </div>
              </div>{/if}
          </div>
        {:else if tab === 'runtime-logs'}
          {#key app.id}<WebAppRuntimeLogs appId={app.id} running={app.status === 'running'} />{/key}
        {:else if tab === 'environments'}
          {#key app.id}<WebAppEnvironments appId={app.id} capabilities={can} onchanged={load} />{/key}
        {:else if tab === 'runtime-profile'}
          {#key app.id}<WebAppRuntime appId={app.id} capabilities={can} onchanged={load} />{/key}
        {:else if tab === 'storage'}
          {#key app.id}<WebAppStorage appId={app.id} capabilities={can} onchanged={load} />{/key}
        {:else if tab === 'ci'}
          {#key app.id}<WebAppCi appId={app.id} />{/key}
        {:else if tab === 'notifications'}
          {#key app.id}<WebAppNotifications appId={app.id} capabilities={can} onopentab={setTab} />{/key}
        {:else if tab === 'health'}
          {#key app.id}<WebAppHealth appId={app.id} capabilities={can} />{/key}
          {#key app.id}<WebAppChecks appId={app.id} environment={app.env} capabilities={can} />{/key}
        {:else if tab === 'tasks'}
          {#key app.id}<WebAppScheduledTasks
              appId={app.id}
              running={app.status === 'running'}
              capabilities={can}
            />{/key}
        {:else if tab === 'backups'}
          {#key app.id}<WebAppBackups appId={app.id} capabilities={can} onchanged={load} />{/key}
        {:else if tab === 'terminal'}
          {#key app.id}<WebAppTerminal
              appId={app.id}
              activeDeployment={app.activeDeployment}
              running={app.status === 'running'}
            />{/key}
        {:else if tab === 'members' && projectId}
          {#key projectId}<WebProjectMembers {projectId} {projectName} onleft={() => goto('/applications')} />{/key}
        {:else if tab === 'webhooks'}
          <section class="web-panel">
            <div class="web-row">
              <h2>{gitApp ? 'Activité Git' : 'Notifications de GitHub'}</h2>
              <button class="web-button" onclick={() => loadWebhooks(webhookPage)} disabled={webhookBusy}
                >Actualiser</button
              >
            </div>
            <p class="web-help">
              Comprenez pourquoi un push a déclenché un déploiement ou a été ignoré. Seules les notifications dont la
              signature a été vérifiée figurent ici.
            </p>
            {#if webhookError}<div class="web-alert web-alert--error" role="alert">{webhookError}</div>{/if}
            <div class="web-table-wrap">
              <table>
                <thead
                  ><tr
                    ><th>Réception</th><th>Branche</th><th>Commit reçu</th><th>Commit retenu</th><th>Traitement</th></tr
                  ></thead
                ><tbody
                  >{#each webhookRows as delivery}<tr
                      ><td>{webDate(delivery.lastReceivedAt || delivery.created)}</td><td
                        ><code>{delivery.branch || '—'}</code></td
                      ><td><code>{webCommit(delivery.payloadCommit) || '—'}</code></td><td
                        ><code>{webCommit(delivery.resolvedCommit) || '—'}</code></td
                      ><td
                        >{delivery.status === 'queued'
                          ? `${delivery.queued} mise(s) en file`
                          : 'Ignorée'}{#if delivery.duplicateCount}<div class="web-help">
                            {delivery.duplicateCount} doublon(s)
                          </div>{/if}</td
                      ></tr
                    >{#if delivery.note}<tr><td colspan="5"><p class="web-help">{delivery.note}</p></td></tr
                      >{/if}{:else}<tr
                      ><td colspan="5"
                        >{webhookBusy
                          ? 'Chargement…'
                          : gitApp
                            ? 'Aucun push Git vérifié pour cette application.'
                            : 'Aucune notification GitHub vérifiée pour cette application.'}</td
                      ></tr
                    >{/each}</tbody
                >
              </table>
            </div>
            {#if webhookPage > 1 || webhookHasMore}<nav
                class="web-pagination"
                aria-label={gitApp ? 'Pages de l’activité Git' : 'Pages des notifications GitHub'}
              >
                <button
                  class="web-button"
                  onclick={() => loadWebhooks(webhookPage - 1)}
                  disabled={webhookBusy || webhookPage === 1}>Précédent</button
                ><span>Page {webhookPage}</span><button
                  class="web-button"
                  onclick={() => loadWebhooks(webhookPage + 1)}
                  disabled={webhookBusy || !webhookHasMore}>Suivant</button
                >
              </nav>{/if}
          </section>
        {:else if tab === 'delete'}
          <WebDeleteResource
            resourceId={app.id}
            name={app.name}
            blocked={inFlight}
            onaccepted={() => goto('/applications')}
          />
        {:else}
          {#if draftSource === 'git'}<WebGitSource
              sourceId={app.gitSourceId ?? ''}
              appId={app.id}
              projectId={app.access?.projectId ?? ''}
              {branch}
              canManage={can['source.hostKey.scan']}
              canConfigureRepository={can['source.git.configure']}
              onbranch={(value) => (branch = value)}
              onsource={(source) => {
                draftGitSourceId = source.id
                sourceRepository = webGitRepository(source.canonical)
              }}
              ondelete={() => (draftGitSourceId = '')}
            />{:else if can['platform.github']}<WebGitHubSource
              repository={sourceRepository}
              {branch}
              {autoDeploy}
              bind:githubConnectionId={draftGithubConnectionId}
              connectionEditable={!mutation && !!can['app.repository']}
              onbranch={(value) => (branch = value)}
            />{/if}
          <div class="web-panel">
            <div class="web-row">
              <h2>Configuration de l’application</h2>
              {#if configDirty}<span class="web-status" data-tone="warning">Modifications non enregistrées</span>{/if}
            </div>
            <form class="web-form" onsubmit={save}>
              <div class="web-form-grid">
                <label class="web-field"
                  ><span>Nom</span><input
                    bind:value={name}
                    required
                    maxlength="80"
                    disabled={!!mutation || !canConfigure}
                  /></label
                >{#if can['app.repository']}<label class="web-field"
                    ><span>Source</span><select bind:value={draftSource} disabled={!!mutation}
                      ><option value="github">GitHub App</option><option value="git"
                        >Dépôt Git (clé de déploiement)</option
                      ></select
                    ><span class="web-help"
                      >{draftSource === 'git'
                        ? 'GitLab, Gitea, Forgejo, Bitbucket ou serveur SSH : une clé en lecture seule par dépôt. Pas d’aperçus de pull request.'
                        : 'Dépôts accessibles à l’installation GitHub App du serveur.'}</span
                    ></label
                  >{/if}{#if draftSource === 'git'}<div class="web-field">
                    <span>Dépôt Git</span><code
                      >{webGitRepositoryLabel(sourceRepository) || 'Enregistrez le dépôt ci-dessus.'}</code
                    ><span class="web-help"
                      >{can['app.repository']
                        ? 'L’adresse du dépôt et sa clé se gèrent dans le panneau « Dépôt Git ».'
                        : 'Le dépôt est fixé par un administrateur de la plateforme.'}</span
                    >
                  </div>{:else}<label class="web-field"
                    ><span>Dépôt GitHub</span><input
                      bind:value={sourceRepository}
                      required
                      pattern="[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+"
                      disabled={!!mutation || !can['app.repository']}
                    /><span class="web-help"
                      >{can['app.repository']
                        ? 'Changer de dépôt s’applique au prochain déploiement ; la version actuelle reste publiée.'
                        : 'Le dépôt est fixé par un administrateur de la plateforme.'}</span
                    ></label
                  >{/if}<label class="web-field"
                  ><span>Branche à publier</span><input
                    bind:value={branch}
                    required
                    maxlength="255"
                    list="web-app-branches"
                    disabled={!!mutation || !canConfigure}
                  /><datalist id="web-app-branches"
                    >{#each branchChoices as item}<option value={item}></option>{/each}</datalist
                  ></label
                ><label class="web-field"
                  ><span>Dossier du projet</span><input
                    bind:value={rootDirectory}
                    placeholder="Racine du dépôt"
                    disabled={!!mutation || !canConfigure}
                  /></label
                ><label class="web-field"
                  ><span>Chemin du contrôle de santé</span><input
                    bind:value={healthPath}
                    required
                    pattern="/.*"
                    disabled={!!mutation || !canConfigure}
                  /></label
                >
              </div>
              <label class="web-check"
                ><input
                  type="checkbox"
                  bind:checked={autoDeploy}
                  disabled={!!mutation || !canConfigure || app.sourceDeployable === false}
                /> Déployer automatiquement les pushes sur cette branche</label
              >
              {#if canConfigure}<div>
                  <button
                    class="web-button web-button--primary"
                    type="submit"
                    disabled={!!mutation || !configDirty || (draftSource === 'git' && !draftGitSourceId)}
                    >{mutation === 'save' ? 'Enregistrement…' : 'Enregistrer la configuration'}</button
                  >
                </div>{:else}<p class="web-help">
                  Consultation seule : la configuration est modifiée par un administrateur du projet.
                </p>{/if}
            </form>
          </div>
        {/if}
      </div>
      <aside class="web-sidebar">
        <section class="web-panel">
          <div class="web-row">
            <h2>Ressources du site</h2>
            <button
              class="web-button"
              onclick={loadRuntime}
              disabled={runtimeBusy}
              aria-label="Actualiser les ressources">↻</button
            >
          </div>
          {#if runtime?.available}<dl class="web-facts">
              <div>
                <dt>CPU · 100 % = un cœur</dt>
                <dd>{runtime.cpuPercent?.toLocaleString('fr-FR', { maximumFractionDigits: 1 }) ?? '—'} %</dd>
              </div>
              <div>
                <dt>
                  Mémoire de l’application{runtime.scope === 'application-services'
                    ? ` · ${runtime.serviceCount ?? 1} services`
                    : ''}
                </dt>
                <dd>{memory(runtime.memoryBytes)} / {memory(runtime.memoryLimitBytes)}</dd>
              </div>
              {#if runtime.pids != null}<div>
                  <dt>Processus</dt>
                  <dd>{runtime.pids}</dd>
                </div>{/if}
            </dl>
            {#if runtime.memoryBytes != null && runtime.memoryLimitBytes}<progress
                class="web-memory-progress"
                max={runtime.memoryLimitBytes}
                value={runtime.memoryBytes}
                aria-label="Mémoire utilisée par le conteneur de cette application"
              ></progress>{/if}
            <p class="web-help">Mesures de cette application, relevées le {webDate(runtime.checkedAt)}.</p>{:else}<p
              class="web-help"
            >
              {runtime?.lastError ||
                (runtime?.state === 'stopped'
                  ? 'Le site est arrêté.'
                  : 'Les mesures du conteneur ne sont pas encore disponibles.')}
            </p>{/if}
          {#if runtimeError}<p class="web-help" role="status">{runtimeError}</p>{/if}
        </section>
        <div class="web-panel">
          <h2>Publication</h2>
          <dl class="web-facts">
            <div>
              <dt>Adresse</dt>
              <dd>
                {#if app.runtimeKind === 'worker'}Sans route HTTP{:else}<a
                    href={app.url || `https://${app.generatedDomain}`}
                    target="_blank"
                    rel="noopener noreferrer">{app.generatedDomain} ↗</a
                  >{/if}
              </dd>
            </div>
            <div>
              <dt>{gitApp ? 'Dépôt Git' : 'Dépôt'}</dt>
              <dd>{webGitRepositoryLabel(app.repository) || '—'}</dd>
            </div>
            <div>
              <dt>Branche suivie</dt>
              <dd><code>{app.branch}</code></dd>
            </div>
            <div>
              <dt>Mises à jour Git</dt>
              <dd>{app.autoDeploy ? 'Automatiques sur push' : 'Déploiement manuel'}</dd>
            </div>
            <div>
              <dt>Dernier commit demandé</dt>
              <dd><code>{webCommit(app.desiredCommit)}</code></dd>
            </div>
          </dl>
        </div>
        <div class="web-panel">
          <h2>Déploiement contrôlé</h2>
          <p class="web-help">
            La version publiée reste en ligne pendant la construction. Le nouveau conteneur reçoit le trafic après ses
            contrôles de santé.
          </p>
          <p class="web-help">
            Une seule construction est exécutée à la fois sur le serveur. Les limites de ressources sont gérées par
            l’administrateur du serveur.
          </p>
          <p class="web-help">Actualisation toutes les 5 secondes lorsque cette page est visible.</p>
        </div>
      </aside>
    </div>
  </div>
{/if}
