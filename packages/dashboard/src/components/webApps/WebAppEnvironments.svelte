<script lang="ts">
  import { onMount } from 'svelte'
  import { webRequest, webError, webDate, webCommit, webLabels, type WebApp } from '$lib/webApps'
  import { WEB_NO_CAPABILITIES, type WebCapabilityMap } from '$lib/webAccessView'
  import { webGitRepositoryLabel } from '$lib/webGit'
  import { webAppMove } from '$lib/webProjectGroupsApi'
  import WebProjectLimits from './WebProjectLimits.svelte'
  import {
    defaultWebProjectLimits,
    type WebProjectLimits as ProjectLimits,
  } from '../../../../pockethost/src/common/webProjectLimits'
  import type { WebPermanentEnvironment } from '../../../../pockethost/src/common/webProjectGroups'
  export let appId: string
  export let capabilities: WebCapabilityMap = WEB_NO_CAPABILITIES
  export let onchanged: () => void | Promise<void> = () => {}
  type Project = {
    id: string
    name: string
    repository: string
    /** Groupe de sites : dépôts différents permis, `repository` ne sert qu'aux previews. */
    multiRepository: boolean
    allowedRepositories?: string[]
    previewEnabled: boolean
    previewSourceAppId: string
    previewMax: number
    previewTtlHours: number
    limits: ProjectLimits
  }
  type Environment = {
    id: string
    appId: string
    projectId: string
    environment: string
    slug: string
    state: string
    sourceAppId: string
    previewNumber: number
    expiresAt: string
    app: WebApp | null
  }
  type Context = {
    legacy: boolean
    project: Project | null
    membership: Environment | null
    items: Environment[]
    effectiveVariables?: { name: string; build: boolean; runtime: boolean; overriddenLocally: boolean }[]
  }
  type Shared = {
    revision: string
    revealed: boolean
    dotenv: string
    variables: { name: string; build: boolean; runtime: boolean; value?: string }[]
  }
  type Changes = { added: string[]; updated: string[]; removed: string[]; preserved: string[] }
  type Promotion = {
    targetAppId: string
    sourceDeploymentId: string
    commit: string
    mode: string
    revision: string
    expiresAt: string
    previewToken: string
    variableNames: string[]
    warning: string
  }
  let context: Context | null = null,
    projects: Project[] = [],
    projectPage = 1,
    projectMore = false
  let busy = '',
    error = '',
    message = '',
    loadedFor = '',
    mounted = false,
    disposed = false,
    generation = 0
  let projectName = '',
    selectedProject = '',
    attachEnvironment: WebPermanentEnvironment = 'prod'
  let cloneEnvironment = 'dev',
    cloneSlug = 'dev',
    cloneName = '',
    cloneBranch = '',
    copyVariables = false
  let targetAppId = '',
    promotion: Promotion | null = null
  let scope = 'dev',
    shared: Shared | null = null,
    sharedText = '',
    sharedMode = 'merge',
    sharedDirty = false,
    sharedGeneration = 0
  let sharedPreview: { changes: Changes; previewToken: string; text: string; mode: string } | null = null
  let detaching = false
  let limits = defaultWebProjectLimits()
  let previewEnabled = false,
    allowedRepositories = '',
    previewSourceAppId = '',
    previewMax = 3,
    previewTtlHours = 24,
    settingsDirty = false,
    pullRequest = '',
    closing = ''
  const labels: Record<string, string> = {
    dev: 'Développement',
    staging: 'Préproduction',
    prod: 'Production',
    preview: 'Preview de PR',
  }
  $: permanent =
    context?.items.filter((item) => item.environment !== 'preview' && item.state === 'active' && item.appId) || []
  $: targets = permanent.filter((item) => item.appId !== appId)
  $: can = capabilities
  const loadProjects = async (next = 1) => {
    const value = await webRequest<{ items: Project[]; hasMore: boolean }>('/projects', { query: { page: next } })
    projects = next === 1 ? value.items : [...projects, ...value.items]
    projectPage = next
    projectMore = value.hasMore
  }
  const loadShared = async (reveal = false) => {
    if (!context?.project) return
    const request = ++sharedGeneration,
      projectId = context.project.id,
      environment = scope
    shared = null
    sharedText = ''
    sharedPreview = null
    sharedDirty = false
    error = ''
    busy = 'shared-load'
    try {
      const value = await webRequest<Shared>(
        `/projects/${projectId}/variables/${environment}${reveal ? '/reveal' : ''}`,
        reveal ? { method: 'POST' } : {}
      )
      if (!disposed && request === sharedGeneration && context?.project?.id === projectId && scope === environment) {
        shared = value
        sharedText = value.dotenv
      }
    } catch (e) {
      if (request === sharedGeneration) error = webError(e)
    } finally {
      if (request === sharedGeneration) busy = ''
    }
  }
  const load = async () => {
    const request = ++generation
    busy = 'load'
    error = ''
    try {
      const value = await webRequest<Context>(`/${appId}/environments`)
      if (disposed || request !== generation) return
      const previous = context?.project?.id
      context = value
      if (!settingsDirty && value.project) {
        projectName = value.project.name
        previewEnabled = value.project.previewEnabled
        allowedRepositories = (value.project.allowedRepositories || []).join('\n')
        previewSourceAppId = value.project.previewSourceAppId
        previewMax = value.project.previewMax
        previewTtlHours = value.project.previewTtlHours
        limits = value.project.limits || defaultWebProjectLimits()
      }
      if (!cloneBranch) cloneBranch = value.membership?.app?.branch || 'main'
      if (value.project && previous !== value.project.id) {
        scope = value.membership?.environment || 'dev'
        await loadShared()
      }
      if (!value.project) {
        sharedGeneration++
        shared = null
        sharedText = ''
        sharedPreview = null
        if (can['app.attach']) await loadProjects()
      }
    } catch (e) {
      if (request === generation) error = webError(e)
    } finally {
      if (request === generation) busy = ''
    }
  }
  $: if (mounted && appId && loadedFor !== appId) {
    loadedFor = appId
    context = null
    promotion = null
    shared = null
    sharedText = ''
    sharedPreview = null
    copyVariables = false
    cloneBranch = ''
    settingsDirty = false
    void load()
  }
  const mutate = async (path: string, method: string, body?: unknown, text = '') => {
    busy = method
    error = ''
    message = ''
    try {
      await webRequest(path, { method, ...(body === undefined ? {} : { body }) })
      message = text
      await onchanged()
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const createProject = () =>
    mutate(
      `/${appId}/project`,
      'POST',
      { name: projectName, environment: attachEnvironment },
      'Projet créé. Les variables existantes de cette application sont conservées.'
    )
  const attachProject = async () => {
    // Groupe de sites : le nom court est dérivé du nom du site par le serveur (« prod » y est souvent déjà pris).
    if (!projects.find((project) => project.id === selectedProject)?.multiRepository)
      return mutate(
        `/${appId}/project/attach`,
        'POST',
        { projectId: selectedProject, environment: attachEnvironment },
        'Application rattachée au projet.'
      )
    busy = 'POST'
    error = ''
    message = ''
    try {
      const result = await webAppMove(appId, { projectId: selectedProject, environment: attachEnvironment })
      message = `Application rattachée au projet sous le nom court « ${result.app.membership?.slug || ''} ».`
      await onchanged()
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const clone = async () => {
    busy = 'clone'
    error = ''
    message = ''
    try {
      const value = await webRequest<{ app: WebApp }>(`/${appId}/environments/clone`, {
        method: 'POST',
        body: {
          environment: cloneEnvironment,
          slug: cloneSlug,
          name: cloneName || undefined,
          branch: cloneBranch,
          copyVariables,
        },
      })
      copyVariables = false
      cloneName = ''
      message = `Environnement créé : ${value.app.name}. Il reste arrêté jusqu’à son premier déploiement ou sa première promotion.`
      await onchanged()
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const inspectPromotion = async () => {
    busy = 'promotion-preview'
    error = ''
    promotion = null
    try {
      promotion = await webRequest<Promotion>(`/${appId}/environments/promotion/preview`, {
        method: 'POST',
        body: { targetAppId },
      })
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const promote = async () => {
    if (promotion) {
      const value = promotion
      promotion = null
      await mutate(
        `/${appId}/environments/promote`,
        'POST',
        value,
        'Promotion ajoutée à la file avec les variables de la cible. Consultez son application pour suivre le déploiement.'
      )
    }
  }
  const inspectShared = async () => {
    if (!shared || !context?.project) return
    busy = 'shared-preview'
    error = ''
    sharedPreview = null
    try {
      const value = await webRequest<{ changes: Changes; previewToken: string }>(
        `/projects/${context.project.id}/variables/${scope}/preview`,
        { method: 'POST', body: { revision: shared.revision, text: sharedText, mode: sharedMode } }
      )
      sharedPreview = { ...value, text: sharedText, mode: sharedMode }
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const saveShared = async () => {
    if (!shared || !sharedPreview || !context?.project) return
    busy = 'shared-save'
    error = ''
    message = ''
    try {
      const value = await webRequest<Shared>(`/projects/${context.project.id}/variables/${scope}`, {
        method: 'PUT',
        body: {
          revision: shared.revision,
          text: sharedPreview.text,
          mode: sharedPreview.mode,
          previewToken: sharedPreview.previewToken,
        },
      })
      shared = value
      sharedText = value.dotenv
      sharedDirty = false
      sharedPreview = null
      message =
        'Variables partagées enregistrées et remasquées. Les applications les utilisent à leur prochain déploiement ou application des variables.'
      await onchanged()
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const editShared = () => {
    sharedDirty = true
    sharedPreview = null
  }
  const saveSettings = async () => {
    if (!context?.project) return
    busy = 'settings'
    error = ''
    message = ''
    try {
      await webRequest(`/projects/${context.project.id}`, {
        method: 'PATCH',
        // Renommer exige le rôle propriétaire : le nom n'est envoyé que s'il peut changer.
        body: {
          ...(can['project.rename'] ? { name: projectName } : {}),
          // Liste gérée par la plateforme : envoyée seulement par un superAdmin.
          ...(can['app.repository']
            ? {
                allowedRepositories: allowedRepositories
                  .split(/[\s,;]+/)
                  .map((value) => value.trim())
                  .filter(Boolean),
              }
            : {}),
          previewEnabled,
          previewSourceAppId,
          previewMax,
          previewTtlHours,
          limits,
        },
      })
      settingsDirty = false
      message = 'Réglages du projet enregistrés.'
      await onchanged()
      await load()
    } catch (e) {
      error = webError(e)
    } finally {
      busy = ''
    }
  }
  const refreshPullRequest = () =>
    context?.project &&
    mutate(
      `/projects/${context.project.id}/previews/${pullRequest}/refresh`,
      'POST',
      {},
      'État de la pull request vérifié. Les previews autorisées apparaissent dans la liste.'
    )
  const close = (item: Environment) =>
    context?.project &&
    mutate(
      `/projects/${context.project.id}/previews/${item.id}/close`,
      'POST',
      {},
      'Preview en fermeture. Ses ressources seront supprimées après arrêt contrôlé.'
    )
  onMount(() => {
    mounted = true
    disposed = false
    return () => {
      disposed = true
      generation++
      sharedGeneration++
      shared = null
      sharedText = ''
      sharedPreview = null
      promotion = null
    }
  })
</script>

<section class="web-panel environment-panel">
  <div class="web-row">
    <div>
      <span class="web-eyebrow">Projets et environnements</span>
      <h2>{context?.project?.name || 'Isoler vos versions de travail'}</h2>
    </div>
    <button class="web-button" type="button" onclick={load} disabled={!!busy}>Actualiser</button>
  </div>
  <p class="web-help">
    Chaque environnement garde sa branche, son adresse et ses variables. Une promotion reprend un commit publié avec les
    variables de sa destination.
  </p>
  {#if error}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}{#if message}<div
      class="web-alert web-alert--success"
      role="status"
    >
      {message}
    </div>{/if}
  {#if !context}<p class="web-muted">
      Chargement des environnements…
    </p>{:else if !context.project && !can['project.create'] && !can['app.attach']}
    <p>Cette application fonctionne sans projet. Seul un administrateur de la plateforme peut la rattacher.</p>
  {:else if !context.project}
    <p>Cette application fonctionne sans projet. Ses réglages actuels restent disponibles.</p>
    <label class="web-field"
      ><span>Environnement de cette application</span><select bind:value={attachEnvironment} disabled={!!busy}
        ><option value="prod">Production</option><option value="staging">Préproduction</option><option value="dev"
          >Développement</option
        ></select
      ></label
    >
    <div class="environment-columns">
      <div class="environment-block">
        <h3>Créer son projet</h3>
        <label class="web-field"
          ><span>Nom du projet</span><input
            bind:value={projectName}
            maxlength="80"
            placeholder="Mon application"
            disabled={!!busy}
          /></label
        ><button
          class="web-button web-button--primary"
          type="button"
          onclick={createProject}
          disabled={!!busy || !projectName.trim()}>Créer le projet et rattacher cette application</button
        >
      </div>
      <div class="environment-block">
        <h3>Rejoindre un projet existant</h3>
        <label class="web-field"
          ><span>Projet</span><select bind:value={selectedProject} disabled={!!busy}
            ><option value="">Choisir un projet</option>{#each projects as project}<option value={project.id}
                >{project.name} · {project.multiRepository
                  ? 'Groupe de sites'
                  : webGitRepositoryLabel(project.repository) || 'Sans dépôt'}</option
              >{/each}</select
          ></label
        >{#if projectMore}<button
            class="web-button"
            type="button"
            onclick={() => loadProjects(projectPage + 1)}
            disabled={!!busy}>Charger d’autres projets</button
          >{/if}<button class="web-button" type="button" onclick={attachProject} disabled={!!busy || !selectedProject}
          >Rattacher cette application</button
        >
      </div>
    </div>
  {:else}
    <div class="environment-cards">
      {#each context.items as item}<article class="environment-card" data-kind={item.environment}>
          <div class="web-row">
            <strong>{labels[item.environment]} · {item.slug}</strong><span
              class="web-status"
              data-tone={item.environment === 'prod' ? 'success' : 'neutral'}
              >{item.state === 'closed'
                ? 'Fermée'
                : item.state === 'closing'
                  ? 'Nettoyage'
                  : item.appId === appId
                    ? 'Application actuelle'
                    : webLabels[item.app?.status || ''] || item.app?.status || 'Arrêtée'}</span
            >
          </div>
          {#if item.app}<p>{item.app.name}</p>
            <p class="web-help">
              <code>{item.app.branch}</code> ·
              <a href={item.app.url} target="_blank" rel="noopener noreferrer">{item.app.url}</a>
            </p>
            <a class="web-button" href={`/applications/${item.appId}`}>Ouvrir cet environnement</a
            >{/if}{#if item.environment === 'preview'}<p class="web-help">
              PR #{item.previewNumber} · expiration {webDate(item.expiresAt)}
            </p>
            {#if item.state === 'active' && can['previews.manage']}{#if closing === item.id}<div class="web-actions">
                  <button
                    class="web-button"
                    type="button"
                    onclick={() => {
                      closing = ''
                      void close(item)
                    }}
                    disabled={!!busy}>Confirmer la fermeture et le nettoyage</button
                  ><button class="web-button" type="button" onclick={() => (closing = '')}>Conserver</button>
                </div>{:else}<button
                  class="web-button"
                  type="button"
                  onclick={() => (closing = item.id)}
                  disabled={!!busy}>Fermer cette preview</button
                >{/if}{/if}{/if}
        </article>{/each}
    </div>
    {#if context.membership?.environment !== 'preview' && can['environments.manage']}<details class="web-details">
        <summary>Créer un environnement isolé depuis cette application</summary>
        <div class="environment-form">
          <label class="web-field"
            ><span>Type</span><select
              bind:value={cloneEnvironment}
              onchange={() => (cloneSlug = cloneEnvironment)}
              disabled={!!busy}
              ><option value="dev">Développement</option><option value="staging">Préproduction</option><option
                value="prod">Production</option
              ></select
            ></label
          ><label class="web-field"
            ><span>Nom court unique</span><input bind:value={cloneSlug} maxlength="60" disabled={!!busy} /></label
          ><label class="web-field"
            ><span>Nom de l’application (facultatif)</span><input
              bind:value={cloneName}
              maxlength="80"
              disabled={!!busy}
            /></label
          ><label class="web-field"
            ><span>Branche indépendante</span><input bind:value={cloneBranch} disabled={!!busy} /></label
          ><label class="web-check"
            ><input type="checkbox" bind:checked={copyVariables} disabled={!!busy} /> Copier explicitement les variables propres
            à l’application source</label
          >
          <p class="web-help">
            Par défaut, aucune valeur de la source n’est copiée. Les variables partagées du type cible restent propres à
            ce type. Les domaines personnalisés, tâches et historiques ne sont pas copiés.
          </p>
          <button class="web-button web-button--primary" type="button" onclick={clone} disabled={!!busy}
            >Créer l’environnement arrêté</button
          >
        </div>
      </details>{/if}
    {#if context.membership?.environment !== 'preview' && can['app.detach']}<details class="web-details">
        <summary>Retirer cette application du projet</summary>
        <p class="web-help">
          Les variables résolues seront conservées comme variables locales de cette application. Les autres
          environnements ne sont pas modifiés.
        </p>
        {#if detaching}<div class="web-actions">
            <button
              class="web-button"
              type="button"
              onclick={() => {
                detaching = false
                void mutate(
                  `/${appId}/project`,
                  'DELETE',
                  undefined,
                  'Application détachée. Ses variables ont été conservées localement.'
                )
              }}
              disabled={!!busy}>Confirmer le détachement et conserver ses variables</button
            ><button class="web-button" type="button" onclick={() => (detaching = false)}>Annuler</button>
          </div>{:else}<button class="web-button" type="button" onclick={() => (detaching = true)} disabled={!!busy}
            >Préparer le détachement</button
          >{/if}
      </details>{/if}
    {#if can['app.deploy']}<div class="environment-block">
        <h3>Promouvoir la version publiée de cette application</h3>
        <label class="web-field"
          ><span>Environnement de destination</span><select
            bind:value={targetAppId}
            onchange={() => (promotion = null)}
            disabled={!!busy}
            ><option value="">Choisir une destination</option>{#each targets as item}<option value={item.appId}
                >{labels[item.environment]} · {item.slug} · {item.app?.name}</option
              >{/each}</select
          ></label
        ><button class="web-button" type="button" onclick={inspectPromotion} disabled={!!busy || !targetAppId}
          >Préparer la promotion</button
        >{#if promotion}<div class="web-alert">
            <p>
              <strong>Commit {webCommit(promotion.commit)}</strong> · {promotion.mode === 'image'
                ? 'Réutilisation contrôlée de l’image'
                : 'Nouvelle construction pour la cible'}
            </p>
            <p>{promotion.warning}</p>
            <p class="web-help">
              {promotion.variableNames.length} variable(s) de destination. Les valeurs restent masquées ; une modification
              concurrente annule la promotion.
            </p>
            <button class="web-button web-button--primary" type="button" onclick={promote} disabled={!!busy}
              >Confirmer la promotion vers la destination</button
            >
          </div>{/if}
      </div>{/if}
  {/if}
</section>

{#if context?.project}<section class="web-panel environment-panel">
    <div class="web-row">
      <h3>Variables partagées du projet</h3>
      <span class="web-eyebrow">Priorité aux variables locales de chaque application</span>
    </div>
    <p class="web-help">
      Une valeur est partagée uniquement entre les applications du type sélectionné. Les previews n’héritent jamais des
      variables dev, staging ou production. Changer de type ou masquer annule le brouillon courant.
    </p>
    {#if context.project.multiRepository}<p class="web-help">
        Ces variables s’appliquent à tous les sites « {labels[scope]} » du projet.
      </p>{/if}
    <label class="web-field"
      ><span>Type d’environnement</span><select bind:value={scope} onchange={() => loadShared()} disabled={!!busy}
        ><option value="dev">Développement</option><option value="staging">Préproduction</option><option value="prod"
          >Production</option
        ><option value="preview">Previews de PR uniquement</option></select
      ></label
    >
    {#if can['variables.reveal']}<div class="web-actions">
        {#if shared?.revealed}<button class="web-button" type="button" onclick={() => loadShared()} disabled={!!busy}
            >Masquer et recharger</button
          >{:else}<button
            class="web-button"
            type="button"
            onclick={() => loadShared(true)}
            disabled={!!busy || !shared || sharedDirty}>Révéler les variables partagées</button
          >{/if}
      </div>{/if}
    <label class="web-field"
      ><span>Éditeur .env littéral · {labels[scope]}</span><textarea
        class="environment-code"
        bind:value={sharedText}
        rows="10"
        oninput={editShared}
        spellcheck="false"
        autocomplete="off"
        readonly={!can['variables.write']}
        disabled={!!busy || !shared}
      ></textarea></label
    >
    <p class="web-help">
      Les lignes <code>__POCKETHOST_MASKED__</code> conservent les secrets. Les dollars restent littéraux. Utilisez l’export
      .env.local de l’onglet Variables pour votre projet Next.js local.
    </p>
    {#if can['variables.write']}<label class="web-field"
        ><span>Import</span><select bind:value={sharedMode} onchange={editShared} disabled={!!busy}
          ><option value="merge">Fusionner et conserver les autres variables</option><option value="replace"
            >Remplacer et retirer les variables absentes</option
          ></select
        ></label
      ><button class="web-button" type="button" onclick={inspectShared} disabled={!!busy || !shared}
        >Prévisualiser les changements partagés</button
      >{#if sharedPreview}<div class="web-alert">
          <p>
            {sharedPreview.changes.added.length} ajout(s), {sharedPreview.changes.updated.length} modification(s), {sharedPreview
              .changes.removed.length} suppression(s), {sharedPreview.changes.preserved.length} conservée(s).
          </p>
          {#if sharedPreview.changes.removed.length}<p>
              Variables retirées : {sharedPreview.changes.removed.join(', ')}.
            </p>{/if}<button class="web-button web-button--primary" type="button" onclick={saveShared} disabled={!!busy}
            >Confirmer pour toutes les applications de ce type</button
          >
        </div>{/if}{/if}{#if context.effectiveVariables?.length}<details class="web-details">
        <summary>Variables résolues pour cette application (noms uniquement)</summary>
        <div class="environment-names">
          {#each context.effectiveVariables as variable}<span
              ><code>{variable.name}</code> · {variable.overriddenLocally ? 'locale' : 'partagée'}</span
            >{/each}
        </div>
      </details>{/if}
  </section>
  {#if can['project.update'] || can['previews.manage']}<section class="web-panel environment-panel">
      <h3>Configuration du projet</h3>
      {#if can['project.update']}<label class="web-field"
          ><span>Nom du projet</span><input
            bind:value={projectName}
            maxlength="80"
            oninput={() => (settingsDirty = true)}
            disabled={!!busy || !can['project.rename']}
          /></label
        >
        {#if can['app.repository']}<label class="web-field"
            ><span>Dépôts que les administrateurs du projet peuvent attribuer</span><textarea
              bind:value={allowedRepositories}
              rows="2"
              oninput={() => (settingsDirty = true)}
              disabled={!!busy}
              placeholder="organisation/depot"
              autocomplete="off"
            ></textarea><span class="web-help"
              >Un dépôt « propriétaire/dépôt » par ligne, 50 au maximum. La GitHub App est partagée par tout le serveur
              : liste vide = seul un administrateur de la plateforme change le dépôt d’une application. Dans un projet,
              une application garde le dépôt du projet.</span
            ></label
          >{/if}
        <h4>Previews automatiques des pull requests</h4>
        <p class="web-help">
          Les previews acceptent uniquement les auteurs OWNER ou MEMBER du même dépôt, sans fork. GitHub App doit
          autoriser la lecture des pull requests et recevoir leurs événements. Une PR fermée, un brouillon ou une
          expiration déclenche le nettoyage du site temporaire.
        </p>
        <label class="web-check"
          ><input
            type="checkbox"
            bind:checked={previewEnabled}
            onchange={() => (settingsDirty = true)}
            disabled={!!busy}
          /> Activer les previews autorisées</label
        >
        <div class="environment-form">
          <label class="web-field"
            ><span>Modèle permanent (ses variables ne sont jamais copiées)</span><select
              bind:value={previewSourceAppId}
              onchange={() => (settingsDirty = true)}
              disabled={!!busy}
              ><option value="">Choisir un modèle</option>{#each permanent as item}<option value={item.appId}
                  >{labels[item.environment]} · {item.app?.name}</option
                >{/each}</select
            ></label
          ><label class="web-field"
            ><span>Quota simultané du projet (1 à 5 ; 10 au total)</span><input
              type="number"
              min="1"
              max="5"
              bind:value={previewMax}
              oninput={() => (settingsDirty = true)}
              disabled={!!busy}
            /></label
          ><label class="web-field"
            ><span>Durée après la dernière synchronisation (heures)</span><input
              type="number"
              min="1"
              max="72"
              bind:value={previewTtlHours}
              oninput={() => (settingsDirty = true)}
              disabled={!!busy}
            /></label
          >
        </div>
        <WebProjectLimits bind:value={limits} disabled={!!busy} onchange={() => (settingsDirty = true)} />
        <button class="web-button" type="button" onclick={saveSettings} disabled={!!busy || !settingsDirty}
          >Enregistrer les règles du projet</button
        >{/if}
      {#if can['previews.manage']}<div class="web-actions">
          <label class="web-field"
            ><span>Numéro d’une PR à synchroniser</span><input
              type="number"
              min="1"
              bind:value={pullRequest}
              disabled={!!busy}
            /></label
          ><button
            class="web-button"
            type="button"
            onclick={refreshPullRequest}
            disabled={!!busy || !previewEnabled || !pullRequest}>Vérifier et synchroniser cette PR</button
          >
        </div>{/if}
      <p class="web-help">
        Les previews ne sont pas sauvegardées. Promouvez un commit vers un environnement permanent pour le conserver.
      </p>
    </section>{/if}{/if}

<style>
  .environment-panel {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .environment-panel p {
    margin: 0;
  }
  .environment-columns,
  .environment-cards {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
  }
  .environment-block,
  .environment-card {
    display: grid;
    align-content: start;
    gap: 0.9rem;
    min-width: 0;
    border: 1px solid var(--web-border);
    padding: 1rem;
    border-radius: var(--web-radius, 6px);
  }
  .environment-card a {
    overflow-wrap: anywhere;
  }
  .environment-card[data-kind='prod'] {
    border-color: var(--app-accent-border, #3b7cff3b);
  }
  .environment-form {
    display: grid;
    gap: 1rem;
    margin-top: 1rem;
  }
  .environment-code {
    font-family: var(--wa-font-family-code, monospace);
    font-size: 0.8rem;
    white-space: pre;
    overflow: auto;
  }
  .environment-names {
    display: grid;
    gap: 0.4rem;
    font-size: 0.8rem;
    margin-top: 0.8rem;
  }
  .environment-panel .web-alert {
    display: grid;
    gap: 0.8rem;
  }
  @media (max-width: 800px) {
    .environment-columns,
    .environment-cards {
      grid-template-columns: 1fr;
    }
  }
  @media (max-width: 550px) {
    .environment-panel .web-button {
      white-space: normal;
    }
  }
</style>
