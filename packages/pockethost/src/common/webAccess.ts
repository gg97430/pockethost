/**
 * Applications web — rôles par projet. Logique pure, partagée par les hooks mothership (Goja JSVM : synchrone, sans API
 * Node) et le dashboard.
 */
export const WEB_ROLES = ['viewer', 'developer', 'admin', 'owner'] as const
export type WebRole = (typeof WEB_ROLES)[number]
/** Rôle minimal requis, ou `platform` pour un superAdmin non suspendu uniquement. */
export type WebMinRole = WebRole | 'platform'

export const WEB_ROLE_LABELS: Record<WebRole, string> = {
  viewer: 'Lecture',
  developer: 'Développement',
  admin: 'Administration',
  owner: 'Propriétaire',
}

export const WEB_PERMISSIONS = {
  // viewer
  'project.read': 'viewer',
  'members.read': 'viewer',
  'app.read': 'viewer',
  'logs.read': 'viewer',
  'variables.read': 'viewer',
  // developer
  'app.deploy': 'developer',
  'variables.write': 'developer',
  'tasks.run': 'developer',
  'backups.create': 'developer',
  'previews.manage': 'developer',
  // admin
  'app.configure': 'admin',
  'variables.reveal': 'admin',
  'terminal.open': 'admin',
  'domains.manage': 'admin',
  'backups.manage': 'admin',
  'storage.manage': 'admin',
  'notifications.manage': 'admin',
  'tasks.manage': 'admin',
  'health.manage': 'admin',
  'ci.manage': 'admin',
  'environments.manage': 'admin',
  'members.manage': 'admin',
  'project.update': 'admin',
  'alerts.test': 'admin',
  // réservées (clés de déploiement Git, workflow parallèle) : limitation de débit obligatoire (SSRF)
  'source.deployKey.rotate': 'admin',
  'source.hostKey.scan': 'admin',
  // owner
  'members.manageAdmins': 'owner',
  'project.rename': 'owner',
  'project.delete': 'owner',
  'app.delete': 'owner',
  // déplacer un site entre deux projets : exigé sur le projet d'origine ET sur le projet cible
  'app.move': 'owner',
  // superAdmin de la plateforme uniquement
  'platform.github': 'platform',
  'platform.recovery': 'platform',
  'platform.settings': 'platform',
  'project.create': 'platform',
  'app.create': 'platform',
  // ranger une application « Sans projet » dans un projet / la retirer de son projet (seule la plateforme voit « Sans projet »)
  'app.attach': 'platform',
  'app.detach': 'platform',
  'app.repository': 'platform',
  'source.git.configure': 'platform',
} as const satisfies Record<string, WebMinRole>
export type WebPermission = keyof typeof WEB_PERMISSIONS
export const WEB_PERMISSION_NAMES = Object.keys(WEB_PERMISSIONS) as WebPermission[]

export const isWebRole = (value: unknown): value is WebRole =>
  typeof value === 'string' && (WEB_ROLES as readonly string[]).indexOf(value) >= 0
export const isWebPermission = (value: unknown): value is WebPermission =>
  typeof value === 'string' && Object.prototype.hasOwnProperty.call(WEB_PERMISSIONS, value)
/** -1 pour une valeur inconnue ou vide : aucun accès. */
export const webRoleRank = (role: unknown) => (isWebRole(role) ? WEB_ROLES.indexOf(role) : -1)
export const webPermissionMinRole = (permission: WebPermission): WebMinRole => WEB_PERMISSIONS[permission]
export const isWebPlatformPermission = (permission: WebPermission) => WEB_PERMISSIONS[permission] === 'platform'

/** `platform` = superAdmin non suspendu ; un rôle de projet n'ouvre jamais une permission plateforme. */
export const webRoleAllows = (role: WebRole | 'platform' | '' | null | undefined, permission: WebPermission) => {
  if (!isWebPermission(permission)) return false
  if (role === 'platform') return true
  const min = WEB_PERMISSIONS[permission]
  if (min === 'platform') return false
  const rank = webRoleRank(role)
  return rank >= 0 && rank >= webRoleRank(min)
}
export const webCapabilities = (role: WebRole | 'platform' | '' | null | undefined) => {
  const out = {} as Record<WebPermission, boolean>
  for (const name of WEB_PERMISSION_NAMES) out[name] = webRoleAllows(role, name)
  return out
}

/** Un acteur peut-il gérer une ligne de membre de ce rôle (ou attribuer ce rôle) ? */
export const webCanManageRole = (actor: WebRole | 'platform' | '' | null | undefined, role: WebRole) => {
  if (!isWebRole(role)) return false
  if (actor === 'platform' || actor === 'owner') return true
  return actor === 'admin' && webRoleRank(role) < webRoleRank('admin')
}

export type WebMemberChange =
  | { kind: 'add'; role: unknown }
  | { kind: 'change'; from: WebRole; role: unknown; ownerCount: number }
  | { kind: 'remove'; from: WebRole; ownerCount: number }
  | { kind: 'leave'; from: WebRole; ownerCount: number }

export type WebMemberChangeError = '' | { code: 'invalid' | 'forbidden' | 'last-owner'; message: string }
/**
 * Règles d'escalade. Retourne '' si autorisé, sinon { code, message } (message en français). `ownerCount` = nombre
 * actuel de propriétaires du projet, ligne ciblée comprise. Un projet qui a au moins un propriétaire en garde toujours
 * au moins un. HTTP conseillé : invalid -> 400, forbidden -> 403, last-owner -> 400.
 */
export const webMemberChangeError = (
  actor: WebRole | 'platform' | '' | null | undefined,
  change: WebMemberChange
): WebMemberChangeError => {
  const fail = (code: 'invalid' | 'forbidden' | 'last-owner', message: string) => ({ code, message })
  const lastOwner = (from: WebRole, ownerCount: number) => from === 'owner' && ownerCount <= 1
  if (change.kind === 'leave') {
    if (!isWebRole(change.from)) return fail('invalid', 'Vous n’êtes pas membre de ce projet.')
    return lastOwner(change.from, change.ownerCount)
      ? fail(
          'last-owner',
          'Vous êtes le dernier propriétaire. Nommez un autre propriétaire avant de quitter le projet.'
        )
      : ''
  }
  if (actor !== 'platform' && !webRoleAllows(actor, 'members.manage'))
    return fail('forbidden', 'Droits insuffisants pour gérer les membres.')
  if (change.kind === 'add' || change.kind === 'change') {
    if (!isWebRole(change.role)) return fail('invalid', 'Rôle attendu : viewer, developer, admin ou owner.')
    if (!webCanManageRole(actor, change.role))
      return fail('forbidden', 'Seul un propriétaire peut attribuer le rôle administrateur ou propriétaire.')
  }
  if (change.kind === 'add') return ''
  if (!webCanManageRole(actor, change.from))
    return fail('forbidden', 'Seul un propriétaire peut modifier un administrateur ou un propriétaire.')
  if (change.kind === 'change' && change.role === change.from) return ''
  return lastOwner(change.from, change.ownerCount)
    ? fail('last-owner', 'Un projet doit garder au moins un propriétaire.')
    : ''
}
