<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { githubManageUrl } from '$lib/webConsoleFlow'
  import {
    webDate,
    webError,
    webRequest,
    webGithubConnections,
    type WebGitHubConnectionSummary,
    type WebGitHubHealth,
    type WebRepository,
    type WebRepositoryPage,
    type WebBranchPage,
  } from '$lib/webApps'
  import {
    WEB_GITHUB_PRINCIPAL,
    webGithubActiveConnections,
    webGithubConnectionHref,
    webGithubConnectionLabel,
    webGithubConnectionOption,
    webGithubConnectionQuery,
  } from '$lib/webGithubConnections'

  export let repository: string
  export let branch: string
  export let autoDeploy: boolean
  export let onbranch: ((value: string) => void) | undefined = undefined
  /** Connexion GitHub de l'application (liée) : 'default' = principale. Réservé à la plateforme. */
  export let githubConnectionId = WEB_GITHUB_PRINCIPAL
  /** Le sélecteur de connexion est modifiable (formulaire de configuration non verrouillé). */
  export let connectionEditable = true
  let connections: WebGitHubConnectionSummary[] = []
  let connectionsLoaded = false
  let connectionsError = ''
  let health: WebGitHubHealth | null = null
  let selected: WebRepository | null = null
  let verified = false
  let busy = false
  let error = ''
  let branches: string[] = []
  let branchPage = 1
  let branchHasMore = false
  let branchBusy = false
  let branchSearch = ''
  let choice = ''
  let observed = ''
  let request = 0
  let disposed = false
  $: manageUrl = githubManageUrl(health?.manageUrl)
  $: choices = branches.filter((value) => value.toLocaleLowerCase().includes(branchSearch.toLocaleLowerCase()))
  $: activeConnections = webGithubActiveConnections(connections)
  $: currentConnection = connections.find((item) => item.id === (githubConnectionId || WEB_GITHUB_PRINCIPAL))
  // La connexion de l'application a été supprimée (ou n'existe plus) : un administrateur doit en choisir une autre.
  $: connectionMissing =
    connectionsLoaded &&
    !connectionsError &&
    !currentConnection &&
    (connections.length > 0 || (githubConnectionId || WEB_GITHUB_PRINCIPAL) !== WEB_GITHUB_PRINCIPAL)
  $: connectionDisabled = !!currentConnection?.disabled
  $: showConnectionPicker =
    connectionsLoaded && (activeConnections.length > 1 || connectionMissing || connectionDisabled)
  // Changer de connexion ou de dépôt invalide la vérification : dépôts et branches dépendent de l'installation.
  $: watchKey = `${githubConnectionId || WEB_GITHUB_PRINCIPAL}|${repository}`
  $: if (watchKey !== observed) {
    observed = watchKey
    ++request
    verified = false
    selected = null
    health = null
    branches = []
    error = ''
    busy = false
    branchBusy = false
  }
  const loadBranches = async (next: number, generation = request) => {
    if (!selected || branchBusy) return
    branchBusy = true
    try {
      const value = await webRequest<WebBranchPage>(
        `/repositories/${encodeURIComponent(String(selected.id))}/branches`,
        { query: { page: next, perPage: 50, ...webGithubConnectionQuery(githubConnectionId) } }
      )
      if (disposed || generation !== request) return
      branches = [...new Set([...(next === 1 ? [] : branches), ...value.items.map((item) => item.name)])]
      branchPage = value.page
      branchHasMore = value.hasMore
    } catch (e) {
      if (!disposed && generation === request) error = webError(e)
    } finally {
      if (generation === request) branchBusy = false
    }
  }
  const verify = async () => {
    if (busy) return
    const generation = ++request
    const repo = repository.trim()
    busy = true
    error = ''
    verified = false
    selected = null
    branches = []
    branchBusy = false
    choice = ''
    try {
      const connectionQuery = webGithubConnectionQuery(githubConnectionId)
      const result = await webRequest<WebGitHubHealth>('/github/health', { query: connectionQuery })
      if (disposed || generation !== request) return
      health = result
      if (!result.configured || !result.ok)
        throw new Error(result.error || 'Terminez la connexion GitHub avant de vérifier ce dépôt.')
      let page = 1
      let hasMore = true
      while (hasMore && page <= 20) {
        const value = await webRequest<WebRepositoryPage>('/repositories', {
          query: { search: repo, page, perPage: 50, ...connectionQuery },
        })
        if (disposed || generation !== request) return
        const found = value.items.find((item) => item.fullName.toLowerCase() === repo.toLowerCase())
        if (found) {
          selected = found
          break
        }
        hasMore = value.hasMore
        page++
        if (value.truncated && !hasMore)
          throw new Error(
            'La sélection GitHub dépasse la limite de recherche. Limitez les dépôts autorisés pour vérifier celui-ci.'
          )
      }
      verified = true
      if (selected) await loadBranches(1, generation)
    } catch (e) {
      if (!disposed && generation === request) error = webError(e)
    } finally {
      if (generation === request) busy = false
    }
  }
  const loadConnections = async () => {
    try {
      const value = await webGithubConnections()
      if (disposed) return
      connections = value.items || []
      connectionsError = ''
    } catch (e) {
      if (!disposed) connectionsError = webError(e)
    } finally {
      if (!disposed) connectionsLoaded = true
    }
  }
  onMount(() => {
    void loadConnections()
  })
  onDestroy(() => {
    disposed = true
    ++request
  })
</script>

<section class="web-panel source-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Source et automatisation</span>
      <h2>Accès à ce projet</h2>
    </div>
    <button class="web-button" type="button" onclick={verify} disabled={busy}
      >{busy ? 'Vérification…' : 'Vérifier le dépôt et les branches'}</button
    >
  </div>
  <div class="source-identity">
    <div class="source-icon" aria-hidden="true">↗</div>
    <div>
      <a
        href={`https://github.com/${repository.split('/').map(encodeURIComponent).join('/')}`}
        target="_blank"
        rel="noopener noreferrer">{repository}</a
      >
      <p class="web-help">
        <code>{branch}</code> · {autoDeploy ? 'Déploiements sur push activés' : 'Déploiements manuels'}
      </p>
    </div>
    {#if selected}<span class="web-status" data-tone="success"
        >{selected.private ? 'Dépôt privé autorisé' : 'Dépôt autorisé'}</span
      >{/if}
  </div>
  {#if showConnectionPicker}<label class="web-field"
      ><span>Connexion GitHub</span><select bind:value={githubConnectionId} disabled={!connectionEditable}
        >{#if connectionMissing}<option value={githubConnectionId}>Connexion introuvable</option
          >{/if}{#each connections as item (item.id)}<option
            value={item.id}
            disabled={item.disabled || !item.configured}
            >{webGithubConnectionOption(item)}{item.disabled ? ' · désactivée' : ''}</option
          >{/each}</select
      ><span class="web-help"
        >Changer de connexion change les identifiants utilisés pour lire le dépôt. Le dépôt doit être autorisé dans
        l’installation choisie.</span
      ></label
    >{:else if currentConnection && connections.length > 1}<p class="web-help">
      Connexion GitHub : <a href={webGithubConnectionHref(currentConnection.id)}
        >{webGithubConnectionLabel(currentConnection)}</a
      >
    </p>{/if}
  {#if connectionMissing}<div class="web-alert web-alert--error" role="alert" data-notice="off">
      Connexion GitHub introuvable : choisissez-en une autre.
    </div>{:else if connectionDisabled}<div class="web-alert" role="status">
      Cette connexion GitHub est désactivée : l’application ne peut pas être déployée. Choisissez une connexion active.
    </div>{/if}
  {#if connectionsError}<div class="web-alert" role="status">
      Liste des connexions GitHub indisponible : {connectionsError}
      <button class="web-button" type="button" onclick={loadConnections}>Réessayer</button>
    </div>{/if}
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if verified && !selected}<div class="web-alert" role="status">
      Ce dépôt ne figure pas parmi les dépôts accessibles à l’installation. Autorisez-le sur GitHub, puis relancez le
      contrôle.
    </div>{/if}
  {#if selected}
    <p class="web-help" role="status">
      Lecture du code vérifiée pour {health?.installation?.account || 'cette installation'} le {webDate(
        health?.checkedAt
      )}. {branches.includes(branch)
        ? 'La branche suivie a été trouvée.'
        : branchHasMore
          ? 'Chargez les branches suivantes pour retrouver la branche suivie.'
          : 'La branche suivie est absente des branches du dépôt.'}
    </p>
    {#if onbranch}<div class="source-branches">
        <label class="web-field"
          ><span>Filtrer les branches chargées</span><input
            bind:value={branchSearch}
            placeholder="Rechercher une branche…"
          /></label
        ><label class="web-field"
          ><span>Choisir une branche</span><select bind:value={choice}
            ><option value="">{choices.length} branche(s) disponible(s)</option>{#each choices as name}<option
                value={name}>{name}</option
              >{/each}</select
          ></label
        ><button
          class="web-button"
          type="button"
          onclick={() => {
            if (choice) onbranch?.(choice)
          }}
          disabled={!choice}>Utiliser cette branche</button
        >
      </div>{/if}
    <div class="web-actions">
      {#if branchHasMore}<button
          class="web-button"
          type="button"
          onclick={() => loadBranches(branchPage + 1)}
          disabled={branchBusy}
          >{branchBusy ? 'Chargement…' : `Charger plus de branches (${branches.length} chargées)`}</button
        >{/if}{#if onbranch}<span class="web-help"
          >Enregistrez la configuration pour confirmer un changement de branche.</span
        >{/if}
    </div>
  {/if}
  <div class="web-actions">
    {#if manageUrl}<a class="web-button" href={manageUrl} target="_blank" rel="noopener noreferrer"
        >Gérer l’accès sur GitHub ↗</a
      >{/if}<a class="web-button" href={webGithubConnectionHref(currentConnection?.id)}
      >Connexion et permissions GitHub</a
    >
  </div>
</section>

<style>
  .source-panel {
    display: grid;
    gap: 1rem;
  }
  .source-identity {
    display: flex;
    gap: 0.8rem;
    align-items: center;
    flex-wrap: wrap;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
  }
  .source-identity > div:nth-child(2) {
    flex: 1;
    min-width: 0;
  }
  .source-identity a {
    overflow-wrap: anywhere;
    font-weight: 600;
    font-size: 0.8125rem;
  }
  .source-identity p {
    margin: 0.4rem 0 0;
  }
  .source-icon {
    height: 2.3rem;
    width: 2.3rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    display: grid;
    place-items: center;
    color: var(--web-muted);
  }
  .source-branches {
    display: grid;
    grid-template-columns: 1fr 1fr auto;
    gap: 0.75rem;
    align-items: end;
  }
  @media (max-width: 750px) {
    .source-branches {
      grid-template-columns: 1fr;
    }
  }
</style>
