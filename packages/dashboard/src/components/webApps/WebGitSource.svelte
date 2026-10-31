<script lang="ts">
  import { onDestroy } from 'svelte'
  import { client } from '$src/pocketbase-client'
  import { webDate, webError } from '$lib/webApps'
  import {
    isWebGitPresetHost,
    parseGitRemote,
    webGitApi,
    webGitDeployKeysUrl,
    webGitKeyType,
    webGitRepositoryUrl,
    webGitSameFingerprints,
    webGitSourceRemote,
    webGitWebhookGuide,
    webGitWebhookSettingsUrl,
    WEB_GIT_DEPLOY_KEY_STEPS,
    WEB_GIT_PROVIDER_LABELS,
    type WebGitBranch,
    type WebGitProvider,
    type WebGitScan,
    type WebGitSource,
    type WebGitTest,
  } from '$lib/webGit'

  /** Source attachée (web_apps.config.gitSourceId) ; vide = saisie de l’URL puis création. */
  export let sourceId = ''
  /**
   * Application affichant la source : retirée du décompte serveur (appCount) quand sa configuration enregistrée
   * référence déjà cette source (sourceId = config.gitSourceId enregistré).
   */
  export let appId = ''
  /** Projet de rattachement transmis à la création (cible RBAC). */
  export let projectId = ''
  /** Branche suivie par l’application, pour signaler si elle existe. */
  export let branch = ''
  /** Admin du projet : analyse, test, branches, rotations. */
  export let canManage = false
  /** Plateforme ('app.repository') : enregistrer, rattacher ou supprimer le dépôt. */
  export let canConfigureRepository = false
  export let initialUrl = ''
  /** Après création ou mise à jour : le parent enregistre gitSourceId et repository = 'git:'+canonical. */
  export let onsource: ((source: WebGitSource) => void) | undefined = undefined
  export let onbranch: ((name: string) => void) | undefined = undefined
  export let ondelete: ((sourceId: string) => void) | undefined = undefined

  const api = webGitApi((path, options) => client().client.send(path, { ...options, requestKey: null }))
  type Busy = '' | 'load' | 'create' | 'scan' | 'confirm' | 'test' | 'branches' | 'rotate' | 'webhook' | 'delete'
  let source: WebGitSource | null = null
  let url = initialUrl
  let busy: Busy = ''
  let error = ''
  let message = ''
  let scan: WebGitScan | null = null
  let scanChecked = false
  let test: WebGitTest | null = null
  let branches: WebGitBranch[] = []
  let branchesTruncated = false
  let branchSearch = ''
  let choice = ''
  let copied = ''
  let reveal: { url: string; secret: string } | null = null
  let confirming: '' | 'rotate' | 'delete' = ''
  let instructions: WebGitProvider = 'other'
  let observed: string | null = null
  let request = 0
  let disposed = false
  const autoScanned = new Set<string>()

  $: parsed = (() => {
    if (!url.trim()) return { remote: null, error: '' }
    try {
      return { remote: parseGitRemote(url), error: '' }
    } catch (e) {
      return { remote: null, error: webError(e) }
    }
  })()
  $: remote = source ? webGitSourceRemote(source) : null
  $: provider = remote?.provider ?? 'other'
  $: preset = !!source && isWebGitPresetHost(source.host, source.port)
  $: shared = source ? source.appCount : 0
  $: others = source ? Math.max(0, source.appCount - (appId && sourceId && source.id === sourceId ? 1 : 0)) : 0
  $: deployKeysUrl = remote ? webGitDeployKeysUrl(remote, instructions) : null
  $: webhookSettingsUrl = remote ? webGitWebhookSettingsUrl(remote, instructions) : null
  $: guide = webGitWebhookGuide(instructions)
  $: accessOk = test ? test.ok : source?.lastCheckStatus === 'ok'
  $: ready = !!source?.hostKeysConfirmed && accessOk
  $: choices = branches.filter((item) => item.name.toLocaleLowerCase().includes(branchSearch.toLocaleLowerCase()))
  $: branchFound = branches.some((item) => item.name === branch)
  $: sync(sourceId)

  /** Ne dépend que de la prop : une source créée ici puis renvoyée par le parent n’est pas rechargée. */
  function sync(id: string) {
    if (id === observed) return
    observed = id
    reset()
    if (id) void load(id)
  }
  function reset() {
    ++request
    source = null
    scan = null
    scanChecked = false
    test = null
    branches = []
    branchesTruncated = false
    choice = ''
    reveal = null
    confirming = ''
    error = ''
    message = ''
    busy = ''
  }
  const apply = (next: WebGitSource | null, notify = true) => {
    if (!next) return
    const changed = source?.id !== next.id
    source = next
    if (changed) instructions = webGitSourceRemote(next).provider
    if (notify) onsource?.(next)
  }
  /** Exécute une action en ignorant les réponses arrivées après un changement de source ou un démontage. */
  const run = async (kind: Busy, action: (generation: number) => Promise<void>, keep = false) => {
    if (busy) return
    const generation = request
    busy = kind
    if (!keep) {
      error = ''
      message = ''
    }
    try {
      await action(generation)
    } catch (e) {
      if (!disposed && generation === request) error = webError(e)
    } finally {
      if (generation === request) busy = ''
    }
  }
  const live = (generation: number) => !disposed && generation === request
  const load = (id: string) =>
    run('load', async (generation) => {
      const next = await api.get(id)
      if (!live(generation)) return
      apply(next, false)
      if (canManage && !next.hostKeysConfirmed && isWebGitPresetHost(next.host, next.port) && !autoScanned.has(id)) {
        autoScanned.add(id)
        busy = ''
        await scanHost(true)
      }
    })
  const create = (event: SubmitEvent) => {
    event.preventDefault()
    if (!parsed.remote) return
    return run('create', async (generation) => {
      const next = await api.create(url, projectId)
      if (!live(generation)) return
      observed = next.id
      apply(next)
      if (canManage && !next.hostKeysConfirmed && isWebGitPresetHost(next.host, next.port)) {
        autoScanned.add(next.id)
        busy = ''
        await scanHost(true)
      }
      if (!error) message = 'Clé de déploiement générée. Ajoutez-la au dépôt en lecture seule.'
    })
  }
  const scanHost = (keep = false) =>
    run(
      'scan',
      async (generation) => {
        if (!source) return
        scanChecked = false
        const result = await api.scan(source.id)
        if (!live(generation)) return
        if (result.confirmed) {
          scan = null
          apply(result.source ?? (await api.get(source.id)))
          message = 'Empreinte officielle de l’hébergeur vérifiée et épinglée.'
        } else if (!result.hostKeys.length) throw new Error('Aucune clé d’hôte reçue. Vérifiez l’hôte et le port.')
        else scan = result
      },
      keep
    )
  const confirmHost = () =>
    run('confirm', async (generation) => {
      if (!source || !scan || !scanChecked) return
      const fingerprints = scan.hostKeys.map((key) => key.fingerprint)
      const next = await api.confirmHostKeys(source.id, scan.nonce, fingerprints)
      if (!live(generation)) return
      if (
        !webGitSameFingerprints(
          fingerprints,
          next.hostKeys.map((key) => key.fingerprint)
        )
      )
        throw new Error('Les empreintes épinglées ne correspondent pas à celles confirmées. Relancez l’analyse.')
      scan = null
      scanChecked = false
      apply(next)
      message = 'Empreintes épinglées. Seules ces clés d’hôte seront acceptées.'
    })
  const testAccess = () =>
    run('test', async (generation) => {
      if (!source) return
      const result = await api.test(source.id)
      if (!live(generation)) return
      test = result
      if (result.source) apply(result.source)
      if (result.ok) {
        message = 'Accès en lecture vérifié.'
        busy = ''
        await loadBranches(true)
      } else error = result.error || 'Accès refusé. Vérifiez que la clé est ajoutée au dépôt.'
    })
  const loadBranches = (keep = false) =>
    run(
      'branches',
      async (generation) => {
        if (!source) return
        const result = await api.branches(source.id)
        if (!live(generation)) return
        branches = result.items
        branchesTruncated = result.truncated
        if (!choice && result.defaultBranch && result.defaultBranch !== branch) choice = result.defaultBranch
      },
      keep
    )
  const rotateKey = () =>
    run('rotate', async (generation) => {
      if (!source) return
      const result = await api.rotateKey(source.id)
      if (!live(generation)) return
      confirming = ''
      test = null
      branches = []
      apply(result.source ?? { ...source, publicKey: result.publicKey, fingerprint: result.fingerprint })
      message = 'Nouvelle clé générée. Remplacez l’ancienne chez l’hébergeur avant le prochain déploiement.'
    })
  const rotateWebhook = () =>
    run('webhook', async (generation) => {
      if (!source) return
      const result = await api.rotateWebhookSecret(source.id)
      if (!live(generation)) return
      // Le serveur a déjà remplacé le secret : il est affiché dès qu’il est présent, même sans adresse.
      if (!result.secret) throw new Error('Réponse inattendue du serveur pour le webhook.')
      reveal = { url: result.webhookUrl || source.webhookUrl, secret: result.secret }
      apply(result.source ?? { ...source, webhookConfigured: true, webhookUrl: reveal.url })
    })
  const remove = () =>
    run('delete', async (generation) => {
      if (!source) return
      const id = source.id
      await api.remove(id)
      if (!live(generation)) return
      reset()
      observed = ''
      url = ''
      ondelete?.(id)
    })
  const copy = async (key: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value)
      copied = key
      setTimeout(() => {
        if (copied === key) copied = ''
      }, 2000)
    } catch {
      error = 'Sélectionnez le texte pour le copier manuellement.'
    }
  }
  onDestroy(() => {
    disposed = true
    ++request
    reveal = null
  })
</script>

<section class="web-panel git-source">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Source</span>
      <h2>Dépôt Git (clé de déploiement)</h2>
    </div>
    {#if source}<span class="web-status" data-tone={ready ? 'success' : 'working'}
        >{ready ? 'Prêt à déployer' : 'À terminer'}</span
      >{/if}
  </div>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}

  {#if !source}
    {#if busy === 'load'}<p class="web-help" role="status">Chargement de la source…</p>
    {:else if canConfigureRepository}<form class="web-form" onsubmit={create}>
        <label class="web-field"
          ><span>Adresse SSH du dépôt</span><input
            bind:value={url}
            required
            maxlength="500"
            placeholder="git@gitlab.com:organisation/site.git"
            spellcheck="false"
            autocomplete="off"
            disabled={!!busy}
          />{#if parsed.error}<span class="web-help git-source-invalid">{parsed.error}</span
            >{:else if parsed.remote}<span class="web-help"
              >{WEB_GIT_PROVIDER_LABELS[parsed.remote.provider]} · <code>{parsed.remote.host}</code>{parsed.remote
                .port !== 22
                ? ` · port ${parsed.remote.port}`
                : ''} · {isWebGitPresetHost(parsed.remote.host, parsed.remote.port)
                ? 'empreinte officielle connue'
                : 'empreinte à confirmer'}</span
            >{:else}<span class="web-help">GitHub, GitLab, Gitea, Forgejo, Bitbucket ou serveur auto-hébergé.</span
            >{/if}</label
        >
        <div>
          <button class="web-button web-button--primary" type="submit" disabled={!!busy || !parsed.remote}
            >{busy === 'create' ? 'Génération…' : 'Générer la clé de déploiement'}</button
          >
        </div>
      </form>
    {:else}<p class="web-help">Un administrateur de la plateforme doit enregistrer ce dépôt.</p>{/if}
  {:else if remote}
    <div class="git-source-identity">
      <div class="git-source-icon" aria-hidden="true"><wa-icon name="code-branch"></wa-icon></div>
      <div>
        {#if webGitRepositoryUrl(remote)}<a href={webGitRepositoryUrl(remote)} target="_blank" rel="noopener noreferrer"
            >{source.canonical}</a
          >{:else}<strong>{source.canonical}</strong>{/if}
        <p class="web-help">
          <code>{remote.sshUrl}</code>{#if branch}
            · <code>{branch}</code>{/if}{#if others > 0}
            · clé partagée avec {others} autre(s) application(s){/if}
        </p>
      </div>
    </div>

    <ol class="git-steps">
      <li>
        <div class="web-row">
          <strong>Empreinte du serveur</strong>
          {#if source.hostKeysConfirmed}<span class="web-status" data-tone="success"
              >{source.hostKeys.some((key) => key.preset)
                ? 'Empreinte officielle épinglée'
                : 'Empreinte épinglée'}</span
            >{:else}<span class="web-status" data-tone="working">À vérifier</span>{/if}
        </div>
        {#if scan}
          <p class="web-help">
            Comparez avec les empreintes publiées par votre hébergeur (ou <code>ssh-keygen -lf</code> sur le serveur).
            Valable jusqu’à {webDate(scan.expiresAt)}.
          </p>
          <ul class="git-keys">
            {#each scan.hostKeys as key}<li>
                <span>{webGitKeyType(key.type)}</span><code>{key.fingerprint}</code>
              </li>{/each}
          </ul>
          <label class="web-check"
            ><input type="checkbox" bind:checked={scanChecked} disabled={!!busy} />J’ai vérifié que ces empreintes sont
            exactement celles de {source.host}</label
          >
          <div class="web-actions">
            <button
              class="web-button web-button--primary"
              type="button"
              onclick={confirmHost}
              disabled={!!busy || !scanChecked}>{busy === 'confirm' ? 'Épinglage…' : 'Épingler ces empreintes'}</button
            ><button class="web-button" type="button" onclick={() => (scan = null)} disabled={!!busy}>Annuler</button>
          </div>
        {:else if source.hostKeysConfirmed}
          <details class="web-details">
            <summary
              >{source.hostKeys.length} clé(s) d’hôte · épinglée(s) le {webDate(source.hostKeysConfirmedAt)}</summary
            >
            <ul class="git-keys">
              {#each source.hostKeys as key}<li>
                  <span>{webGitKeyType(key.type)}</span><code>{key.fingerprint}</code>
                </li>{/each}
            </ul>
            {#if canManage}<button class="web-button" type="button" onclick={() => scanHost()} disabled={!!busy}
                >{busy === 'scan' ? 'Analyse…' : 'Analyser à nouveau (clé d’hôte changée)'}</button
              >{/if}
          </details>
        {:else if canManage}
          <div class="web-actions">
            <button class="web-button web-button--primary" type="button" onclick={() => scanHost()} disabled={!!busy}
              >{busy === 'scan' ? 'Analyse…' : preset ? 'Vérifier l’empreinte officielle' : 'Analyser l’hôte'}</button
            >
          </div>
        {:else}<p class="web-help">Un administrateur du projet doit vérifier l’empreinte de {source.host}.</p>{/if}
      </li>

      <li>
        <div class="web-row">
          <strong>Clé de déploiement</strong>
          <span class="web-status">Lecture seule</span>
        </div>
        <textarea
          class="git-key"
          readonly
          rows="2"
          value={source.publicKey}
          spellcheck="false"
          aria-label="Clé publique à ajouter au dépôt"
        ></textarea>
        <div class="web-actions">
          <button
            class="web-button web-button--primary"
            type="button"
            onclick={() => copy('key', source?.publicKey ?? '')}
            disabled={!source.publicKey}
            ><wa-icon name={copied === 'key' ? 'check' : 'copy'}></wa-icon>{copied === 'key'
              ? 'Clé copiée'
              : 'Copier la clé publique'}</button
          >{#if deployKeysUrl}<a class="web-button" href={deployKeysUrl} target="_blank" rel="noopener noreferrer"
              >Ouvrir les clés de déploiement ↗</a
            >{/if}
          {#if source.fingerprint}<span class="web-help"><code>{source.fingerprint}</code></span>{/if}
        </div>
        <div class="git-instructions">
          <p class="web-help">{WEB_GIT_DEPLOY_KEY_STEPS[instructions]}</p>
          {#if provider === 'other'}<label class="web-field git-provider"
              ><span>Interface de l’hébergeur</span><select bind:value={instructions}
                >{#each Object.entries(WEB_GIT_PROVIDER_LABELS) as [value, label]}<option {value}>{label}</option
                  >{/each}</select
              ></label
            >{/if}
        </div>
      </li>

      <li>
        <div class="web-row">
          <strong>Accès et branche</strong>
          {#if test || source.lastCheckAt}<span class="web-status" data-tone={accessOk ? 'success' : 'danger'}
              >{accessOk ? 'Accès vérifié' : 'Accès refusé'}</span
            >{/if}
        </div>
        {#if !test && source.lastCheckAt}<p class="web-help">
            Dernier contrôle le {webDate(source.lastCheckAt)}.{#if !accessOk && source.lastError}
              {source.lastError}{/if}
          </p>{/if}
        {#if canManage}<div class="web-actions">
            <button
              class="web-button"
              class:web-button--primary={!accessOk}
              type="button"
              onclick={testAccess}
              disabled={!!busy || !source.hostKeysConfirmed}>{busy === 'test' ? 'Test…' : 'Tester l’accès'}</button
            >{#if accessOk && !branches.length}<button
                class="web-button"
                type="button"
                onclick={() => loadBranches()}
                disabled={!!busy}>{busy === 'branches' ? 'Chargement…' : 'Lister les branches'}</button
              >{/if}
            {#if !source.hostKeysConfirmed}<span class="web-help">Épinglez d’abord l’empreinte du serveur.</span>{/if}
          </div>{/if}
        {#if branches.length}
          <p class="web-help" role="status">
            {branch
              ? branchFound
                ? `La branche suivie « ${branch} » existe.`
                : `La branche suivie « ${branch} » est absente du dépôt.`
              : `${branches.length} branche(s) trouvée(s).`}{branchesTruncated ? ' Liste tronquée.' : ''}
          </p>
          {#if onbranch}<div class="git-branches">
              <label class="web-field"
                ><span>Filtrer</span><input bind:value={branchSearch} placeholder="Rechercher une branche…" /></label
              ><label class="web-field"
                ><span>Branche à déployer</span><select bind:value={choice}
                  ><option value="">{choices.length} branche(s)</option>{#each choices as item}<option value={item.name}
                      >{item.name}</option
                    >{/each}</select
                ></label
              ><button
                class="web-button"
                type="button"
                onclick={() => {
                  if (choice) onbranch?.(choice)
                }}
                disabled={!choice || choice === branch}>Utiliser cette branche</button
              >
            </div>{/if}
        {/if}
      </li>
    </ol>

    <details class="web-details">
      <summary>Déployer à chaque push (webhook){source.webhookConfigured ? ' · configuré' : ''}</summary>
      <div class="git-webhook">
        {#if reveal}<section class="git-secret" aria-live="polite">
            <div class="web-row">
              <strong>Secret affiché une seule fois</strong>
              <button class="web-button" type="button" onclick={() => (reveal = null)}>Masquer définitivement</button>
            </div>
            {#if reveal.url}<label class="web-field"
                ><span>Adresse du webhook</span><input readonly value={reveal.url} spellcheck="false" /></label
              >{/if}
            <label class="web-field"
              ><span>Secret</span><input readonly value={reveal.secret} spellcheck="false" autocomplete="off" /></label
            >
            <div class="web-actions">
              {#if reveal.url}<button class="web-button" type="button" onclick={() => copy('url', reveal?.url ?? '')}
                  >{copied === 'url' ? 'Adresse copiée' : 'Copier l’adresse'}</button
                >{/if}<button
                class="web-button web-button--primary"
                type="button"
                onclick={() => copy('secret', reveal?.secret ?? '')}
                >{copied === 'secret' ? 'Secret copié' : 'Copier le secret'}</button
              >{#if webhookSettingsUrl}<a
                  class="web-button"
                  href={webhookSettingsUrl}
                  target="_blank"
                  rel="noopener noreferrer">Ouvrir les webhooks ↗</a
                >{/if}
            </div>
            <ol class="git-guide">
              {#each guide.steps as step}<li>{step}</li>{/each}
            </ol>
          </section>
        {:else if !guide.supported}<p class="web-help">{guide.steps[0]}</p>
        {:else}
          {#if source.webhookUrl}<p class="web-help"><code>{source.webhookUrl}</code></p>{/if}
          {#if canManage}<div class="web-actions">
              <button class="web-button" type="button" onclick={rotateWebhook} disabled={!!busy}
                >{busy === 'webhook'
                  ? 'Génération…'
                  : source.webhookConfigured
                    ? 'Renouveler le secret'
                    : 'Générer l’adresse et le secret'}</button
              >{#if source.webhookConfigured}<span class="web-help">L’ancien secret cessera de fonctionner.</span>{/if}
            </div>{:else}<p class="web-help">
              {source.webhookConfigured ? 'Webhook configuré.' : 'Aucun webhook : déploiements manuels.'}
            </p>{/if}
        {/if}
      </div>
    </details>

    {#if canManage || canConfigureRepository}<details class="web-details">
        <summary>Rotation et suppression</summary>
        <div class="git-danger">
          {#if confirming === 'rotate'}<div class="web-confirm">
              <p>
                La clé actuelle sera remplacée{others > 0 ? `, y compris pour ${others} autre(s) application(s)` : ''}.
                Les déploiements échoueront jusqu’à l’ajout de la nouvelle clé chez l’hébergeur.
              </p>
              <div class="web-actions">
                <button class="web-button web-button--danger" type="button" onclick={rotateKey} disabled={!!busy}
                  >{busy === 'rotate' ? 'Rotation…' : 'Remplacer la clé'}</button
                ><button class="web-button" type="button" onclick={() => (confirming = '')} disabled={!!busy}
                  >Conserver</button
                >
              </div>
            </div>
          {:else if confirming === 'delete'}<div class="web-confirm">
              <p>La clé privée sera détruite. Retirez aussi la clé publique chez l’hébergeur.</p>
              <div class="web-actions">
                <button class="web-button web-button--danger" type="button" onclick={remove} disabled={!!busy}
                  >{busy === 'delete' ? 'Suppression…' : 'Supprimer la source'}</button
                ><button class="web-button" type="button" onclick={() => (confirming = '')} disabled={!!busy}
                  >Conserver</button
                >
              </div>
            </div>
          {:else}<div class="web-actions">
              {#if canManage}<button
                  class="web-button"
                  type="button"
                  onclick={() => (confirming = 'rotate')}
                  disabled={!!busy}>Générer une nouvelle clé</button
                >{/if}{#if canConfigureRepository}<button
                  class="web-button web-button--danger"
                  type="button"
                  onclick={() => (confirming = 'delete')}
                  disabled={!!busy || shared > 0}>Supprimer la source</button
                >{#if shared > 0}<span class="web-help">Détachez d’abord les applications qui l’utilisent.</span
                  >{/if}{/if}
            </div>{/if}
        </div>
      </details>{/if}
  {/if}
</section>

<style>
  .git-source p {
    margin: 0;
  }
  .git-source-identity {
    display: flex;
    gap: 0.8rem;
    align-items: center;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    padding: 1rem;
  }
  .git-source-identity > div:nth-child(2) {
    flex: 1;
    min-width: 0;
  }
  .git-source-identity a,
  .git-source-identity strong {
    overflow-wrap: anywhere;
    font-weight: 600;
    font-size: 0.8125rem;
  }
  .git-source-identity p {
    margin-top: 0.4rem;
  }
  .git-source-icon {
    height: 2.3rem;
    width: 2.3rem;
    flex: none;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    display: grid;
    place-items: center;
    color: var(--web-muted);
  }
  .git-source-invalid {
    color: var(--app-danger, #f87171);
  }
  .git-steps {
    display: grid;
    gap: 1.25rem;
    margin: 0;
    padding-left: 1.3rem;
  }
  .git-steps > li {
    display: grid;
    gap: 0.75rem;
    min-width: 0;
  }
  .git-steps strong {
    font-size: 0.8125rem;
  }
  .git-keys {
    display: grid;
    gap: 0.4rem;
    margin: 0 0 0.75rem;
    padding: 0;
    list-style: none;
  }
  .git-keys li {
    display: grid;
    grid-template-columns: 4.5rem minmax(0, 1fr);
    gap: 0.6rem;
    font-size: 0.8rem;
  }
  .git-keys span {
    color: var(--web-muted);
  }
  .git-source code {
    overflow-wrap: anywhere;
  }
  .git-key {
    width: 100%;
    min-height: 0;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius, 6px);
    background: var(--app-input-bg, var(--web-surface-soft));
    color: inherit;
    padding: 0.6rem 0.65rem;
    resize: none;
    font:
      12px/1.6 ui-monospace,
      SFMono-Regular,
      monospace;
    overflow-wrap: anywhere;
  }
  .git-source .web-button wa-icon {
    margin-right: 0.4rem;
  }
  .git-instructions {
    display: grid;
    gap: 0.6rem;
  }
  .git-provider {
    max-width: 18rem;
  }
  .git-branches {
    display: grid;
    grid-template-columns: 1fr 1fr auto;
    gap: 0.75rem;
    align-items: end;
  }
  .git-webhook,
  .git-danger {
    display: grid;
    gap: 0.75rem;
  }
  .git-secret {
    display: grid;
    gap: 0.9rem;
    padding: 1rem;
    border: 1px solid var(--app-accent-border, #3b7cff3b);
    border-radius: var(--web-radius, 6px);
    background: var(--app-accent-soft, #3b7cff1c);
  }
  .git-secret input {
    font-family: ui-monospace, SFMono-Regular, monospace;
  }
  .git-guide {
    display: grid;
    gap: 0.35rem;
    margin: 0;
    padding-left: 1.2rem;
    font-size: 0.8rem;
    color: var(--web-muted);
  }
  @media (max-width: 750px) {
    .git-branches {
      grid-template-columns: 1fr;
    }
    .git-keys li {
      grid-template-columns: 1fr;
      gap: 0.15rem;
    }
  }
</style>
