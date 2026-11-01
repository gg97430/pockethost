/**
 * Applications web — contrôle d'accès central (source unique de vérité). Résolution : application ->
 * web_app_environments (appId unique) -> projet. Une application sans projet (« legacy ») reste réservée aux
 * superAdmins. Module autonome (aucun import de ./api ou ./projects) pour éviter les cycles. Les primitives
 * (suspension, collection users, superAdmin => tout, rôle de membre, filtres de liste) viennent de
 * `security/webProjectAccess.ts`, calqué sur `security/instanceAccess.ts`.
 */
import {
  type WebPermission,
  type WebRole,
  isWebPlatformPermission,
  webCapabilities,
  webRoleAllows,
} from '../../../../../common/webAccess'
import {
  WEB_PROJECT_MEMBERS,
  isWebPlatformAdmin,
  webAppAccessQuery,
  webProjectAccessQuery,
  webRoleFor,
} from '../../security/webProjectAccess'
import { requireOperatorAdmin } from '../operatorAdmin/auth'
import { assertWebNotDeleting } from './deletionState'
export {
  canAccessWebProject,
  canSetWebRepository,
  isWebPlatformAdmin,
  webAppAccessQuery,
  webProjectAccessQuery,
  webProjectAllowedRepositories,
  webProjectMemberRecord,
  webRoleFor,
} from '../../security/webProjectAccess'

export const WEB_MEMBERS = WEB_PROJECT_MEMBERS
export type WebEffectiveRole = WebRole | 'platform'
export type WebAccessTarget = { projectId?: string; appId?: string; environmentId?: string }
export type WebAccess = {
  user: core.Record
  role: WebEffectiveRole
  platform: boolean
  /** Rôle de membre réel (même pour un superAdmin), '' si aucun. */
  memberRole: WebRole | ''
  project: core.Record | null
  app: core.Record | null
  /** Ligne web_app_environments de l'application (null pour une application legacy ou une cible projet). */
  environment: core.Record | null
}

const ID = /^[a-z0-9]{15}$/
const enabled = () => $os.getenv('WEB_APPS_ENABLED') === 'true'
const notFound = (target: WebAccessTarget) =>
  new NotFoundError(
    target.projectId && !target.appId && !target.environmentId ? 'Projet introuvable.' : 'Application introuvable.'
  )

/** Ancienne sémantique de requireWebAdmin : superAdmin non suspendu et module activé. */
export const requirePlatformAdmin = (e: core.RequestEvent) => {
  const user = requireOperatorAdmin(e)
  if (!enabled()) throw new ForbiddenError('Applications web désactivées sur ce serveur.')
  return user
}
/** Utilisateur dashboard authentifié, non suspendu, module activé. */
export const requireWebUser = (e: core.RequestEvent) => {
  const user = e.auth
  if (!user) throw new UnauthorizedError('Authentification requise.')
  if (user.collection().name !== 'users' || user.getString('suspension')) throw new ForbiddenError('Accès refusé.')
  if (!enabled()) throw new ForbiddenError('Applications web désactivées sur ce serveur.')
  return user
}

/** Rôle effectif : 'platform' pour un superAdmin non suspendu, '' si aucun accès. */
export const webEffectiveRole = (user: core.Record, projectId: string, store: any = $app): WebEffectiveRole | '' => {
  if (user.getString('suspension')) return ''
  if (isWebPlatformAdmin(user)) return 'platform'
  return projectId ? webRoleFor(projectId, user, store) : ''
}
/** Null = tous les projets (superAdmin). Sinon les projets dont l'utilisateur est membre. */
export const listAccessibleProjectIds = (user: core.Record, store: any = $app): string[] | null => {
  if (isWebPlatformAdmin(user)) return null
  if (user.getString('suspension')) return []
  const q = webProjectAccessQuery(user, 'id')
  return store
    .findRecordsByFilter('web_projects', q.filter, '-created', 1000, 0, q.params)
    .map((r: core.Record) => r.id)
}
/** Adhésions de l'utilisateur : [{ projectId, role }]. */
export const listWebMemberships = (user: core.Record, store: any = $app) =>
  store
    .findRecordsByFilter(WEB_MEMBERS, 'user={:user}', 'created', 1000, 0, { user: user.id })
    .map((r: core.Record) => ({ projectId: r.getString('project'), role: r.getString('role') as WebRole }))
/**
 * Identifiants d'applications accessibles ; null = toutes (superAdmin). Pour une liste paginée, utiliser
 * webAppAccessQuery.
 */
export const listAccessibleWebAppIds = (user: core.Record, store: any = $app): string[] | null => {
  if (isWebPlatformAdmin(user)) return null
  const q = webAppAccessQuery(user, 'id')
  return store.findRecordsByFilter('web_apps', q.filter, '-created', 1000, 0, q.params).map((r: core.Record) => r.id)
}

const appEnvironment = (appId: string, store: any): core.Record | null => {
  try {
    return store.findFirstRecordByFilter('web_app_environments', 'appId={:id}', { id: appId })
  } catch {
    return null
  }
}
/** Résout la cible sans contrôle de droits. Lève 404 uniforme si introuvable. */
export const resolveWebAccessTarget = (target: WebAccessTarget, store: any = $app) => {
  let app: core.Record | null = null,
    environment: core.Record | null = null,
    project: core.Record | null = null
  const find = (collection: string, id: string) => {
    try {
      return store.findRecordById(collection, id) as core.Record
    } catch {
      throw notFound(target)
    }
  }
  if (target.appId !== undefined) {
    if (!ID.test(target.appId || '')) throw new BadRequestError('Application invalide.')
    app = find('web_apps', target.appId)
    environment = appEnvironment(app.id, store)
  } else if (target.environmentId !== undefined) {
    if (!ID.test(target.environmentId || '')) throw new BadRequestError('Environnement invalide.')
    environment = find('web_app_environments', target.environmentId)
    if (environment.getString('appId'))
      try {
        app = store.findRecordById('web_apps', environment.getString('appId'))
      } catch {}
  }
  const projectId = environment ? environment.getString('projectId') : target.projectId
  if (projectId !== undefined && projectId !== '') {
    if (!ID.test(projectId)) throw new BadRequestError('Projet invalide.')
    if (target.projectId && environment && target.projectId !== projectId) throw notFound(target)
    project = find('web_projects', projectId)
  }
  if (!app && !project) throw notFound(target)
  return { app, environment, project }
}

const check = (user: core.Record, target: WebAccessTarget, permission: WebPermission, store: any): WebAccess => {
  const platform = isWebPlatformAdmin(user)
  if (!platform && isWebPlatformPermission(permission)) {
    // Aucune résolution de la cible : un non-superAdmin ne peut pas sonder l'existence d'un identifiant.
    throw new ForbiddenError('Action réservée aux administrateurs de la plateforme.')
  }
  const { app, environment, project } = resolveWebAccessTarget(target, store)
  const memberRole = project ? webRoleFor(project, user, store) : ''
  // Non membre ou application legacy : même réponse qu'un identifiant inexistant (pas d'énumération).
  if (!platform && !memberRole) throw notFound(target)
  if (!platform && !webRoleAllows(memberRole, permission))
    throw new ForbiddenError('Droits insuffisants sur ce projet.')
  if (app) assertWebNotDeleting(app, permission)
  if (project) assertWebNotDeleting(project, permission)
  if (platform) return { user, role: 'platform', platform, memberRole, project, app, environment }
  return { user, role: memberRole as WebRole, platform, memberRole, project, app, environment }
}
/**
 * Garde centrale des routes Applications web. superAdmin non suspendu : tout. Sinon rôle de projet >= rôle minimal de
 * la permission. 401 sans session, 403 suspendu/module désactivé/droits insuffisants, 404 si inaccessible.
 */
export const requireWebPermission = (
  e: core.RequestEvent,
  target: WebAccessTarget,
  permission: WebPermission,
  store: any = $app
): WebAccess => check(requireWebUser(e), target, permission, store)
/** Raccourci pour les routes /api/web-apps/{id}/... */
export const requireWebAppPermission = (e: core.RequestEvent, permission: WebPermission, store: any = $app) =>
  requireWebPermission(e, { appId: e.request!.pathValue('id') }, permission, store)
/** Raccourci pour les routes /api/web-projects/{projectId}/... */
export const requireWebProjectPermission = (e: core.RequestEvent, permission: WebPermission, store: any = $app) =>
  requireWebPermission(e, { projectId: e.request!.pathValue('projectId') }, permission, store)
/** Variante non bloquante (même règles), utile pour décider d'un sous-cas dans un handler. */
export const webCan = (user: core.Record, target: WebAccessTarget, permission: WebPermission, store: any = $app) => {
  try {
    check(user, target, permission, store)
    return true
  } catch {
    return false
  }
}
/**
 * Revalidation des identifiants longue durée (terminal, jetons CI, tâches, notifications) : l'acteur existe, n'est pas
 * suspendu, et a encore la permission sur le projet ACTUEL de la cible. Ne dépend pas de WEB_APPS_ENABLED (les
 * appelants internes le vérifient déjà).
 */
export const webActorCan = (actorId: string, target: WebAccessTarget, permission: WebPermission, store: any = $app) => {
  if (!ID.test(actorId || '')) return false
  let actor: core.Record
  try {
    actor = store.findRecordById('users', actorId)
  } catch {
    return false
  }
  if (actor.getString('suspension')) return false
  return webCan(actor, target, permission, store)
}
/** Vue d'accès pour une application, à joindre aux réponses (dashboard). */
export const webAppAccessView = (user: core.Record, app: core.Record, store: any = $app) => {
  const environment = appEnvironment(app.id, store),
    projectId = environment?.getString('projectId') || '',
    role = webEffectiveRole(user, projectId, store)
  return {
    projectId,
    role: role === 'platform' ? 'platform' : role || null,
    memberRole: projectId ? webRoleFor(projectId, user, store) || null : null,
    capabilities: webCapabilities(role),
  }
}
/**
 * Réglages d'application (tâches planifiées, alertes) : ils appartiennent à l'application, pas à leur auteur. Actifs
 * tant que l'application existe et appartient à un projet ; pour une application legacy (sans projet), tant que
 * l'auteur reste superAdmin non suspendu. Le départ ou la rétrogradation de l'auteur ne les coupe pas. Toute
 * modification de ces réglages exige la permission au moment de l'écriture (requireWebAppPermission).
 */
export const webAppSettingActive = (appId: string, actorId: string, store: any = $app) => {
  if (!ID.test(appId || '')) return false
  try {
    if (store.findRecordById('web_apps', appId).getString('deletionId')) return false
  } catch {
    return false
  }
  if (appEnvironment(appId, store)) return true
  try {
    return isWebPlatformAdmin(store.findRecordById('users', actorId))
  } catch {
    return false
  }
}
