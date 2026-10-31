<script lang="ts">
  import { onMount, tick } from 'svelte'
  import WebGitHubConnectionCard from '$components/webApps/WebGitHubConnectionCard.svelte'
  import {
    webRequest,
    webError,
    webGithubConnections,
    webGithubConnectionCreate,
    type WebGitHubConnection,
    type WebGitHubConnectionDeleted,
    type WebGitHubConnectionSummary,
    type WebGitHubConnectResult,
    type WebGitHubManifestStart,
    type WebGitHubInstallStart,
    githubDestination,
  } from '$lib/webApps'
  import { githubManageUrl } from '$lib/webConsoleFlow'
  import {
    WEB_GITHUB_LABEL_MAX,
    WEB_GITHUB_MAX_CONNECTIONS,
    webGithubActiveConnections,
    webGithubCompletionMessage,
    webGithubConnectionAnchor,
    webGithubConnectionFromResponse,
    webGithubConnectionLabel,
    webGithubConnectionsHeading,
    webGithubConnectStartBody,
    webGithubLabelError,
  } from '$lib/webGithubConnections'

  let connections: WebGitHubConnectionSummary[] = []
  let maxConnections = WEB_GITHUB_MAX_CONNECTIONS
  let loading = true
  let listError = ''
  let error = ''
  let message = ''
  let deletedNotice: { text: string; manageUrl: string } | null = null
  let healthRefresh = 0
  let connection: WebGitHubConnection | null = null
  let connectionError = ''
  let connecting = false
  let resuming = false
  let checkingInstallation = false
  let disposed = false
  // Ajout par l'assistant (non destructif : les connexions existantes ne sont pas modifiées).
  let addOpen = false
  let addOwner: 'personal' | 'organization' = 'personal'
  let organization = ''
  let addLabel = ''
  // Ajout manuel d'une GitHub App existante.
  let manualOpen = false
  let manualPanel: HTMLElement | undefined
  let manualSaving = false
  let manualLabel = '',
    appId = '',
    slug = '',
    installationId = '',
    privateKey = '',
    webhookSecret = ''
  let created: WebGitHubConnectionSummary | null = null
  let copiedCreated = false

  const load = async () => {
    try {
      const value = await webGithubConnections()
      if (disposed) return
      connections = value.items || []
      maxConnections = value.max || WEB_GITHUB_MAX_CONNECTIONS
      listError = ''
    } catch (e) {
      if (!disposed) listError = webError(e)
    } finally {
      if (!disposed) loading = false
    }
  }
  const loadConnection = async () => {
    try {
      const value = await webRequest<WebGitHubConnection>('/github/connect/status')
      if (!disposed) {
        connection = value
        connectionError = ''
      }
    } catch (e) {
      if (!disposed) connectionError = webError(e)
    }
  }
  const refreshAll = async () => {
    await Promise.all([load(), loadConnection()])
    healthRefresh++
  }
  const resetFeedback = () => {
    error = ''
    message = ''
    deletedNotice = null
  }
  /** Ouvre l'assistant GitHub : ajout par défaut, remplacement uniquement depuis « Recréer la GitHub App ». */
  const startWizard = async (input: Parameters<typeof webGithubConnectStartBody>[0]) => {
    connecting = true
    resetFeedback()
    connectionError = ''
    try {
      const value = await webRequest<WebGitHubManifestStart>('/github/connect/start', {
        method: 'POST',
        body: webGithubConnectStartBody(input),
      })
      const form = document.createElement('form')
      form.method = 'POST'
      form.action = githubDestination(value.createUrl, 'create')
      const manifest = document.createElement('input')
      manifest.type = 'hidden'
      manifest.name = 'manifest'
      manifest.value = JSON.stringify(value.manifest)
      form.appendChild(manifest)
      document.body.appendChild(form)
      form.submit()
    } catch (e) {
      error = webError(e)
      connecting = false
    }
  }
  const addConnection = (event: SubmitEvent) => {
    event.preventDefault()
    const org = addOwner === 'organization' ? organization.trim() : ''
    if (addOwner === 'organization' && !org) {
      error = 'Indiquez le nom de l’organisation propriétaire de la GitHub App.'
      return
    }
    const label = addLabel.trim()
    const invalid = label ? webGithubLabelError(label) : ''
    if (invalid) {
      error = invalid
      return
    }
    void startWizard({ mode: 'add', organization: org, label })
  }
  const replaceConnection = (target: WebGitHubConnectionSummary, input: { organization: string }) =>
    startWizard({
      mode: 'replace',
      connectionId: target.id,
      organization: input.organization,
      // En remplacement, le serveur conserve le libellé existant : il n'est transmis que pour l'affichage de l'assistant.
      label: target.label || '',
    })
  const resumeInstallation = async () => {
    if (!connection?.pending) return
    resuming = true
    error = ''
    try {
      const value = await webRequest<WebGitHubInstallStart>('/github/connect/install/start', {
        method: 'POST',
        body: { sessionId: connection.pending.sessionId },
      })
      window.location.assign(githubDestination(value.installUrl, 'install'))
    } catch (e) {
      error = webError(e)
      resuming = false
      void loadConnection()
    }
  }
  const checkInstallation = async () => {
    if (!connection?.pending) return
    const pendingLabel = connection.pending.label || ''
    const pendingMode = connection.pending.mode
    checkingInstallation = true
    resetFeedback()
    try {
      const value = await webRequest<WebGitHubConnectResult>('/github/connect/install/check', {
        method: 'POST',
        body: { sessionId: connection.pending.sessionId },
      })
      if (!value.connected)
        throw new Error('GitHub n’a pas confirmé l’installation. Choisissez d’abord les dépôts sur GitHub.')
      await refreshAll()
      message = webGithubCompletionMessage({
        label: value.label || pendingLabel,
        account: value.installation?.account,
        mode: value.mode || pendingMode,
        repositoryCount: value.repositoryCount,
      })
      await focusConnection(value.connectionId)
    } catch (e) {
      error = webError(e)
      void loadConnection()
    } finally {
      checkingInstallation = false
    }
  }
  const resetManual = () => {
    manualLabel = ''
    appId = ''
    slug = ''
    installationId = ''
    privateKey = ''
    webhookSecret = ''
  }
  const openManual = async () => {
    manualOpen = true
    addOpen = false
    created = null
    await tick()
    manualPanel?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const createManual = async (event: SubmitEvent) => {
    event.preventDefault()
    const label = manualLabel.trim()
    const invalid = label ? webGithubLabelError(label) : ''
    if (invalid) {
      error = invalid
      return
    }
    manualSaving = true
    resetFeedback()
    try {
      const before = new Set(connections.map((item) => item.id))
      const value = await webGithubConnectionCreate({
        ...(label ? { label } : {}),
        githubAppId: appId.trim(),
        githubAppSlug: slug.trim(),
        githubInstallationId: installationId.trim(),
        privateKey,
        webhookSecret,
      })
      await load()
      created = webGithubConnectionFromResponse(value) || connections.find((item) => !before.has(item.id)) || null
      // La réponse peut précéder la liste : reprendre la version listée (URL du webhook, compteurs).
      if (created) created = connections.find((item) => item.id === created?.id) || created
      resetManual()
      copiedCreated = false
      healthRefresh++
      message = `Connexion « ${webGithubConnectionLabel(created)} » ajoutée. Configurez son URL de webhook sur GitHub.`
    } catch (e) {
      error = webError(e)
    } finally {
      manualSaving = false
    }
  }
  const copyCreated = async () => {
    if (!created?.webhookUrl) return
    try {
      await navigator.clipboard.writeText(created.webhookUrl)
      copiedCreated = true
    } catch {
      error = 'La copie automatique est indisponible. Sélectionnez et copiez l’URL du webhook.'
    }
  }
  const changed = (text: string) => {
    resetFeedback()
    message = text
    void refreshAll()
  }
  const deleted = (result: WebGitHubConnectionDeleted, target: WebGitHubConnectionSummary) => {
    resetFeedback()
    message = `Connexion « ${webGithubConnectionLabel(target)} » supprimée.`
    deletedNotice = {
      text: result.notice || 'Supprimez aussi la GitHub App sur GitHub.',
      manageUrl: githubSettingsUrl(result.manageUrl),
    }
    if (created?.id === target.id) created = null
    void refreshAll()
  }
  /** Lien de gestion renvoyé par le serveur : installation (githubManageUrl) ou paramètres d'une App sur github.com. */
  const githubSettingsUrl = (value?: string) => {
    const safe = githubManageUrl(value)
    if (safe || !value) return safe
    try {
      const url = new URL(value)
      return url.protocol === 'https:' &&
        url.hostname === 'github.com' &&
        !url.username &&
        !url.password &&
        !url.search &&
        !url.hash &&
        // Page publique de l'App (manageUrl du serveur à la suppression) ou ses paramètres (compte ou organisation).
        (/^\/apps\/[A-Za-z0-9-]+$/.test(url.pathname) ||
          /^\/settings\/apps\/[A-Za-z0-9-]+$/.test(url.pathname) ||
          /^\/organizations\/[A-Za-z0-9-]+\/settings\/apps\/[A-Za-z0-9-]+$/.test(url.pathname))
        ? url.toString()
        : ''
    } catch {
      return ''
    }
  }
  const focusConnection = async (id?: string) => {
    const anchor = webGithubConnectionAnchor(id)
    if (!anchor) return
    await tick()
    document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  $: pending = connection?.pending
  $: pendingExpired = !!pending && Date.parse(pending.expiresAt) <= Date.now()
  $: canResume =
    !!pending &&
    !!pending.app &&
    !pendingExpired &&
    ['awaiting_installation', 'installation_failed'].includes(pending.status)
  $: pendingTarget = pending?.connectionId ? connections.find((item) => item.id === pending?.connectionId) : undefined
  $: pendingName = pending?.label || (pendingTarget ? webGithubConnectionLabel(pendingTarget) : '')
  $: active = webGithubActiveConnections(connections)
  $: configured = active.length > 0 || (!connections.length && !!connection?.configured)
  $: atLimit = connections.length >= maxConnections
  $: busyWizard = connecting || resuming || checkingInstallation
  $: firstConnection = !loading && !listError && !connections.length
  // Premier passage sans connexion : l'assistant d'ajout est ouvert d'emblée (une seule fois).
  let autoOpened = false
  $: if (firstConnection && !autoOpened) {
    autoOpened = true
    addOpen = true
  }
  onMount(() => {
    void Promise.all([load(), loadConnection()]).then(() => {
      const hash = window.location.hash.slice(1)
      if (hash.startsWith('connection-')) void focusConnection(hash.slice('connection-'.length))
    })
    return () => {
      disposed = true
    }
  })
</script>

<svelte:head><title>Connexions GitHub - Applications web</title></svelte:head>
<div class="web-actions">
  <a class="web-back" href="/applications">← Applications web</a><a class="web-button" href="/applications/recovery"
    >Reprise du serveur</a
  >
</div>
<header class="web-header">
  <div class="web-title">
    <span class="web-eyebrow">Applications web · Connexions</span>
    <h1>Votre code, vos déploiements</h1>
    <p class="web-subtitle">
      Connectez un ou plusieurs comptes GitHub, choisissez vos dépôts et publiez chaque push sur la branche de votre
      choix.
    </p>
  </div>
  <span class="web-status" data-tone={configured ? 'success' : 'neutral'}
    ><span class="web-status-dot" aria-hidden="true"></span>{configured
      ? active.length > 1
        ? `${active.length} connexions GitHub actives`
        : 'GitHub connecté'
      : 'Connexion à terminer'}</span
  >
</header>
{#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
{#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}
{#if deletedNotice}<div class="web-alert" role="status">
    {deletedNotice.text}{#if deletedNotice.manageUrl}
      <a class="web-button" href={deletedNotice.manageUrl} target="_blank" rel="noopener noreferrer"
        >Ouvrir la GitHub App sur GitHub ↗</a
      >{/if}
  </div>{/if}
{#if loading}<section class="web-panel web-empty" role="status"><p>Chargement des connexions…</p></section>{:else}
  <div class="web-layout">
    <div class="web-main">
      {#if pending}
        <section class="web-panel web-panel--accent" aria-label="Assistant GitHub en cours">
          <div class="web-alert" role="status">
            <strong
              >{pendingExpired
                ? 'L’assistant a expiré'
                : pending.status === 'manifest_failed'
                  ? 'Création à reprendre'
                  : pending.status === 'installation_failed'
                    ? 'Installation à vérifier'
                    : pending.app
                      ? 'GitHub App créée, dépôts à autoriser'
                      : 'Création démarrée sur GitHub'}{pendingName
                ? ` · ${pending.mode === 'replace' ? 'remplacement de' : 'ajout de'} « ${pendingName} »`
                : ''}</strong
            >
            <p>
              {pending.error ||
                (pendingExpired
                  ? 'Relancez l’assistant. Les connexions déjà actives restent inchangées.'
                  : pending.app
                    ? `Application ${pending.app.slug}. Terminez la sélection des dépôts pour activer cette connexion.`
                    : 'Si vous avez quitté GitHub avant la confirmation, vous pouvez relancer l’assistant.')}
            </p>
            {#if pending.mode === 'replace' && !pendingExpired}<p class="web-help">
                La GitHub App actuelle continue de servir les déploiements jusqu’à la validation complète de la
                nouvelle.
              </p>{/if}
            {#if canResume}<div class="web-actions">
                <button class="web-button web-button--primary" onclick={resumeInstallation} disabled={busyWizard}
                  >{resuming ? 'Ouverture…' : 'Choisir les dépôts sur GitHub'}</button
                ><button class="web-button" onclick={checkInstallation} disabled={busyWizard}
                  >{checkingInstallation ? 'Vérification…' : 'J’ai installé, vérifier la connexion'}</button
                >
              </div>{/if}
          </div>
        </section>
      {/if}
      {#if connectionError}<div class="web-alert" role="alert">
          {connectionError}<button class="web-button" onclick={loadConnection}>Réessayer</button>
        </div>{/if}

      <section class="web-panel github-list" aria-labelledby="github-connections-title">
        <div class="web-row">
          <div>
            <span class="web-eyebrow">GitHub App</span>
            <h2 id="github-connections-title">{webGithubConnectionsHeading(connections.length, maxConnections)}</h2>
          </div>
          <div class="web-actions">
            <button
              class="web-button web-button--primary"
              type="button"
              aria-expanded={addOpen}
              onclick={() => {
                addOpen = !addOpen
                if (addOpen) manualOpen = false
              }}
              disabled={atLimit || busyWizard}>Ajouter une connexion GitHub</button
            >{#if configured}<a class="web-button" href="/applications?create=1">Créer une application</a>{/if}
          </div>
        </div>
        <p class="web-help">
          Chaque connexion est une GitHub App installée sur un compte ou une organisation. Une organisation demande sa
          propre connexion.
        </p>
        {#if atLimit}<p class="web-help" role="status">
            Limite de {maxConnections} connexions atteinte : supprimez une connexion inutilisée pour en ajouter une autre.
          </p>{/if}
        {#if listError}<div class="web-alert web-alert--error" role="alert">
            {listError}<button class="web-button" onclick={load}>Réessayer</button>
          </div>{/if}

        {#if addOpen && !atLimit}
          <form class="web-form github-add" onsubmit={addConnection} aria-label="Ajouter une connexion GitHub">
            {#if firstConnection}<ol class="github-steps" aria-label="Étapes de connexion">
                <li data-state="current" aria-current="step">
                  <span>1</span>
                  <div>
                    <strong>Créer votre GitHub App</strong>
                    <p>Confirmez son nom sur GitHub.</p>
                  </div>
                </li>
                <li data-state="waiting">
                  <span>2</span>
                  <div>
                    <strong>Choisir les dépôts</strong>
                    <p>Autorisez seulement les projets à héberger.</p>
                  </div>
                </li>
                <li data-state="waiting">
                  <span>3</span>
                  <div>
                    <strong>Publier une application</strong>
                    <p>La connexion est vérifiée avant son activation.</p>
                  </div>
                </li>
              </ol>{/if}
            <fieldset class="web-fieldset">
              <legend>Propriétaire de la GitHub App</legend>
              <label class="web-check"
                ><input type="radio" bind:group={addOwner} value="personal" disabled={busyWizard} /> Mon compte personnel</label
              >
              <label class="web-check"
                ><input type="radio" bind:group={addOwner} value="organization" disabled={busyWizard} /> Une organisation</label
              >
            </fieldset>
            <div class="web-form-grid">
              {#if addOwner === 'organization'}<label class="web-field"
                  ><span>Nom de l’organisation</span><input
                    bind:value={organization}
                    required
                    pattern="[A-Za-z0-9\-]*"
                    maxlength="39"
                    autocomplete="off"
                    placeholder="mon-organisation"
                    disabled={busyWizard}
                  /><span class="web-help">Le nom court affiché dans <code>github.com/…</code>.</span></label
                >{/if}
              <label class="web-field"
                ><span>Libellé <span class="web-muted">· facultatif</span></span><input
                  bind:value={addLabel}
                  maxlength={WEB_GITHUB_LABEL_MAX}
                  autocomplete="off"
                  placeholder="Organisation Acme"
                  disabled={busyWizard}
                /></label
              >
            </div>
            <p class="web-help">
              {connections.length
                ? 'Vos connexions existantes ne sont pas modifiées.'
                : 'L’assistant prépare les permissions et le webhook. GitHub crée les clés ; elles sont enregistrées directement sur ce serveur.'}
            </p>
            <div class="web-actions">
              <button class="web-button web-button--primary" type="submit" disabled={busyWizard}
                >{connecting ? 'Ouverture de GitHub…' : 'Créer la GitHub App sur GitHub ↗'}</button
              ><button class="web-button" type="button" onclick={openManual} disabled={busyWizard}
                >Ajouter manuellement</button
              >
            </div>
            <p class="web-help">
              Permissions : lecture du code et des métadonnées des dépôts sélectionnés, notifications de push. Aucun
              droit d’écriture sur votre code n’est demandé.
            </p>
          </form>
        {:else if !atLimit && !manualOpen}<p class="web-help">
            <button class="github-link" type="button" onclick={openManual}>Ajouter manuellement</button> une GitHub App existante.
          </p>{/if}

        {#if manualOpen && !atLimit}
          <section class="github-manual" bind:this={manualPanel} aria-labelledby="github-manual-title">
            <div class="web-row">
              <h3 id="github-manual-title">Ajouter une GitHub App existante</h3>
              <button class="web-button" type="button" onclick={() => (manualOpen = false)} disabled={manualSaving}
                >Fermer</button
              >
            </div>
            <p class="web-help">
              Renseignez les identifiants de l’App. Les clés et secrets ne sont jamais renvoyés au navigateur après leur
              enregistrement. Vos connexions existantes ne sont pas modifiées.
            </p>
            <form class="web-form" onsubmit={createManual}>
              <div class="web-form-grid">
                <label class="web-field web-field--full"
                  ><span>Libellé <span class="web-muted">· facultatif</span></span><input
                    bind:value={manualLabel}
                    maxlength={WEB_GITHUB_LABEL_MAX}
                    placeholder="Organisation Acme"
                    disabled={manualSaving}
                    autocomplete="off"
                  /></label
                ><label class="web-field"
                  ><span>App ID GitHub</span><input
                    bind:value={appId}
                    required
                    inputmode="numeric"
                    pattern="[0-9]*"
                    placeholder="123456"
                    disabled={manualSaving}
                    autocomplete="off"
                  /><span class="web-help">Identifiant affiché dans les paramètres de votre GitHub App.</span></label
                ><label class="web-field"
                  ><span>Nom de l’application GitHub</span><input
                    bind:value={slug}
                    required
                    placeholder="kolab-monappli"
                    pattern="[A-Za-z0-9\-]*"
                    disabled={manualSaving}
                    autocomplete="off"
                  /><span class="web-help">Le nom présent dans <code>github.com/apps/…</code>.</span></label
                ><label class="web-field web-field--full"
                  ><span>Installation ID GitHub</span><input
                    bind:value={installationId}
                    required
                    inputmode="numeric"
                    pattern="[0-9]*"
                    placeholder="Identifiant de l’installation"
                    disabled={manualSaving}
                    autocomplete="off"
                  /><span class="web-help"
                    >Après installation, l’identifiant figure dans l’adresse <code
                      >github.com/settings/installations/…</code
                    > ou dans les paramètres de l’organisation.</span
                  ></label
                ><label class="web-field web-field--full"
                  ><span>Clé privée de la GitHub App</span><textarea
                    bind:value={privateKey}
                    required
                    placeholder="-----BEGIN RSA PRIVATE KEY-----"
                    disabled={manualSaving}
                    autocomplete="off"
                    spellcheck="false"
                    rows="5"
                  ></textarea><span class="web-help"
                    >Collez le contenu du fichier PEM généré dans les paramètres GitHub.</span
                  ></label
                ><label class="web-field web-field--full"
                  ><span>Secret de signature des webhooks</span><input
                    type="password"
                    bind:value={webhookSecret}
                    required
                    placeholder="Le même secret que dans les paramètres GitHub"
                    disabled={manualSaving}
                    autocomplete="new-password"
                    minlength="32"
                    maxlength="256"
                  /><span class="web-help"
                    >32 à 256 caractères. Il vérifie que les notifications de push proviennent de cette GitHub App.</span
                  ></label
                >
              </div>
              <p class="web-help">
                L’URL du webhook à configurer sur GitHub (événements Push et Pull request) s’affiche une fois la
                connexion créée.
              </p>
              <div class="web-actions">
                <button class="web-button web-button--primary" type="submit" disabled={manualSaving}
                  >{manualSaving ? 'Enregistrement…' : 'Ajouter la connexion'}</button
                >
              </div>
            </form>
          </section>
        {/if}
        {#if created}<div class="web-alert web-alert--success github-created" role="status">
            <strong>Configurez maintenant le webhook de « {webGithubConnectionLabel(created)} » sur GitHub</strong>
            <p class="web-help">
              Dans les paramètres de la GitHub App, renseignez cette URL et le secret saisi, avec les événements Push et
              Pull request.
            </p>
            {#if created.webhookUrl}<div class="web-dns-record"><code>{created.webhookUrl}</code></div>
              <div class="web-actions">
                <button class="web-button" type="button" onclick={copyCreated}
                  >{copiedCreated ? 'URL copiée' : 'Copier l’URL'}</button
                ><button class="web-button" type="button" onclick={() => focusConnection(created?.id)}
                  >Voir la connexion</button
                >
              </div>{/if}
          </div>{/if}
      </section>

      {#each connections as item (item.id)}
        <WebGitHubConnectionCard
          connection={item}
          refreshKey={healthRefresh}
          connecting={busyWizard}
          onchanged={changed}
          ondeleted={deleted}
          onreplace={replaceConnection}
        />
      {/each}
    </div>
    <aside class="web-sidebar">
      <section class="web-panel">
        <h2>Des accès limités à vos projets</h2>
        <p class="web-help">
          Chaque GitHub App vous appartient. Sélectionnez ses dépôts sur GitHub et retirez son accès quand vous le
          souhaitez.
        </p>
        <p class="web-help">
          Les clés et secrets ne sont jamais affichés dans le navigateur après leur enregistrement.
        </p>
      </section>
      <section class="web-panel">
        <h2>Plusieurs comptes GitHub ?</h2>
        <p class="web-help">
          L’assistant crée une GitHub App privée : elle ne s’installe que sur son compte propriétaire. Ajoutez une
          connexion par compte personnel ou par organisation, puis choisissez-la à la création de chaque application.
        </p>
        <p class="web-help">
          « Recréer la GitHub App » remplace une connexion existante ; elle continue de servir les déploiements jusqu’à
          la validation complète de la nouvelle. Une tentative abandonnée ou expirée ne la remplace pas.
        </p>
      </section>
    </aside>
  </div>
{/if}

<style>
  .github-list {
    gap: 1rem;
  }
  .github-add,
  .github-manual {
    display: grid;
    gap: 1rem;
    padding: 1rem 1.2rem;
    border: 1px solid var(--web-border);
    border-radius: 0.8rem;
  }
  .github-manual h3 {
    margin: 0;
    font-size: 0.95rem;
  }
  .github-created {
    display: grid;
    gap: 0.6rem;
  }
  .github-created p {
    margin: 0;
  }
  .github-link {
    padding: 0;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
  }
  .github-steps {
    list-style: none;
    padding: 0;
    margin: 0;
    display: grid;
    gap: 1.25rem;
  }
  .github-steps li {
    display: flex;
    gap: 0.85rem;
    align-items: start;
  }
  .github-steps li > span {
    display: grid;
    place-items: center;
    flex-shrink: 0;
    width: 1.8rem;
    height: 1.8rem;
    border: 1px solid #75a26370;
    border-radius: 50%;
    font-size: 0.8rem;
    font-weight: 650;
    color: #619950;
  }
  .github-steps li[data-state='waiting'] {
    opacity: 0.55;
  }
  .github-steps li[data-state='current'] > span {
    background: #75a2631a;
    box-shadow: 0 0 0 4px #75a2630b;
  }
  .github-steps strong {
    font-size: 0.9rem;
  }
  .github-steps p {
    font-size: 0.8rem;
    opacity: 0.7;
    margin-top: 0.25rem;
  }
</style>
