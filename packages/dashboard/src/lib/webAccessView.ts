/**
 * Applications web — logique d'affichage des droits par projet (pure, testable sans PocketBase). Le serveur reste seul
 * juge : ces fonctions masquent seulement les actions qu'il refuserait.
 */
import {
  WEB_ROLE_LABELS,
  WEB_ROLES,
  webCanManageRole,
  webCapabilities,
  webMemberChangeError,
  type WebPermission,
  type WebRole,
} from '../../../pockethost/src/common/webAccess'

export type WebViewRole = WebRole | 'platform'
export type WebCapabilityMap = Record<WebPermission, boolean>
type AccessLike = { capabilities?: Partial<WebCapabilityMap> | null } | null | undefined
type MeLike =
  | {
      enabled?: boolean
      isPlatformAdmin: boolean
      hasAccess: boolean
      projects: { id: string; capabilities: Partial<WebCapabilityMap> }[]
    }
  | null
  | undefined
type UserLike = { superAdmin?: boolean; suspension?: string } | null | undefined

/** Aucune capacité : valeur sûre tant que les droits ne sont pas connus. */
export const WEB_NO_CAPABILITIES: WebCapabilityMap = webCapabilities('')

/**
 * Capacités sur une application : la vue `access` renvoyée par l'API fait foi ; à défaut, celles du projet connu via
 * GET me ; sinon tout pour un administrateur de la plateforme, rien pour les autres.
 */
export const webAppCapabilities = (access: AccessLike, me: MeLike, projectId = ''): WebCapabilityMap => {
  if (access?.capabilities) return { ...WEB_NO_CAPABILITIES, ...access.capabilities }
  if (me?.isPlatformAdmin) return webCapabilities('platform')
  const project = projectId ? me?.projects.find((p) => p.id === projectId) : undefined
  return project ? { ...WEB_NO_CAPABILITIES, ...project.capabilities } : WEB_NO_CAPABILITIES
}

/** Superadmin actif d'après la session locale (avant la réponse de GET me). */
export const webLocalPlatformAdmin = (user: UserLike) => !!user?.superAdmin && !user?.suspension

/** Entrée « Applications web » : module activé et (superadmin actif ou au moins un projet). */
export const webNavVisible = (enabled: boolean, user: UserLike, me: MeLike) =>
  enabled && !!user && !user.suspension && (webLocalPlatformAdmin(user) || !!me?.hasAccess)

export const webRoleLabel = (role: WebViewRole | '' | null | undefined) =>
  role === 'platform' ? 'Administration plateforme' : role ? WEB_ROLE_LABELS[role] || role : 'Aucun accès'

export const WEB_ROLE_HELP: Record<WebRole, string> = {
  viewer: 'Consulte les applications, les déploiements, les logs et le nom des variables.',
  developer: 'Déploie, modifie les variables sans les révéler, lance les tâches et les sauvegardes.',
  admin: 'Gère domaines, terminal, sauvegardes, alertes, secrets, environnements et membres.',
  owner: 'Tous les droits du projet, y compris les administrateurs et le nom du projet.',
}

/** Rôles qu'un acteur peut attribuer, du plus faible au plus fort. */
export const webAssignableRoles = (actor: WebViewRole | '' | null | undefined): WebRole[] =>
  WEB_ROLES.filter((role) => webCanManageRole(actor, role))

/** Actions possibles sur une ligne de membre (mêmes règles que le serveur). */
export const webMemberActions = (
  actor: WebViewRole | '' | null | undefined,
  member: { role: WebRole; self: boolean },
  ownerCount: number
) => {
  const roles = webAssignableRoles(actor).filter(
    (role) =>
      role === member.role || !webMemberChangeError(actor, { kind: 'change', from: member.role, role, ownerCount })
  )
  return {
    roles,
    canChange: roles.some((role) => role !== member.role),
    canRemove: !member.self && !webMemberChangeError(actor, { kind: 'remove', from: member.role, ownerCount }),
    canLeave: member.self && !webMemberChangeError(actor, { kind: 'leave', from: member.role, ownerCount }),
  }
}

/** Onglets d'une application et la permission minimale pour les afficher. */
export const WEB_APP_TABS: { id: string; label: string; permission: WebPermission; project?: boolean }[] = [
  { id: 'deployments', label: 'Déploiements', permission: 'app.read' },
  { id: 'runtime-logs', label: 'Logs du site', permission: 'logs.read' },
  { id: 'environments', label: 'Environnements', permission: 'app.read' },
  { id: 'members', label: 'Membres', permission: 'members.read', project: true },
  { id: 'runtime-profile', label: 'Moteur', permission: 'app.read' },
  { id: 'storage', label: 'Stockage', permission: 'app.read' },
  { id: 'ci', label: 'CI / Images', permission: 'ci.manage' },
  { id: 'notifications', label: 'Alertes', permission: 'app.read' },
  { id: 'domains', label: 'Domaines', permission: 'app.read' },
  { id: 'environment', label: 'Variables', permission: 'variables.read' },
  { id: 'terminal', label: 'Terminal', permission: 'terminal.open' },
  { id: 'health', label: 'Santé', permission: 'app.read' },
  { id: 'tasks', label: 'Tâches planifiées', permission: 'app.read' },
  { id: 'backups', label: 'Sauvegardes', permission: 'app.read' },
  { id: 'settings', label: 'Configuration', permission: 'app.read' },
  { id: 'webhooks', label: 'Activité GitHub', permission: 'app.read' },
  { id: 'delete', label: 'Supprimer l’application', permission: 'app.delete' },
]
export const webAppTabs = (capabilities: WebCapabilityMap, hasProject: boolean) =>
  WEB_APP_TABS.filter((tab) => capabilities[tab.permission] && (!tab.project || hasProject))
