<script lang="ts">
  import { onMount } from 'svelte'
  import { goto } from '$app/navigation'
  import WebHostingSummary from '$components/webApps/WebHostingSummary.svelte'
  import WebAppEnvironment from '$components/webApps/WebAppEnvironment.svelte'
  import WebGitSource from '$components/webApps/WebGitSource.svelte'
  import WebProjectGroups from '$components/webApps/WebProjectGroups.svelte'
  import WebProjectCreateDialog from '$components/webApps/WebProjectCreateDialog.svelte'
  import type { WebGitSource as WebGitSourceView } from '$lib/webGit'
  import { userStore } from '$util/stores'
  import { refreshWebAccess, webAccessStore } from '$lib/webAccessStore'
  import { WEB_NO_CAPABILITIES, webLocalPlatformAdmin } from '$lib/webAccessView'
  import { webCapabilities } from '$lib/webAccess'
  import { webProjectGroupsLoad, type WebGroupedApps } from '$lib/webProjectGroupsApi'
  import {
    webRequest,
    webError,
    type WebApp,
    type WebCapabilities,
    type WebRepository,
    type WebAppEnvironmentDraft,
    type WebRepositoryPage,
    type WebBranchPage,
    type WebGitHubConnectionSummary,
    webGithubConnections,
  } from '$lib/webApps'
  import {
    WEB_GITHUB_PRINCIPAL,
    webGithubActiveConnections,
    webGithubConnectionOption,
    webGithubConnectionQuery,
    webGithubDefaultSelection,
  } from '$lib/webGithubConnections'

  /** GET /api/web-projects/groups : projets de l'utilisateur, leurs sites et « Sans projet » (superAdmin). */
  let groups: WebGroupedApps | null = null
  let capabilities: WebCapabilities | null = null
  let repositories: WebRepository[] = []
  let branches: { name: string }[] = []
  let enabled = true
  let generation = 0
  // Action de groupe ouverte (renommage, déplacement, suppression, membres) ou dialogue « Nouveau projet » :
  // le rafraîchissement périodique est suspendu pour ne pas refermer ce que l'utilisateur est en train de saisir.
  let groupActionOpen = false
  let showProjectCreate = false
  let createdProject = ''
  let message = ''
  let busy = false
  let loading = true
  let creating = false
  let showCreate = false
  let error = ''
  let sourceMode: 'github' | 'git' | 'image' = 'github'
  /** Source « Dépôt Git » créée (ou retrouvée) dans le formulaire : le serveur en déduit le dépôt. */
  let gitSource: WebGitSourceView | null = null
  let sourceKind: 'nextjs' | 'auto' | 'dockerfile' | 'worker' = 'nextjs'
  let imageReference = ''
  let imageWorker = false
  let imagePort = 3000
  let dockerfile = 'Dockerfile'
  let name = ''
  let repositoryId = ''
  let repository = ''
  let branch = 'main'
  let rootDirectory = ''
  let healthPath = '/'
  let autoDeploy = true
  let env: WebAppEnvironmentDraft[] = []
  let disposed = false
  let repositoryLoaded = false
  let repositorySearch = ''
  let repositoryFilter = ''
  let repositoryPage = 1
  let repositoryHasMore = false
  let repositoryBusy = false
  let repositoryTotal: number | undefined
  let repositoryTruncated = false
  let selectedRepository: WebRepository | null = null
  let branchPage = 1
  let branchHasMore = false
  let branchBusy = false
  let repositoryRequest = 0
  // Connexions GitHub (plateforme) : le sélecteur n'apparaît qu'avec plusieurs connexions actives.
  let githubConnections: WebGitHubConnectionSummary[] = []
  let githubConnectionsLoaded = false
  let githubConnectionId = WEB_GITHUB_PRINCIPAL
  $: activeGithubConnections = webGithubActiveConnections(githubConnections)
  $: repositoryChoices =
    selectedRepository && !repositories.some((r) => r.id === selectedRepository?.id)
      ? [selectedRepository, ...repositories]
      : repositories
  let branchRequest = 0
  // Création d'application et connexion GitHub : administrateurs de la plateforme uniquement.
  $: canCreate = $webAccessStore
    ? !!$webAccessStore.globalCapabilities['app.create']
    : webLocalPlatformAdmin($userStore)
  $: canGithub = $webAccessStore
    ? !!$webAccessStore.globalCapabilities['platform.github']
    : webLocalPlatformAdmin($userStore)
  $: canCreateProject = $webAccessStore
    ? !!$webAccessStore.globalCapabilities['project.create']
    : webLocalPlatformAdmin($userStore)
  $: globalCapabilities =
    $webAccessStore?.globalCapabilities ??
    (webLocalPlatformAdmin($userStore) ? webCapabilities('platform') : WEB_NO_CAPABILITIES)

  /** La dernière requête lancée fait foi : un rafraîchissement lent ne remplace jamais une liste plus récente. */
  const load = async () => {
    if (disposed) return
    const request = ++generation
    busy = true
    try {
      const value = await webProjectGroupsLoad()
      if (disposed || request !== generation) return
      groups = value
      enabled = !!value.enabled
      capabilities = value.capabilities
      error = ''
    } catch (e) {
      if (!disposed && request === generation) error = webError(e)
    } finally {
      if (request === generation) {
        busy = false
        loading = false
      }
    }
  }
  const projectCreated = async (project: { id: string; name: string }) => {
    message = `Projet « ${project.name} » créé. Vous en êtes propriétaire.`
    createdProject = project.id
    await refreshWebAccess()
    await load()
  }
  const loadRepositories = async (next = 1) => {
    if (disposed) return
    const request = ++repositoryRequest
    const search = next === 1 ? repositorySearch.trim() : repositoryFilter
    repositoryBusy = true
    try {
      const value = await webRequest<WebRepositoryPage>('/repositories', {
        query: { page: next, perPage: 50, search, ...webGithubConnectionQuery(githubConnectionId) },
      })
      if (disposed || request !== repositoryRequest) return
      repositories =
        next === 1
          ? value.items
          : [...repositories, ...value.items.filter((r) => !repositories.some((old) => old.id === r.id))]
      repositoryPage = value.page || next
      repositoryFilter = search
      repositoryHasMore = !!value.hasMore
      repositoryTotal = value.totalItems
      repositoryTruncated = !!value.truncated
      repositoryLoaded = true
    } catch (e) {
      if (request === repositoryRequest) error = webError(e)
    } finally {
      if (request === repositoryRequest) repositoryBusy = false
    }
  }
  const loadGithubConnections = async () => {
    if (!canGithub || githubConnectionsLoaded) return
    try {
      const value = await webGithubConnections()
      if (disposed) return
      githubConnections = value.items || []
      githubConnectionId = webGithubDefaultSelection(githubConnections, githubConnectionId)
    } catch {
      // Liste indisponible : la connexion principale reste utilisée, comme avant les connexions multiples.
      githubConnections = []
    } finally {
      githubConnectionsLoaded = true
    }
  }
  /** Changer de connexion change l'installation : dépôts et branches chargés sont invalidés. */
  const selectGithubConnection = async () => {
    ++branchRequest
    repositories = []
    selectedRepository = null
    repositoryId = ''
    repository = ''
    branches = []
    branchPage = 1
    branchHasMore = false
    repositoryLoaded = false
    repositoryHasMore = false
    repositoryTotal = undefined
    repositoryTruncated = false
    await loadRepositories(1)
  }
  const openCreate = async () => {
    if (!canCreate) return
    showCreate = !showCreate
    if (showCreate && sourceMode === 'github' && capabilities?.githubConfigured) {
      await loadGithubConnections()
      if (!repositoryLoaded) await loadRepositories()
    }
  }
  const loadBranches = async (next = 1, request = branchRequest) => {
    if (!repositoryId) return
    branchBusy = true
    try {
      const value = await webRequest<WebBranchPage>(`/repositories/${encodeURIComponent(repositoryId)}/branches`, {
        query: { page: next, perPage: 50, ...webGithubConnectionQuery(githubConnectionId) },
      })
      if (disposed || request !== branchRequest) return
      branches =
        next === 1
          ? value.items
          : [...branches, ...value.items.filter((item) => !branches.some((old) => old.name === item.name))]
      branchPage = value.page || next
      branchHasMore = !!value.hasMore
    } catch (e) {
      if (request === branchRequest) error = webError(e)
    } finally {
      if (request === branchRequest) branchBusy = false
    }
  }
  const selectRepository = async () => {
    ++branchRequest
    const selected = repositoryChoices.find((r) => String(r.id) === repositoryId)
    selectedRepository = selected || null
    branches = []
    branchPage = 1
    branchHasMore = false
    if (!selected) return
    repository = selected.fullName
    branch = selected.defaultBranch
    await loadBranches()
  }
  const create = async (event: SubmitEvent) => {
    event.preventDefault()
    creating = true
    error = ''
    try {
      const result = await webRequest<{ app: WebApp }>('', {
        method: 'POST',
        body: {
          name: name.trim(),
          ...(sourceMode === 'git' ? { source: 'git', gitSourceId: gitSource?.id || '' } : {}),
          repositoryId: sourceMode === 'github' ? repositoryId || undefined : undefined,
          // Absente = connexion principale : un serveur sans connexions multiples reçoit la même requête qu'avant.
          ...(sourceMode === 'github' && githubConnectionId && githubConnectionId !== WEB_GITHUB_PRINCIPAL
            ? { githubConnectionId }
            : {}),
          repository: sourceMode === 'github' ? repository.trim() : '',
          runtime:
            sourceMode === 'image'
              ? {
                  kind: imageWorker ? 'worker' : 'image',
                  image: imageReference.trim(),
                  port: imageWorker ? null : imagePort,
                }
              : sourceKind === 'auto'
                ? { kind: 'auto', port: 3000 }
                : { kind: sourceKind, dockerfile, port: sourceKind === 'worker' ? null : 3000 },
          branch: branch.trim(),
          rootDirectory: rootDirectory.trim(),
          healthPath: healthPath.trim(),
          autoDeploy: sourceMode !== 'image' && autoDeploy,
          env: env.filter((v) => v.name.trim()).map((v) => ({ ...v, name: v.name.trim() })),
        },
      })
      await goto(`/applications/${result.app.id}`)
    } catch (e) {
      error = webError(e)
    } finally {
      creating = false
    }
  }
  onMount(() => {
    disposed = false
    void load().then(() => {
      if (!disposed && canCreate && new URLSearchParams(window.location.search).get('create') === '1') void openCreate()
    })
    const timer = setInterval(() => {
      if (!document.hidden && !busy && !showCreate && !groupActionOpen && !showProjectCreate) void load()
    }, 10000)
    return () => {
      disposed = true
      clearInterval(timer)
    }
  })
</script>

<svelte:head><title>Applications web - Kolab Hosting</title></svelte:head>
<header class="web-header">
  <div class="web-title">
    <span class="web-eyebrow">Hébergement · Applications & services</span>
    <h1>Applications web</h1>
    <p class="web-subtitle">
      Depuis GitHub ou une image immuable. Hébergez vos sites et vos workers, suivez leurs versions et leurs
      déploiements.
    </p>
  </div>
  {#if canGithub || canCreate}<div class="web-actions">
      {#if canGithub}<a class="web-button" href="/applications/settings">Connexion GitHub</a>{/if}{#if canCreate}<button
          class="web-button web-button--primary"
          onclick={openCreate}
          disabled={!enabled || loading}
          aria-expanded={showCreate}>{showCreate ? 'Fermer' : 'Nouvelle application'}</button
        >{/if}
    </div>{/if}
</header>
{#if enabled && (groups || loading)}<WebHostingSummary {groups} loading={!groups && loading} />{/if}
{#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
{#if !enabled}<div class="web-alert" role="status">L’hébergement web n’est pas activé sur ce serveur.</div>{/if}
{#if capabilities?.error}<div class="web-alert" role="status">{capabilities.error}</div>{/if}
{#if capabilities && (!capabilities.workerReady || !capabilities.rootlessReady)}<div class="web-alert" role="status">
    Le moteur de déploiement se prépare. Les applications déjà publiées conservent leur version actuelle ; une nouvelle
    publication attendra que le moteur soit prêt.
  </div>{/if}
{#if showCreate && canCreate}
  <section class="web-panel web-panel--accent">
    <div class="web-row">
      <h2>Nouvelle application</h2>
      <span class="web-eyebrow">01 · Connecter le projet</span>
    </div>
    {#if sourceMode === 'github' && !capabilities?.githubConfigured}<p class="web-help">
        Un dépôt public peut être déployé dès maintenant. <a href="/applications/settings">Connectez GitHub</a> pour les dépôts
        privés et les mises à jour automatiques à chaque push.
      </p>{/if}
    <!-- Hors du formulaire : le panneau « Dépôt Git » contient son propre formulaire (pas de formulaires imbriqués). -->
    <div class="web-form-grid">
      <label class="web-field web-field--full"
        ><span>Source de l’application</span><select
          bind:value={sourceMode}
          disabled={creating}
          onchange={() => {
            error = ''
            if (sourceMode === 'github' && capabilities?.githubConfigured)
              void loadGithubConnections().then(() => {
                if (!repositoryLoaded) void loadRepositories()
              })
          }}
          ><option value="github">Dépôt GitHub</option><option value="git">Dépôt Git (clé de déploiement)</option
          ><option value="image">Image Docker immuable · sans GitHub</option></select
        ></label
      >
    </div>
    {#if sourceMode === 'git'}
      {#if capabilities?.gitAvailable === false}<p class="web-help" role="status">
          Le serveur web ne dispose pas de git/openssh : installez git et openssh-client sur le worker avant le premier
          déploiement.
        </p>{/if}
      <WebGitSource
        canConfigureRepository={canCreate}
        canManage={canCreate}
        {branch}
        onsource={(value) => (gitSource = value)}
        onbranch={(value) => (branch = value)}
        ondelete={() => (gitSource = null)}
      />
    {/if}
    <form class="web-form" onsubmit={create}>
      <div class="web-form-grid">
        <label class="web-field"
          ><span>Nom de l’application</span><input
            bind:value={name}
            required
            maxlength="80"
            placeholder="Mon site"
            disabled={creating}
            autocomplete="off"
          /></label
        >
        {#if sourceMode !== 'image'}
          <label class="web-field"
            ><span>Construction</span><select bind:value={sourceKind} disabled={creating}
              ><option value="nextjs">Next.js</option><option value="auto">Automatique (Railpack)</option><option
                value="dockerfile">Dockerfile</option
              ><option value="worker">Worker depuis un Dockerfile</option></select
            ></label
          >
          {#if sourceKind === 'auto' && capabilities?.railpackAvailable === false}<p class="web-help" role="status">
              Railpack n’est pas installé sur le worker : la construction automatique échouera tant que l’administrateur
              ne l’a pas installé.
            </p>{/if}
          {#if sourceKind === 'dockerfile' || sourceKind === 'worker'}<label class="web-field"
              ><span>Chemin du Dockerfile</span><input
                bind:value={dockerfile}
                required
                maxlength="200"
                disabled={creating}
                placeholder="Dockerfile"
              /></label
            >{/if}
          {#if sourceMode === 'github' && capabilities?.githubConfigured && activeGithubConnections.length > 1}<label
              class="web-field web-field--full"
              ><span>Connexion GitHub</span><select
                bind:value={githubConnectionId}
                onchange={selectGithubConnection}
                disabled={creating || repositoryBusy}
                >{#each activeGithubConnections as item (item.id)}<option value={item.id}
                    >{webGithubConnectionOption(item)}</option
                  >{/each}</select
              ><span class="web-help"
                >Seuls les dépôts autorisés dans l’installation de cette connexion sont listés.</span
              ></label
            >{/if}
          {#if sourceMode === 'github' && capabilities?.githubConfigured}<div class="web-field web-field--full">
              <label for="repo-search">Chercher dans les dépôts autorisés</label>
              <div class="web-actions">
                <input
                  id="repo-search"
                  bind:value={repositorySearch}
                  onkeydown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      void loadRepositories(1)
                    }
                  }}
                  placeholder="Nom du dépôt ou de l’organisation"
                  disabled={creating}
                /><button class="web-button" type="button" onclick={() => loadRepositories(1)} disabled={repositoryBusy}
                  >{repositoryBusy ? 'Recherche…' : 'Rechercher'}</button
                >
              </div>
              <span class="web-help"
                >{repositories.length} dépôt(s) chargé(s){repositoryTotal == null ? '' : ` sur ${repositoryTotal}`}.
                Seuls les dépôts accessibles à cette installation sont proposés.</span
              >
              {#if repositoryTruncated}<span class="web-help"
                  >GitHub a renvoyé un inventaire partiel. Vous pouvez aussi saisir le nom complet du dépôt.</span
                >{/if}
            </div>{/if}
          {#if sourceMode === 'github' && repositoryChoices.length}<label class="web-field"
              ><span>Dépôt GitHub autorisé</span><select
                bind:value={repositoryId}
                onchange={selectRepository}
                disabled={creating}
                ><option value="">Saisir un dépôt public</option>{#each repositoryChoices as repo}<option
                    value={String(repo.id)}>{repo.fullName}{repo.private ? ' · privé' : ''}</option
                  >{/each}</select
              >{#if repositoryHasMore}<button
                  class="web-button"
                  type="button"
                  onclick={() => loadRepositories(repositoryPage + 1)}
                  disabled={repositoryBusy}>Charger d’autres dépôts</button
                >{/if}</label
            >{/if}
          {#if sourceMode === 'github'}<label class="web-field"
              ><span>Dépôt GitHub</span><input
                bind:value={repository}
                required
                placeholder="organisation/mon-site"
                pattern="[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+"
                disabled={creating || !!repositoryId}
                autocomplete="off"
              /><span class="web-help">Nom du propriétaire et du dépôt, sans URL ni jeton d’accès.</span></label
            >{/if}
          <label class="web-field"
            ><span>Branche à publier</span><input
              bind:value={branch}
              required
              maxlength="255"
              list="web-branches"
              placeholder="main"
              disabled={creating}
            /><datalist id="web-branches"
              >{#each branches as item}<option value={item.name}></option>{/each}</datalist
            >{#if branchHasMore}<button
                class="web-button"
                type="button"
                onclick={() => loadBranches(branchPage + 1)}
                disabled={branchBusy}>Charger d’autres branches</button
              >{/if}<span class="web-help"
              >Vous pouvez saisir une branche exacte, même si elle n’est pas encore chargée.</span
            ></label
          >
          <label class="web-field"
            ><span>Dossier du projet</span><input
              bind:value={rootDirectory}
              placeholder="Racine du dépôt"
              disabled={creating}
            /><span class="web-help">Pour un monorepo, par exemple <code>apps/site</code>.</span></label
          >
        {:else}
          <label class="web-field web-field--full"
            ><span>Image et digest SHA256</span><input
              bind:value={imageReference}
              required
              maxlength="500"
              pattern={'.*@sha256:[a-f0-9]{64}'}
              placeholder="ghcr.io/organisation/application@sha256:…"
              disabled={creating}
              autocomplete="off"
              spellcheck="false"
            /><span class="web-help"
              >Une référence avec digest complet garantit la version déployée. Les tags seuls, comme latest, sont
              refusés. Les identifiants d’un registre privé se configurent ensuite dans Moteur.</span
            ></label
          >
          <label class="web-field"
            ><span>Type de service</span><select bind:value={imageWorker} disabled={creating}
              ><option value={false}>Site ou API avec une route HTTP</option><option value={true}
                >Worker en arrière-plan, sans route HTTP</option
              ></select
            ></label
          >
          {#if !imageWorker}<label class="web-field"
              ><span>Port HTTP du conteneur</span><input
                type="number"
                bind:value={imagePort}
                min="1"
                max="65535"
                step="1"
                required
                disabled={creating}
              /></label
            >{/if}
        {/if}
        {#if (sourceMode !== 'image' && sourceKind !== 'worker') || (sourceMode === 'image' && !imageWorker)}
          <label class="web-field"
            ><span>Chemin du contrôle de santé</span><input
              bind:value={healthPath}
              required
              placeholder="/"
              pattern="/.*"
              disabled={creating}
            /><span class="web-help">La nouvelle version doit répondre avant de recevoir le trafic.</span></label
          >
        {/if}
      </div>
      {#if sourceMode !== 'image'}<label class="web-check"
          ><input type="checkbox" bind:checked={autoDeploy} disabled={creating} /> Déployer automatiquement les pushes
          sur cette branche{sourceMode === 'git' ? ' (webhook signé à configurer chez l’hébergeur)' : ''}</label
        >{/if}
      <details class="web-details">
        <summary>Variables d’environnement</summary><WebAppEnvironment bind:rows={env} disabled={creating} />
      </details>
      <p class="web-help">
        {#if sourceMode !== 'image' && sourceKind === 'nextjs'}Next.js utilise le lockfile du projet et sa sortie
          standalone, ou next start.
        {:else if sourceMode !== 'image' && sourceKind === 'auto'}Railpack détecte le langage du dossier du projet
          (Node, Vite, Astro, site statique, Python, Go, PHP, Ruby…), le construit dans le builder isolé, puis le
          démarre avec la variable PORT=3000.
        {:else if sourceMode !== 'image'}Le Dockerfile est construit dans le contexte du dépôt.
        {:else}L’image est récupérée par digest et s’exécute sous UID 10001 avec une racine en lecture seule.
        {/if}La configuration complète du moteur, Compose, les variables et les volumes restent accessibles après la
        création. Une route HTTPS sera publiée pour les services HTTP.
      </p>
      <div class="web-actions">
        <button
          class="web-button web-button--primary"
          type="submit"
          disabled={creating || (sourceMode === 'git' && !gitSource)}
          >{creating ? 'Création…' : 'Créer l’application'}</button
        ><button class="web-button" type="button" onclick={() => (showCreate = false)} disabled={creating}
          >Annuler</button
        >
      </div>
    </form>
  </section>
{/if}
<section class="web-group-section" aria-labelledby="web-groups-title">
  <div class="web-row">
    <h2 id="web-groups-title">Applications web</h2>
    {#if canCreateProject}<button
        class="web-button"
        type="button"
        onclick={() => (showProjectCreate = true)}
        disabled={!enabled || loading}>+ Nouveau projet</button
      >{/if}
  </div>
  {#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}
  <WebProjectGroups
    data={groups}
    {loading}
    {busy}
    {globalCapabilities}
    canCreateApp={enabled && canCreate && !showCreate}
    canCreateProject={enabled && canCreateProject}
    forceOpen={createdProject}
    bind:active={groupActionOpen}
    oncreateapp={openCreate}
    oncreateproject={() => (showProjectCreate = true)}
    onchanged={() => {
      message = ''
      return load()
    }}
  />
</section>
{#if canCreateProject}<WebProjectCreateDialog bind:open={showProjectCreate} oncreated={projectCreated} />{/if}

<style>
  .web-group-section {
    display: grid;
    gap: 0.75rem;
  }
</style>
