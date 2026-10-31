import { type WebRole, WEB_ROLES, isWebRole, webRoleRank } from '../../../../common/webAccess'

/**
 * Applications web : même modèle que `instanceAccess.ts` (canManageInstance / instanceAccessQuery). Refus si l'acteur
 * n'est pas un utilisateur dashboard ou est suspendu ; superAdmin => tout (1=1). Le rôle d'un utilisateur sur un projet
 * est porté par `web_project_members` (une relation sur users ne peut pas porter de rôle). Collection à règles API
 * nulles : aucune auto-attribution possible via l'API PocketBase.
 */
export const WEB_PROJECT_MEMBERS = 'web_project_members'
const ID = /^[a-z0-9]{15}$/
const usable = (user?: core.Record | null): user is core.Record =>
  !!user && user.collection().name === 'users' && !user.getString('suspension')
const projectIdOf = (project: core.Record | string) => (typeof project === 'string' ? project : project.id)
const rolesFrom = (minRole: WebRole) => WEB_ROLES.filter((role) => webRoleRank(role) >= webRoleRank(minRole))

export const isWebPlatformAdmin = (user?: core.Record | null) => usable(user) && user.getBool('superAdmin')

export const webProjectMemberRecord = (
  project: core.Record | string,
  user: core.Record | string,
  store: any = $app
): core.Record | null => {
  const projectId = projectIdOf(project),
    userId = typeof user === 'string' ? user : user.id
  if (!ID.test(userId || '') || !ID.test(projectId || '')) return null
  try {
    return store.findFirstRecordByFilter(WEB_PROJECT_MEMBERS, 'project={:project} && user={:user}', {
      project: projectId,
      user: userId,
    })
  } catch {
    return null
  }
}
/** Rôle de membre réel ('' si non membre, suspendu ou hors collection users). N'inclut pas le statut superAdmin. */
export const webRoleFor = (
  project: core.Record | string,
  user?: core.Record | null,
  store: any = $app
): WebRole | '' => {
  if (!usable(user)) return ''
  const role = webProjectMemberRecord(project, user, store)?.getString('role')
  return isWebRole(role) ? role : ''
}
/** Accès d'un utilisateur à un projet avec au moins `minRole` (superAdmin non suspendu : toujours). */
export const canAccessWebProject = (
  project: core.Record | string,
  user?: core.Record | null,
  minRole: WebRole = 'viewer',
  store: any = $app
) =>
  usable(user) && (user.getBool('superAdmin') || webRoleRank(webRoleFor(project, user, store)) >= webRoleRank(minRole))

/**
 * Filtre de liste par projet accessible, à combiner avec `&&` : `projectField` est le champ de la collection interrogée
 * qui porte l'identifiant du projet (`id` pour web_projects, `projectId` pour web_app_environments,
 * web_project_variables, web_project_audit ; `project` pour une relation). Jointure `@collection`, donc pas de limite
 * du nombre d'adhésions (un filtre `id={:a0} || ...` est refusé par PocketBase au-delà d'environ 200 termes).
 */
export const webProjectAccessQuery = (user: core.Record, projectField = 'project', minRole: WebRole = 'viewer') => {
  const params: Record<string, string> = { uid: user.id }
  if (!usable(user)) return { filter: '1=0', params }
  if (user.getBool('superAdmin')) return { filter: '1=1', params }
  const m = `@collection.${WEB_PROJECT_MEMBERS}:wpa`
  const roles = rolesFrom(minRole)
    .map((role) => `${m}.role ?= "${role}"`)
    .join(' || ')
  return { filter: `(${m}.user ?= {:uid} && ${m}.project ?= ${projectField} && (${roles}))`, params }
}
/**
 * Filtre de liste d'applications accessibles (web_apps : `appField = 'id'` ; collections filles : `appId`). Résolution
 * application -> web_app_environments.appId -> projectId -> membre. Une application legacy (sans projet) n'est visible
 * que d'un superAdmin.
 */
export const webAppAccessQuery = (user: core.Record, appField = 'id', minRole: WebRole = 'viewer') => {
  const params: Record<string, string> = { uid: user.id }
  if (!usable(user)) return { filter: '1=0', params }
  if (user.getBool('superAdmin')) return { filter: '1=1', params }
  const m = `@collection.${WEB_PROJECT_MEMBERS}:wpa`,
    env = '@collection.web_app_environments:wpe'
  const roles = rolesFrom(minRole)
    .map((role) => `${m}.role ?= "${role}"`)
    .join(' || ')
  return {
    filter: `(${env}.appId ?= ${appField} && ${env}.projectId ?= ${m}.project && ${m}.user ?= {:uid} && (${roles}))`,
    params,
  }
}

/** Dépôts « propriétaire/dépôt » que les admins du projet peuvent choisir (liste gérée par un superAdmin). */
export const webProjectAllowedRepositories = (project: core.Record): string[] => {
  let value: unknown
  try {
    value = JSON.parse(project.getString('allowedRepositories') || '[]')
  } catch {
    return []
  }
  return Array.isArray(value)
    ? value.filter((r): r is string => typeof r === 'string' && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(r))
    : []
}
/**
 * Un utilisateur peut-il donner ce dépôt à une application du projet ? superAdmin : toujours. Sinon rôle >= admin ET
 * dépôt (insensible à la casse) présent dans allowedRepositories ; liste vide => superAdmin uniquement. Le dépôt vide
 * (moteur non source) et le dépôt actuel inchangé ne sont pas des changements de dépôt.
 */
export const canSetWebRepository = (
  project: core.Record | null,
  user: core.Record | null | undefined,
  repository: string,
  current = '',
  store: any = $app
) => {
  if (isWebPlatformAdmin(user)) return true
  const wanted = `${repository || ''}`.toLowerCase()
  if (!project || !canAccessWebProject(project, user, 'admin', store)) return false
  if (wanted === `${current || ''}`.toLowerCase()) return true
  return !!wanted && webProjectAllowedRepositories(project).some((r) => r.toLowerCase() === wanted)
}
