/** Applications web — client des routes « groupes de sites » (GET /groups, création, suppression, déplacement). */
import type {
  WebAppProjectMoveInput,
  WebAppProjectMoveResult,
  WebProjectGroupProject,
  WebProjectGroups,
} from '../../../pockethost/src/common/webProjectGroups'
import { webRequest, type WebApp, type WebCapabilities } from './webApps'

export type WebGroupedApps = WebProjectGroups<WebApp, WebCapabilities>
export type WebGroupedApp = WebGroupedApps['groups'][number]['apps'][number]

const project = (id: string) => `/projects/${encodeURIComponent(id)}`

export const webProjectGroupsLoad = () => webRequest<WebGroupedApps>('/projects/groups')
export const webProjectCreate = (name: string) =>
  webRequest<{ project: WebProjectGroupProject }>('/projects', { method: 'POST', body: { name } })
export const webProjectDelete = (id: string) =>
  webRequest<{ deleted: true; projectId: string }>(project(id), { method: 'DELETE' })
/** Réglages de preview omis : le serveur garde les valeurs actuelles (aucune réécriture d'un instantané périmé). */
export const webProjectRename = (id: string, name: string) =>
  webRequest<{ project: unknown }>(project(id), { method: 'PATCH', body: { name } })
/** Conversion définitive en groupe de sites (superAdmin). */
export const webProjectAllowRepositories = (id: string) =>
  webRequest<{ project: unknown }>(project(id), { method: 'PATCH', body: { multiRepository: true } })
export const webAppMove = (appId: string, input: WebAppProjectMoveInput) =>
  webRequest<WebAppProjectMoveResult<WebApp>>(`/${encodeURIComponent(appId)}/project`, { method: 'PUT', body: input })
