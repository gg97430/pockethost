import { client } from '$src/pocketbase-client'
import type { WebMinRole, WebPermission, WebRole } from '../../../pockethost/src/common/webAccess'
export {
  WEB_PERMISSIONS,
  WEB_ROLE_LABELS,
  WEB_ROLES,
  webCanManageRole,
  webCapabilities,
  webMemberChangeError,
  webRoleAllows,
  webRoleRank,
} from '../../../pockethost/src/common/webAccess'
export type { WebMinRole, WebPermission, WebRole } from '../../../pockethost/src/common/webAccess'

/** Rôle effectif : 'platform' = superAdmin non suspendu. */
export type WebEffectiveRole = WebRole | 'platform'
export type WebCapabilityMap = Record<WebPermission, boolean>
export type WebAccessProject = {
  id: string
  name: string
  repository: string
  /** Groupe de sites : ses sites peuvent venir de dépôts différents. */
  multiRepository: boolean
  role: WebEffectiveRole
  memberRole: WebRole | null
  capabilities: WebCapabilityMap
}
/** Réponse de GET /api/web-access/me. */
export type WebAccessMe = {
  enabled: boolean
  isPlatformAdmin: boolean
  /** True pour un superAdmin : tout est permis ; `projects` ne liste alors que ses adhésions explicites. */
  allProjects: boolean
  hasAccess: boolean
  roles: WebRole[]
  permissions: Record<WebPermission, WebMinRole>
  globalCapabilities: WebCapabilityMap
  projects: WebAccessProject[]
}
/** Vue d'accès jointe aux réponses d'application (`access`). */
export type WebAppAccess = {
  projectId: string
  role: WebEffectiveRole | null
  memberRole: WebRole | null
  capabilities: WebCapabilityMap
}
export type WebProjectMember = {
  id: string
  projectId: string
  userId: string
  email: string
  role: WebRole
  invitedBy: { id: string; email: string } | null
  self: boolean
  created: string
  updated: string
}
export type WebProjectMembers = {
  projectId: string
  role: WebEffectiveRole
  capabilities: WebCapabilityMap
  items: WebProjectMember[]
}
export type WebAccessAuditEntry = {
  id: string
  action: 'member-added' | 'member-role-changed' | 'member-removed' | 'member-left'
  actor: { id: string; email: string } | null
  details: { userId?: string; email?: string; role?: WebRole; from?: WebRole }
  created: string
}

const send = <T>(path: string, options: Record<string, unknown> = {}) =>
  client().client.send<T>(path, { ...options, requestKey: null })
const members = (projectId: string) => `/api/web-projects/${encodeURIComponent(projectId)}/members`

export const webAccessMe = () => send<WebAccessMe>('/api/web-access/me')
export const webProjectMembers = (projectId: string) => send<WebProjectMembers>(members(projectId))
export const webProjectMemberAdd = (projectId: string, email: string, role: WebRole) =>
  send<{ member: WebProjectMember }>(members(projectId), { method: 'POST', body: { email, role } })
export const webProjectMemberRole = (projectId: string, memberId: string, role: WebRole) =>
  send<{ member: WebProjectMember }>(`${members(projectId)}/${encodeURIComponent(memberId)}`, {
    method: 'PATCH',
    body: { role },
  })
export const webProjectMemberRemove = (projectId: string, memberId: string) =>
  send<{ removed: true }>(`${members(projectId)}/${encodeURIComponent(memberId)}`, { method: 'DELETE' })
export const webProjectLeave = (projectId: string) =>
  send<{ left: true }>(`${members(projectId)}/leave`, { method: 'POST', body: {} })
export const webProjectAccessAudit = (projectId: string, page = 1) =>
  send<{ page: number; hasMore: boolean; items: WebAccessAuditEntry[] }>(`${members(projectId)}/audit?page=${page}`)
/** Capacité d'un projet ; un superAdmin a tout, un non-membre rien. */
export const webCan = (me: WebAccessMe | null | undefined, projectId: string, permission: WebPermission) =>
  !!me && (me.isPlatformAdmin || !!me.projects.find((p) => p.id === projectId)?.capabilities[permission])
/** Attribution des projets d'un utilisateur par un superAdmin (même parcours que UserInstanceAccess). */
export type WebUserProjectAssignment = { projectId: string; name: string; repository: string; role: WebRole | null }
export type WebUserProjects = { userId: string; superAdmin: boolean; items: WebUserProjectAssignment[] }
export const webUserProjects = (userId: string) =>
  send<WebUserProjects>(`/api/web-access/users/${encodeURIComponent(userId)}/projects`)
export const webUserProjectsSave = (userId: string, projects: { projectId: string; role: WebRole }[]) =>
  send<WebUserProjects>(`/api/web-access/users/${encodeURIComponent(userId)}/projects`, {
    method: 'PUT',
    body: { projects },
  })
