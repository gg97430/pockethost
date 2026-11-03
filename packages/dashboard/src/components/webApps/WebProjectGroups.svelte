<script lang="ts">
  import { onMount, tick } from 'svelte'
  import WebDeleteResource from '$components/webApps/WebDeleteResource.svelte'
  import WebAppStatus from '$components/webApps/WebAppStatus.svelte'
  import WebAppMovePanel from '$components/webApps/WebAppMovePanel.svelte'
  import WebProjectMembers from '$components/webApps/WebProjectMembers.svelte'
  import WebProjectLimits from '$components/webApps/WebProjectLimits.svelte'
  import {
    defaultWebProjectLimits,
    normalizeWebProjectLimits,
  } from '../../../../pockethost/src/common/webProjectLimits'
  import { webRequest } from '$lib/webApps'
  import { webError } from '$lib/webApps'
  import { webGitRepositoryLabel } from '$lib/webGit'
  import { refreshWebAccess } from '$lib/webAccessStore'
  import { webRoleLabel, type WebCapabilityMap } from '$lib/webAccessView'
  import {
    webProjectAllowRepositories,
    webProjectRename,
    type WebGroupedApp,
    type WebGroupedApps,
  } from '$lib/webProjectGroupsApi'
  import {
    WEB_GROUP_STATE_KEY,
    WEB_UNASSIGNED_GROUP,
    webGroupOpen,
    webMembershipLabel,
    webMoveTargets,
    webParseGroupState,
    webSiteCountLabel,
    webSortGroups,
  } from '$lib/webProjectGroupsView'
  export let data: WebGroupedApps | null = null
  export let loading = false
  export let busy = false
  export let globalCapabilities: Partial<WebCapabilityMap> = {}
  export let canCreateApp = false
  export let canCreateProject = false
  /** Projet à forcer ouvert (par exemple après sa création). */
  export let forceOpen = ''
  /** Vrai tant qu'une action de groupe est ouverte : la page suspend alors son rafraîchissement. */
  export let active = false
  export let oncreateapp: () => void = () => {}
  export let oncreateproject: () => void = () => {}
  export let onchanged: () => void | Promise<void> = () => {}

  type Action = {
    kind: 'members' | 'rename' | 'settings' | 'delete' | 'convert' | 'move'
    projectId: string
    appId?: string
  }
  type Project = WebGroupedApps['groups'][number]['project']
  // Une seule action ouverte à la fois sur la page.
  let action: Action | null = null,
    working = false,
    error = '',
    message = '',
    renameValue = '',
    groupState: Record<string, boolean> = {},
    forcedFor = '',
    section: HTMLElement | null = null,
    // Bouton qui a ouvert l'action : le focus y revient à la fermeture (la section s'il a disparu).
    trigger: HTMLElement | null = null
  let projectLimits = defaultWebProjectLimits()
  $: active = !!action
  $: groups = data ? webSortGroups(data.groups) : []
  $: unassigned = data?.unassigned?.apps || []
  $: allApps = [...groups.flatMap((group) => group.apps), ...unassigned]
  $: if (forceOpen && forcedFor !== forceOpen) {
    forcedFor = forceOpen
    setOpen(forceOpen, true)
  }

  onMount(() => {
    try {
      groupState = { ...webParseGroupState(localStorage.getItem(WEB_GROUP_STATE_KEY)), ...groupState }
    } catch {
      // Stockage indisponible (navigation privée) : repli par défaut.
    }
  })
  const setOpen = (id: string, open: boolean) => {
    groupState = { ...groupState, [id]: open }
    try {
      localStorage.setItem(WEB_GROUP_STATE_KEY, JSON.stringify(groupState))
    } catch {
      // Préférence non mémorisée : sans conséquence.
    }
  }
  const isOpen = (state: Record<string, boolean>, id: string) => webGroupOpen(state, id, id === WEB_UNASSIGNED_GROUP)
  const isAction = (current: Action | null, kind: Action['kind'], projectId: string, appId = '') =>
    current?.kind === kind && current.projectId === projectId && (current.appId || '') === appId
  const actionId = (value: Action) => `group-action-${value.kind}-${value.projectId || 'none'}-${value.appId || ''}`
  const restoreFocus = async () => {
    await tick()
    ;(trigger?.isConnected ? trigger : section)?.focus()
    trigger = null
  }
  const toggleAction = async (next: Action) => {
    action = isAction(action, next.kind, next.projectId, next.appId) ? null : next
    error = ''
    if (!action) return
    message = ''
    trigger =
      document.activeElement instanceof HTMLElement && document.activeElement !== document.body
        ? document.activeElement
        : null
    if (action.kind === 'rename') renameValue = groups.find((g) => g.project.id === next.projectId)?.project.name || ''
    if (action.kind === 'settings')
      projectLimits = normalizeWebProjectLimits(
        groups.find((g) => g.project.id === next.projectId)?.project.limits || {}
      )
    // Panneau en ligne : le focus va sur son premier champ ou bouton.
    await tick()
    document.getElementById(actionId(action))?.querySelector<HTMLElement>('input, select, button')?.focus()
  }
  const closeAction = () => {
    action = null
    error = ''
    void restoreFocus()
  }
  const done = async (text: string) => {
    action = null
    message = text
    await refreshWebAccess()
    await onchanged()
    await restoreFocus()
  }
  const run = async (task: () => Promise<string>) => {
    if (working) return
    working = true
    error = ''
    try {
      await done(await task())
    } catch (e) {
      error = webError(e)
    } finally {
      working = false
    }
  }
  const rename = (event: SubmitEvent, project: Project) => {
    event.preventDefault()
    const name = renameValue.trim()
    if (!name) return
    void run(async () => {
      await webProjectRename(project.id, name)
      return 'Projet renommé.'
    })
  }
  const saveLimits = (event: SubmitEvent, project: Project) => {
    event.preventDefault()
    void run(async () => {
      await webRequest(`/projects/${project.id}`, { method: 'PATCH', body: { limits: projectLimits } })
      return 'Limites du projet enregistrées.'
    })
  }
  const convert = (project: Project) =>
    run(async () => {
      await webProjectAllowRepositories(project.id)
      return `« ${project.name} » accepte désormais des sites de dépôts différents.`
    })
  /** Un site d'un autre groupe pourrait-il être rangé ici ? (aide affichée dans un projet vide) */
  const canReceive = (projectId: string) =>
    allApps.some(
      (app) =>
        !app.deletion &&
        webMoveTargets(groups, app, globalCapabilities).some((target) => target.projectId === projectId)
    )
  const siteMeta = (app: WebGroupedApp) => [
    app.membership
      ? `${app.membership.slug} · ${webMembershipLabel(app.membership)}`
      : app.repository
        ? `${webGitRepositoryLabel(app.repository)} · ${app.branch}`
        : 'Image immuable',
    app.runtimeKind === 'worker' ? 'Worker · sans route HTTP' : app.generatedDomain,
  ]
</script>

{#snippet site(app: WebGroupedApp, fromName: string, projectId: string)}
  {@const movable =
    !app.deletion &&
    app.membership?.environment !== 'preview' &&
    webMoveTargets(groups, app, globalCapabilities).length}
  {@const moving = isAction(action, 'move', projectId, app.id)}
  <li class="web-app-card">
    <div>
      <div class="web-app-name">
        <a href={`/applications/${app.id}`}>{app.name}</a><WebAppStatus
          status={app.status}
          online={app.status === 'running'}
          label={app.status === 'running' ? 'En ligne' : app.status === 'stopped' ? 'Arrêté' : ''}
        />
        {#if app.deploymentInProgress}<WebAppStatus status="running" label="En cours de déploiement" />{/if}
      </div>
      <div class="web-app-meta">
        {#each siteMeta(app) as item}<span>{item}</span>{/each}
      </div>
      {#if app.deletion}<p class="web-help">
          {app.deletion.error || app.deletion.stage || 'Suppression en cours…'}
        </p>{/if}
      {#if app.lastError}<p class="web-help">{app.lastError}</p>{/if}
    </div>
    <div class="web-actions">
      {#if movable}<button
          class="web-button"
          type="button"
          aria-expanded={moving}
          onclick={() => toggleAction({ kind: 'move', projectId, appId: app.id })}
          disabled={working}>{moving ? 'Fermer' : 'Déplacer vers…'}</button
        >{/if}<a class="web-button" href={`/applications/${app.id}`}>Gérer <span aria-hidden="true">→</span></a>
    </div>
    {#if moving}<div class="web-group-move" id={actionId({ kind: 'move', projectId, appId: app.id })}>
        <WebAppMovePanel
          {app}
          {groups}
          {globalCapabilities}
          {fromName}
          oncancel={closeAction}
          onmoved={(summary) => done(summary)}
        />
      </div>{/if}
  </li>
{/snippet}

<section class="web-groups" aria-label="Applications par projet" aria-busy={busy} tabindex="-1" bind:this={section}>
  {#if message}<div class="web-alert web-alert--success" role="status">{message}</div>{/if}
  {#if error && !action}<div class="web-alert web-alert--error" role="alert">{error}</div>{/if}
  {#if data?.truncated}<div class="web-alert" role="status">
      Affichage limité aux 200 premiers projets et 1 000 premiers sites.
    </div>{/if}
  {#if loading && !data}<div class="web-panel web-empty" role="status"><p>Chargement des applications…</p></div>
  {:else if data && !groups.length && !unassigned.length}<div class="web-panel web-empty">
      <span class="web-empty-icon" aria-hidden="true">↗</span>
      {#if data.isPlatformAdmin}<h2>Votre prochain site commence ici</h2>
        <p class="web-muted">
          Choisissez un dépôt GitHub ou une image Docker immuable pour publier votre première version.
        </p>
        <div class="web-actions">
          {#if canCreateApp}<button class="web-button" type="button" onclick={oncreateapp}>Créer une application</button
            >{/if}{#if canCreateProject}<button class="web-button" type="button" onclick={oncreateproject}
              >+ Nouveau projet</button
            >{/if}
        </div>
      {:else}<h2>Aucun projet ne vous est encore ouvert</h2>
        <p class="web-muted">Demandez à un propriétaire de projet de vous inviter.</p>{/if}
    </div>
  {:else if data}
    {#each groups as group (group.project.id)}
      {@const project = group.project}
      {@const open = isOpen(groupState, project.id)}
      <article class="web-panel web-group">
        <div class="web-row">
          <button
            class="web-group-toggle"
            type="button"
            aria-expanded={open}
            aria-controls={`group-${project.id}`}
            onclick={() => setOpen(project.id, !open)}
            ><span aria-hidden="true">{open ? '▾' : '▸'}</span><strong>{project.name}</strong><span class="web-help"
              >{webSiteCountLabel(project.siteCount, project.previewCount)} · {webRoleLabel(
                project.memberRole || project.role
              )}{project.multiRepository ? '' : ` · ${webGitRepositoryLabel(project.repository) || 'Sans dépôt'}`}</span
            ></button
          >
          <div class="web-actions">
            {#if !project.deletion}{#if project.capabilities['project.update']}<button
                  class="web-button"
                  type="button"
                  aria-expanded={isAction(action, 'settings', project.id)}
                  onclick={() => toggleAction({ kind: 'settings', projectId: project.id })}
                  disabled={working}>Configuration</button
                >{/if}
              {#if project.capabilities['members.read']}<button
                  class="web-button"
                  type="button"
                  aria-expanded={isAction(action, 'members', project.id)}
                  onclick={() => toggleAction({ kind: 'members', projectId: project.id })}
                  >{isAction(action, 'members', project.id) ? 'Fermer' : 'Membres'}</button
                >{/if}{#if project.capabilities['project.rename']}<button
                  class="web-button"
                  type="button"
                  aria-expanded={isAction(action, 'rename', project.id)}
                  onclick={() => toggleAction({ kind: 'rename', projectId: project.id })}
                  disabled={working}>Renommer</button
                >{/if}{#if project.capabilities['app.repository'] && !project.multiRepository}<button
                  class="web-button"
                  type="button"
                  aria-expanded={isAction(action, 'convert', project.id)}
                  onclick={() => toggleAction({ kind: 'convert', projectId: project.id })}
                  disabled={working}>Autoriser plusieurs dépôts</button
                >{/if}{/if}{#if project.capabilities['project.delete']}<button
                class="web-button web-button--danger"
                type="button"
                aria-expanded={isAction(action, 'delete', project.id)}
                onclick={() => toggleAction({ kind: 'delete', projectId: project.id })}
                disabled={working || (!!project.deletion && project.deletion.status !== 'failed')}
                >{!project.deletion
                  ? 'Supprimer le projet'
                  : project.deletion.status === 'failed'
                    ? 'Réessayer la suppression'
                    : 'Suppression en cours…'}</button
              >{/if}
          </div>
        </div>
        {#if project.deletion}<p class="web-alert" role="status">
            {project.deletion.error || project.deletion.stage || 'Suppression en cours…'}
          </p>{/if}
        {#if error && action?.projectId === project.id && action.kind !== 'move'}<div
            class="web-alert web-alert--error"
            role="alert"
          >
            {error}
          </div>{/if}
        {#if isAction(action, 'members', project.id)}<WebProjectMembers
            projectId={project.id}
            projectName={project.name}
            onleft={async () => {
              action = null
              await refreshWebAccess()
              await onchanged()
              await restoreFocus()
            }}
          />
        {:else if isAction(action, 'settings', project.id)}<form
            class="grid gap-4"
            id={actionId({ kind: 'settings', projectId: project.id })}
            onsubmit={(event) => saveLimits(event, project)}
          >
            <h3>Configuration de {project.name}</h3>
            <WebProjectLimits bind:value={projectLimits} disabled={working} />
            <div class="web-actions">
              <button class="web-button web-button--primary" type="submit" disabled={working}
                >{working ? 'Enregistrement…' : 'Enregistrer les limites'}</button
              ><button class="web-button" type="button" onclick={closeAction} disabled={working}>Annuler</button>
            </div>
          </form>
        {:else if isAction(action, 'rename', project.id)}<form
            class="web-row"
            id={actionId({ kind: 'rename', projectId: project.id })}
            onsubmit={(event) => rename(event, project)}
          >
            <input
              bind:value={renameValue}
              maxlength="80"
              required
              aria-label={`Nouveau nom du projet ${project.name}`}
              disabled={working}
              autocomplete="off"
            />
            <div class="web-actions">
              <button class="web-button web-button--primary" type="submit" disabled={working || !renameValue.trim()}
                >{working ? 'Enregistrement…' : 'Enregistrer'}</button
              ><button class="web-button" type="button" onclick={closeAction} disabled={working}>Annuler</button>
            </div>
          </form>
        {:else if isAction(action, 'convert', project.id)}<div
            class="web-confirm"
            id={actionId({ kind: 'convert', projectId: project.id })}
            role="region"
            aria-label={`Autoriser plusieurs dépôts dans ${project.name}`}
          >
            <h3>Autoriser des sites de dépôts différents ?</h3>
            <p>Ce choix est définitif. Le dépôt du projet ne servira plus qu’aux previews.</p>
            <div class="web-actions">
              <button
                class="web-button web-button--primary"
                type="button"
                onclick={() => convert(project)}
                disabled={working}>{working ? 'Enregistrement…' : 'Confirmer'}</button
              ><button class="web-button" type="button" onclick={closeAction} disabled={working}>Annuler</button>
            </div>
          </div>
        {:else if isAction(action, 'delete', project.id)}
          <div id={actionId({ kind: 'delete', projectId: project.id })}>
            <WebDeleteResource
              kind="project"
              resourceId={project.id}
              name={project.name}
              siteCount={group.apps.length}
              oncancel={closeAction}
              onaccepted={() => done('Suppression du projet demandée. Le nettoyage est en cours.')}
            />
          </div>
        {/if}
        <div id={`group-${project.id}`} class="web-group-body" hidden={!open}>
          {#if group.apps.length}<ul class="web-group-sites">
              {#each group.apps as app (app.id)}{@render site(app, project.name, project.id)}{/each}
            </ul>
          {:else}<p class="web-muted">Aucun site dans ce projet.</p>
            {#if canReceive(project.id)}<p class="web-help">
                Utilisez « Déplacer vers… » sur un site pour le ranger ici.
              </p>{/if}{/if}
        </div>
      </article>
    {/each}
    {#if unassigned.length}
      {@const open = isOpen(groupState, WEB_UNASSIGNED_GROUP)}
      <article class="web-panel web-group">
        <div class="web-row">
          <button
            class="web-group-toggle"
            type="button"
            aria-expanded={open}
            aria-controls="group-unassigned"
            onclick={() => setOpen(WEB_UNASSIGNED_GROUP, !open)}
            ><span aria-hidden="true">{open ? '▾' : '▸'}</span><strong>Sans projet</strong><span class="web-help"
              >{webSiteCountLabel(unassigned.length)}</span
            ></button
          >
        </div>
        <div id="group-unassigned" class="web-group-body" hidden={!open}>
          <ul class="web-group-sites">
            {#each unassigned as app (app.id)}{@render site(app, '', '')}{/each}
          </ul>
        </div>
      </article>
    {/if}
  {/if}
</section>

<style>
  .web-groups {
    display: grid;
    gap: 0.75rem;
  }
  /* Cible de repli du focus (tabindex=-1) : jamais atteinte au clavier, aucun contour. */
  .web-groups:focus {
    outline: none;
  }
  .web-group {
    display: grid;
    gap: 0.75rem;
  }
  .web-group-toggle {
    display: inline-flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.5rem;
    min-height: 2.25rem;
    padding: 0.25rem 0.4rem;
    margin-left: -0.4rem;
    border: 0;
    border-radius: var(--web-radius);
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .web-group-toggle:hover {
    background: color-mix(in srgb, var(--web-accent) 8%, transparent);
  }
  .web-group-toggle:focus-visible {
    outline: 2px solid var(--web-accent);
    outline-offset: 2px;
  }
  .web-group-toggle > span[aria-hidden='true'] {
    width: 1ch;
    color: var(--web-muted);
  }
  .web-group-body[hidden] {
    display: none;
  }
  .web-group-sites {
    display: grid;
    gap: 0.625rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .web-group-move {
    grid-column: 1 / -1;
  }
  .web-group form.web-row input {
    flex: 1 1 16rem;
    min-width: 0;
    padding: 0.5rem 0.65rem;
    border: 1px solid var(--web-border);
    border-radius: var(--web-radius);
    background: var(--web-surface-soft);
    color: inherit;
    font: inherit;
  }
</style>
