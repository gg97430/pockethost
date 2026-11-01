/**
 * Applications web — projets « groupes de sites ». Types de l'API et règles pures partagés par les hooks mothership
 * (Goja JSVM) et le dashboard.
 */
import type { WebPermission, WebRole } from './webAccess'
import type { WebProjectLimits } from './webProjectLimits'

export const WEB_PROJECT_NAME_MAX = 80
export const WEB_SITE_SLUG = /^[a-z0-9][a-z0-9-]{0,59}$/
/** Plafonds de GET /api/web-projects/groups (au-delà : `truncated: true`). */
export const WEB_PROJECT_GROUP_LIMITS = { projects: 200, apps: 1000 } as const
export type WebPermanentEnvironment = 'dev' | 'staging' | 'prod'

/** '' si valide, sinon le message d'erreur français (HTTP 400). */
export const webProjectNameError = (value: unknown) =>
  typeof value === 'string' && value.trim() && value.trim().length <= WEB_PROJECT_NAME_MAX
    ? ''
    : 'Nom de projet requis (80 caractères maximum).'

const ACCENTS: Record<string, string> = {
  à: 'a',
  á: 'a',
  â: 'a',
  ã: 'a',
  ä: 'a',
  å: 'a',
  æ: 'ae',
  ç: 'c',
  è: 'e',
  é: 'e',
  ê: 'e',
  ë: 'e',
  ì: 'i',
  í: 'i',
  î: 'i',
  ï: 'i',
  ñ: 'n',
  ò: 'o',
  ó: 'o',
  ô: 'o',
  õ: 'o',
  ö: 'o',
  œ: 'oe',
  ù: 'u',
  ú: 'u',
  û: 'u',
  ü: 'u',
  ý: 'y',
  ÿ: 'y',
}
/** Nom court d'un site déduit de son nom : « Back-office Été » -> « back-office-ete » ; jamais vide (« site »). */
export const webSiteSlug = (name: unknown) => {
  const slug = `${name ?? ''}`
    .toLowerCase()
    .replace(/[^\x00-\x7f]/g, (c) => ACCENTS[c] ?? '-')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+/, '')
    .slice(0, 60)
    .replace(/-+$/, '')
  return slug || 'site'
}
/** `base`, sinon `base-2` … `base-99` (tronqué à 60 caractères) ; '' si tout est pris. */
export const webUniqueSlug = (base: string, taken: readonly string[]) => {
  const used = new Set(taken)
  if (!used.has(base)) return base
  for (let n = 2; n < 100; n++) {
    const suffix = `-${n}`,
      candidate = `${base.slice(0, 60 - suffix.length).replace(/-+$/, '')}${suffix}`
    if (!used.has(candidate)) return candidate
  }
  return ''
}

export type WebGroupRole = WebRole | 'platform'
export type WebGroupCapabilities = Record<WebPermission, boolean>
/** Projet tel que renvoyé par GET /api/web-projects/groups et POST /api/web-projects. */
export type WebProjectGroupProject = {
  id: string
  name: string
  /** Mono-dépôt : dépôt du projet. Groupe : dépôt des previews ('' si jamais activées). */
  repository: string
  multiRepository: boolean
  allowedRepositories: string[]
  previewEnabled: boolean
  previewSourceAppId: string
  previewMax: number
  previewTtlHours: number
  limits?: WebProjectLimits
  deletion?: { id: string; status: string; stage: string; error: string } | null
  role: WebGroupRole
  memberRole: WebRole | null
  capabilities: WebGroupCapabilities
  /** Sites permanents (dev, staging, prod). */
  siteCount: number
  /** Previews de pull request avec une application. */
  previewCount: number
}
export type WebProjectGroupMembership = {
  id: string
  environment: WebPermanentEnvironment | 'preview'
  slug: string
  state: string
  previewNumber: number
  sourceAppId: string
}
export type WebProjectGroupAccess = {
  projectId: string
  role: WebGroupRole | null
  memberRole: WebRole | null
  capabilities: WebGroupCapabilities
}
/** `App` = forme de serializeWebApp (dashboard : WebApp). */
export type WebProjectGroupApp<App> = App & {
  access: WebProjectGroupAccess
  membership: WebProjectGroupMembership | null
}
export type WebProjectGroup<App> = { project: WebProjectGroupProject; apps: WebProjectGroupApp<App>[] }
export type WebProjectGroups<App, Status = Record<string, unknown>> = {
  enabled: true
  isPlatformAdmin: boolean
  /** État du worker, même objet que `capabilities` de GET /api/web-apps. */
  capabilities: Status
  groups: WebProjectGroup<App>[]
  /** Null pour un non-superAdmin (aucune application sans projet ne lui est visible). */
  unassigned: { apps: WebProjectGroupApp<App>[] } | null
  truncated: boolean
}
/** Corps de PUT /api/web-apps/{id}/project. */
export type WebAppProjectMoveInput = {
  projectId: string | null
  environment?: WebPermanentEnvironment
  slug?: string
  copySharedVariables?: boolean
}
export type WebAppProjectMoveResult<App> = {
  app: WebProjectGroupApp<App>
  fromProjectId: string
  /** Noms (jamais les valeurs) des variables partagées de l'ancien projet qui ne s'appliquent plus. */
  droppedVariables: string[]
  /** Noms copiés dans les variables locales (copySharedVariables, ou retrait vers « Sans projet »). */
  copiedVariables: string[]
  /** Le site était le modèle de previews de l'ancien projet : previews désactivées. */
  previewsDisabled: boolean
  notificationsReset: boolean
}
