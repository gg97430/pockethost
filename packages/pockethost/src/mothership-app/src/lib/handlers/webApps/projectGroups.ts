/**
 * Applications web — projets « groupes de sites » : liste groupée, création et suppression d'un projet, rangement d'un
 * site (sans projet -> P, P -> sans projet, A -> B). Le lien application -> projet reste la ligne web_app_environments
 * (appId unique), seule source des droits.
 */
import { isWebRole, webCapabilities, type WebRole } from '../../../../../common/webAccess'
import { planWebEnvironment, type WebEnvMap } from '../../../../../common/webEnvironment'
import {
  WEB_PROJECT_GROUP_LIMITS,
  webProjectNameError,
  webSiteSlug,
  webUniqueSlug,
} from '../../../../../common/webProjectGroups'
import {
  WEB_MEMBERS,
  isWebPlatformAdmin,
  listWebMemberships,
  requirePlatformAdmin,
  requireWebAppPermission,
  requireWebPermission,
  requireWebProjectPermission,
  requireWebUser,
  webAppAccessView,
  webProjectAccessQuery,
} from './access'
import {
  bindWebGitSourceProject,
  decryptWebValue,
  encryptWebValue,
  serializeWebApp,
  webAppsCapability,
  webConfig,
  webInput,
  webJson,
} from './api'
import { HandleWebProjectDeleteContents } from './deletion'
import {
  assertWebEnvironmentSlug,
  assertWebPermanentCapacity,
  attachWebAppToProject,
  detachWebAppFromProject,
  webEnvironmentSlug,
  webPermanentEnvironment,
} from './environments'
import { reconcileWebGitSourceProject } from './gitSource'
import { resetWebNotificationChannels } from './notifications'
import {
  auditWebProject,
  resolveWebEnvironment,
  webDeploymentOrigin,
  webHistoryInScope,
  webMembership,
  webProjectId,
  webProjectLineage,
  webProjectRepositoryMatches,
  webProjectVariable,
  webProjectView,
} from './projects'

type GroupRole = WebRole | 'platform'
const LIMITS = WEB_PROJECT_GROUP_LIMITS
const has = (value: object, name: string) => Object.prototype.hasOwnProperty.call(value, name)
const groupProjectView = (
  row: core.Record,
  role: GroupRole,
  memberRole: WebRole | null,
  counts = { sites: 0, previews: 0 }
) => ({
  ...webProjectView(row),
  role,
  memberRole,
  capabilities: webCapabilities(role),
  siteCount: counts.sites,
  previewCount: counts.previews,
})
const membershipOf = (row: core.Record | null) =>
  row
    ? {
        id: row.id,
        environment: row.getString('environment'),
        slug: row.getString('slug'),
        state: row.getString('state'),
        previewNumber: row.getInt('previewNumber'),
        sourceAppId: row.getString('sourceAppId'),
      }
    : null
const findApps = (ids: string[]) => {
  const out = new Map<string, core.Record>()
  for (let i = 0; i < ids.length; i += 200)
    for (const row of $app.findRecordsByIds('web_apps', ids.slice(i, i + 200))) if (row) out.set(row.id, row)
  return out
}
const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name)

/** Une lecture groupée, limitée aux applications déjà autorisées dans la liste. */
const appsWithPendingDeployments = (ids: string[]) => {
  const pending = new Set<string>()
  for (let offset = 0; offset < ids.length; offset += 200) {
    const params: Record<string, string> = {},
      placeholders = ids.slice(offset, offset + 200).map((id, index) => {
        params[`app${index}`] = id
        return `{:app${index}}`
      }),
      rows = arrayOf(new DynamicModel({ appId: '' }))
    $app
      .db()
      .newQuery(
        `SELECT DISTINCT appId FROM web_deployments WHERE status IN ('queued', 'running') AND appId IN (${placeholders.join(',')})`
      )
      .bind(params)
      .all(rows)
    for (const row of rows) pending.add(`${row.appId}`)
  }
  return pending
}

/** GET /api/web-projects/groups — projets accessibles et leurs sites ; superAdmin : tout, plus « Sans projet ». */
export const HandleWebProjectGroups = (e: core.RequestEvent) => {
  const user = requireWebUser(e),
    platform = isWebPlatformAdmin(user),
    q = webProjectAccessQuery(user, 'id'),
    rows = $app.findRecordsByFilter('web_projects', q.filter, 'name', LIMITS.projects + 1, 0, q.params) as core.Record[]
  let truncated = rows.length > LIMITS.projects
  // Rôles calculés une fois (une requête d'adhésions), jamais par application.
  const memberships = new Map<string, WebRole>()
  for (const m of listWebMemberships(user)) if (isWebRole(m.role)) memberships.set(m.projectId, m.role)
  const projects = new Map<
    string,
    { row: core.Record; role: GroupRole | null; memberRole: WebRole | null; capabilities: any }
  >()
  for (const row of rows.slice(0, LIMITS.projects)) {
    const memberRole = memberships.get(row.id) || null,
      role: GroupRole | null = platform ? 'platform' : memberRole
    projects.set(row.id, { row, role, memberRole, capabilities: webCapabilities(role) })
  }
  const access = platform ? { filter: '', params: {} } : webProjectAccessQuery(user, 'projectId'),
    environments = $app.findRecordsByFilter(
      'web_app_environments',
      "appId != ''" + (access.filter ? ` && ${access.filter}` : ''),
      'slug',
      LIMITS.apps + 1,
      0,
      access.params
    ) as core.Record[]
  if (environments.length > LIMITS.apps) truncated = true
  const kept = environments.slice(0, LIMITS.apps).filter((row) => projects.has(row.getString('projectId'))),
    apps = findApps(kept.map((row) => row.getString('appId')))
  const items = new Map<string, any[]>()
  for (const env of kept) {
    const app = apps.get(env.getString('appId'))
    if (!app) continue
    const project = projects.get(env.getString('projectId'))!,
      list = items.get(project.row.id) || []
    list.push({
      ...serializeWebApp(app),
      access: {
        projectId: project.row.id,
        role: project.role,
        memberRole: project.memberRole,
        capabilities: project.capabilities,
      },
      membership: membershipOf(env),
    })
    items.set(project.row.id, list)
  }
  const groups = [...projects.values()]
    .map(({ row, role, memberRole }) => {
      const list = items.get(row.id) || [],
        sites = list.filter((app) => app.membership.environment !== 'preview').sort(byName),
        previews = list
          .filter((app) => app.membership.environment === 'preview')
          .sort((a, b) => a.membership.previewNumber - b.membership.previewNumber)
      return {
        project: groupProjectView(row, role!, memberRole, { sites: sites.length, previews: previews.length }),
        apps: [...sites, ...previews],
      }
    })
    .sort((a, b) => a.project.name.localeCompare(b.project.name))
  let unassigned: { apps: any[] } | null = null
  if (platform) {
    const ids = arrayOf(new DynamicModel({ id: '' }))
    $app
      .db()
      .newQuery(
        `SELECT id FROM web_apps WHERE id NOT IN (SELECT appId FROM web_app_environments WHERE appId != '') ORDER BY created DESC LIMIT ${LIMITS.apps + 1}`
      )
      .all(ids)
    if (ids.length > LIMITS.apps) truncated = true
    const found = findApps(ids.slice(0, LIMITS.apps).map((row: any) => `${row.id}`)),
      capabilities = webCapabilities('platform')
    unassigned = {
      apps: [...found.values()]
        .map((app) => ({
          ...serializeWebApp(app),
          access: { projectId: '', role: 'platform', memberRole: null, capabilities },
          membership: null,
        }))
        .sort(byName),
    }
  }
  const visibleApps = [...groups.flatMap((group) => group.apps), ...(unassigned?.apps || [])],
    pending = appsWithPendingDeployments(visibleApps.map((app) => app.id))
  for (const app of visibleApps) app.deploymentInProgress = pending.has(app.id)
  return webJson(e, {
    enabled: true,
    isPlatformAdmin: platform,
    capabilities: webAppsCapability(),
    groups,
    unassigned,
    truncated,
  })
}

/** POST /api/web-projects — projet vide (groupe de sites) créé par un superAdmin, qui en devient propriétaire. */
export const HandleWebProjectCreate = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    input = webInput(e),
    invalid = webProjectNameError(input.name)
  if (invalid) throw new BadRequestError(invalid)
  const name = `${input.name}`.trim()
  let project: any
  $app.runInTransaction((tx) => {
    // Unicité insensible à la casse, accents compris (création seulement : un renommage ne révèle pas les autres
    // projets). Comparée en JS sur tous les noms : LIKE et lower() de SQLite ne replient que l'ASCII.
    const key = (value: string) => value.trim().normalize('NFC').toLowerCase(),
      names = arrayOf(new DynamicModel({ name: '' }))
    tx.db().newQuery('SELECT name FROM web_projects').all(names)
    if (names.some((row: any) => key(`${row.name}`) === key(name)))
      throw new BadRequestError('Un projet porte déjà ce nom.')
    const row = new Record(tx.findCollectionByNameOrId('web_projects'))
    row.set('name', name)
    row.set('repository', '')
    row.set('multiRepository', true)
    row.set('actorId', user.id)
    row.set('previewEnabled', false)
    row.set('previewSourceAppId', '')
    row.set('previewMax', 3)
    row.set('previewTtlHours', 24)
    row.set('allowedRepositories', [])
    tx.save(row)
    const member = new Record(tx.findCollectionByNameOrId(WEB_MEMBERS))
    member.set('project', row.id)
    member.set('user', user.id)
    member.set('role', 'owner')
    member.set('invitedBy', user.id)
    tx.save(member)
    auditWebProject(row.id, user.id, 'project-created', { name, standalone: true, ownerId: user.id }, tx)
    project = groupProjectView(row, 'platform', 'owner')
  })
  return webJson(e, { project }, 201)
}

/**
 * DELETE /api/web-projects/{projectId} — refusé tant qu'il reste un site (previews comprises) : un détachement en masse
 * ferait perdre l'accès aux membres sans bruit. L'audit est conservé (webAppDetachment, webHistoryInScope).
 */
export const HandleWebProjectDelete = (e: core.RequestEvent) => {
  const { user, project: found } = requireWebProjectPermission(e, 'project.delete'),
    projectId = found!.id
  if (webInput(e).deleteContents === true || found!.getString('deletionId')) return HandleWebProjectDeleteContents(e)
  $app.runInTransaction((tx) => {
    const project = tx.findRecordById('web_projects', projectId),
      sites = tx.findRecordsByFilter('web_app_environments', "projectId={:id} && appId != ''", '', 1000, 0, {
        id: projectId,
      }).length
    if (sites)
      throw new BadRequestError(
        `Ce projet contient encore ${sites} site(s). Retirez-les ou déplacez-les avant de le supprimer.`
      )
    if (
      tx.findRecordsByFilter('web_app_environments', "projectId={:id} && appId = '' && state != 'closed'", '', 1, 0, {
        id: projectId,
      }).length
    )
      throw new BadRequestError('Le nettoyage de previews est en cours. Réessayez dans quelques minutes.')
    for (const row of tx.findRecordsByFilter('web_app_environments', 'projectId={:id}', '', 0, 0, {
      id: projectId,
    }) as core.Record[])
      tx.delete(row)
    for (const row of tx.findRecordsByFilter('web_project_variables', 'projectId={:id}', '', 0, 0, {
      id: projectId,
    }) as core.Record[])
      tx.delete(row)
    // Source « Dépôt Git » sans projet : gérée par la plateforme.
    for (const row of tx.findRecordsByFilter('web_git_sources', 'projectId={:id}', '', 0, 0, {
      id: projectId,
    }) as core.Record[]) {
      row.set('projectId', '')
      tx.save(row)
    }
    auditWebProject(projectId, user.id, 'project-deleted', { name: project.getString('name'), actorId: user.id }, tx)
    // Membres : suppression en cascade (relation web_project_members.project).
    tx.delete(project)
  })
  return webJson(e, { deleted: true, projectId })
}

const targetProject = (projectId: string, store: any): core.Record => {
  try {
    return store.findRecordById('web_projects', projectId)
  } catch {
    throw new NotFoundError('Projet introuvable.')
  }
}
const busy = (appId: string, store: any) =>
  store.findRecordsByFilter('web_deployments', 'appId={:id} && (status="queued" || status="running")', '', 1, 0, {
    id: appId,
  }).length > 0
/**
 * Previews ouvertes à partir du site : refus. Site modèle des previews de son projet : previews désactivées (le modèle
 * quitte le projet). Renvoie true si elles l'ont été.
 */
const releasePreviewSource = (app: core.Record, projectId: string, user: core.Record, tx: any) => {
  if (
    tx.findRecordsByFilter(
      'web_app_environments',
      "projectId={:project} && environment='preview' && sourceAppId={:app} && appId != ''",
      '',
      1,
      0,
      { project: projectId, app: app.id }
    ).length
  )
    throw new BadRequestError('Fermez d’abord les previews ouvertes à partir de ce site.')
  const project = tx.findRecordById('web_projects', projectId)
  if (project.getString('previewSourceAppId') !== app.id) return false
  project.set('previewEnabled', false)
  project.set('previewSourceAppId', '')
  tx.save(project)
  auditWebProject(projectId, user.id, 'settings-updated', { previewEnabled: false, reason: 'preview-source-moved' }, tx)
  return true
}

/**
 * Variables locales du site + variables partagées de l'environnement cible : même limite qu'au déploiement, vérifiée
 * avant d'enregistrer le rangement (sinon chaque résolution échouerait ensuite).
 */
const assertMergedVariables = (appId: string, project: core.Record, environment: string, tx: any) => {
  const combined: WebEnvMap = {
    ...decryptWebValue(webProjectVariable(project.id, environment, tx)?.getString('valueEncrypted') || ''),
    ...decryptWebValue(tx.findRecordById('web_apps', appId).getString('envEncrypted')),
  }
  try {
    planWebEnvironment(combined, [], 'merge')
  } catch (error) {
    throw new BadRequestError(
      `Variables de ce site et variables partagées de « ${project.getString('name')} » : ${error instanceof Error ? error.message : 'limite dépassée'}`
    )
  }
}

/**
 * PUT /api/web-apps/{id}/project — ranger (sans projet -> P : app.attach), retirer (P -> sans projet : app.detach) ou
 * déplacer (A -> B : app.move sur A ET sur B). Gardes dans cet ordre : application visible, corps valide, droits.
 */
export const HandleWebAppProjectMove = (e: core.RequestEvent) => {
  const access = requireWebAppPermission(e, 'app.read'),
    user = access.user,
    app = access.app!,
    input = webInput(e)
  if (input.projectId !== null && typeof input.projectId !== 'string') throw new BadRequestError('Projet invalide.')
  const targetId: string | null = input.projectId === null ? null : webProjectId(input.projectId),
    environment: string = input.environment === undefined ? '' : webPermanentEnvironment(input.environment),
    slug: string = input.slug === undefined ? '' : webEnvironmentSlug(input.slug)
  if (input.copySharedVariables !== undefined && typeof input.copySharedVariables !== 'boolean')
    throw new BadRequestError('Le choix de copie des variables doit être explicite.')
  const fromId = access.environment?.getString('projectId') || ''
  if (!fromId && targetId === null) throw new BadRequestError('Cette application n’appartient pas à un projet.')
  if (!fromId) requireWebPermission(e, { appId: app.id }, 'app.attach')
  else if (targetId === null) requireWebPermission(e, { appId: app.id }, 'app.detach')
  else {
    // Retirer l'accès aux membres de A (owner de A) et l'ouvrir à ceux de B (owner de B).
    requireWebPermission(e, { appId: app.id }, 'app.move')
    requireWebPermission(e, { projectId: targetId }, 'app.move')
    if (targetId === fromId) throw new BadRequestError('Ce site est déjà dans ce projet.')
  }
  const result = {
    fromProjectId: fromId,
    droppedVariables: [] as string[],
    copiedVariables: [] as string[],
    previewsDisabled: false,
    notificationsReset: false,
  }
  $app.runInTransaction((tx) => {
    const current = tx.findRecordById('web_apps', app.id),
      member = webMembership(app.id, tx)
    if ((member?.getString('projectId') || '') !== fromId)
      throw new BadRequestError('Ce site a changé de projet entre-temps. Rechargez la page.')
    if (member?.getString('environment') === 'preview')
      throw new BadRequestError('Une preview temporaire ne peut pas changer de projet.')
    if (busy(app.id, tx))
      throw new BadRequestError('Attendez la fin du déploiement en cours avant de déplacer ce site.')
    if (targetId === null) {
      result.previewsDisabled = releasePreviewSource(current, fromId, user, tx)
      result.copiedVariables = detachWebAppFromProject(current, user, tx)
      return
    }
    const project = targetProject(targetId, tx)
    if (!webProjectRepositoryMatches(current, project, tx))
      throw new BadRequestError('Le projet et l’application doivent utiliser le même dépôt.')
    // Nom court : explicite, sinon l'actuel s'il est libre dans B, sinon dérivé du nom du site.
    const taken = tx
      .findRecordsByFilter('web_app_environments', 'projectId={:id}', '', 0, 0, { id: project.id })
      .map((row: core.Record) => row.getString('slug'))
    let nextSlug = slug
    if (nextSlug) assertWebEnvironmentSlug(project.id, nextSlug, tx)
    else if (member && !taken.includes(member.getString('slug'))) nextSlug = member.getString('slug')
    else nextSlug = webUniqueSlug(webSiteSlug(current.getString('name')), taken)
    if (!nextSlug) throw new BadRequestError('Aucun nom court disponible : précisez-en un.')
    const nextEnvironment = environment || member?.getString('environment') || 'prod'
    if (!member) {
      const attached = attachWebAppToProject(
        current,
        project,
        { environment: nextEnvironment, slug: nextSlug, keepVariables: input.copySharedVariables === true },
        user,
        tx
      )
      result.droppedVariables = attached.stripped
      result.notificationsReset = attached.notificationsReset
      assertMergedVariables(app.id, project, nextEnvironment, tx)
      return
    }
    assertWebPermanentCapacity(project.id, tx)
    bindWebGitSourceProject(webConfig(current), project.id, tx, app.id)
    // 1. La version publiée dans A (image, variables de build) ne tourne pas sous les membres de B.
    let active: core.Record | null = null
    try {
      active = tx.findRecordById('web_deployments', current.getString('activeDeployment'))
    } catch {}
    if (
      active &&
      !webHistoryInScope(current, webDeploymentOrigin(active), tx, project.id) &&
      current.getString('status') !== 'stopped'
    )
      throw new BadRequestError(
        'Arrêtez le site avant de le déplacer : sa version publiée vient d’un autre projet. Redéployez-le ensuite depuis le projet cible.'
      )
    // 2. Previews de A.
    result.previewsDisabled = releasePreviewSource(current, fromId, user, tx)
    // 3. Variables partagées de A (noms seulement) : copiées dans les variables locales, sinon elles ne s'appliquent plus.
    const local = decryptWebValue(current.getString('envEncrypted')),
      shared = Object.keys(
        decryptWebValue(
          webProjectVariable(fromId, member.getString('environment'), tx)?.getString('valueEncrypted') || ''
        )
      )
        .filter((name) => !has(local, name))
        .sort()
    if (input.copySharedVariables === true) {
      if (shared.length) {
        current.set('envEncrypted', encryptWebValue(resolveWebEnvironment(current, tx).env))
        tx.save(current)
      }
      result.copiedVariables = shared
    } else result.droppedVariables = shared
    // 4. Canaux d'alerte : réinitialisés si le site change d'équipe (B hors de la lignée de A).
    if (!webProjectLineage(project.id, tx).includes(fromId))
      result.notificationsReset = resetWebNotificationChannels(app.id, user.id, tx)
    // 5. Ligne mise à jour en place (index unique appId) ; la lignée de clonage ne traverse pas les projets.
    member.set('projectId', project.id)
    member.set('environment', nextEnvironment)
    member.set('slug', nextSlug)
    member.set('state', 'active')
    member.set('sourceAppId', '')
    tx.save(member)
    reconcileWebGitSourceProject(current, tx)
    assertMergedVariables(app.id, project, nextEnvironment, tx)
    // 6. Audit : action existante côté A (webAppDetachment et webHistoryInScope gardent leur sémantique).
    auditWebProject(
      fromId,
      user.id,
      'application-detached',
      { appId: app.id, sharedVariables: result.copiedVariables, movedTo: project.id },
      tx
    )
    auditWebProject(
      project.id,
      user.id,
      'application-attached',
      {
        appId: app.id,
        environment: nextEnvironment,
        slug: nextSlug,
        movedFrom: fromId,
        droppedVariables: result.droppedVariables,
        copiedVariables: result.copiedVariables,
        ...(result.previewsDisabled ? { previewsDisabled: true } : {}),
        ...(result.notificationsReset ? { notificationsReset: true } : {}),
      },
      tx
    )
  })
  const fresh = $app.findRecordById('web_apps', app.id)
  return webJson(e, {
    app: {
      ...serializeWebApp(fresh),
      access: webAppAccessView(user, fresh),
      membership: membershipOf(webMembership(fresh.id)),
    },
    ...result,
  })
}
