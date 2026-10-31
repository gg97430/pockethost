/**
 * Applications web — affichage des projets « groupes de sites » (pur, testable sans PocketBase). Le serveur reste seul
 * juge : ces fonctions ne proposent que les actions qu'il accepterait.
 */
import type { WebPermission } from '../../../pockethost/src/common/webAccess'
import {
  webSiteSlug,
  webUniqueSlug,
  type WebAppProjectMoveResult,
  type WebPermanentEnvironment,
  type WebProjectGroupMembership,
} from '../../../pockethost/src/common/webProjectGroups'

/** Clé de repli mémorisée pour le groupe « Sans projet ». */
export const WEB_UNASSIGNED_GROUP = '__unassigned__'
export const WEB_GROUP_STATE_KEY = 'web-project-groups:v1'
export const WEB_ENVIRONMENT_LABELS: Record<WebPermanentEnvironment, string> = {
  prod: 'Production',
  staging: 'Préproduction',
  dev: 'Développement',
}
export const WEB_UNASSIGNED_LABEL = 'Sans projet (administrateurs de la plateforme uniquement)'

type Capabilities = Partial<Record<WebPermission, boolean>> | null | undefined
type ProjectLike = { id: string; name: string; siteCount: number; previewCount: number; deletion?: unknown }
type AppLike = {
  name: string
  deletion?: unknown
  access: { projectId: string; capabilities?: Capabilities }
  membership: Pick<WebProjectGroupMembership, 'environment' | 'slug'> | null
}
type GroupLike = { project: ProjectLike & { capabilities: Capabilities }; apps: AppLike[] }
export type WebMoveTarget = { projectId: string | null; label: string }

const plural = (n: number, one: string, many: string) => (n === 1 ? `1 ${one}` : `${n} ${many}`)

/** « Aucun site », « 1 site », « 3 sites », suivi de « · 1 preview » / « · n previews » s'il y en a. */
export const webSiteCountLabel = (sites: number, previews = 0) => {
  const base = sites > 0 ? plural(sites, 'site', 'sites') : 'Aucun site'
  return previews > 0 ? `${base} · ${plural(previews, 'preview', 'previews')}` : base
}

/** Libellé d'environnement d'une ligne de site (« Preview PR #12 » pour une preview). */
export const webMembershipLabel = (membership: Pick<WebProjectGroupMembership, 'environment' | 'previewNumber'>) =>
  membership.environment === 'preview'
    ? `Preview PR #${membership.previewNumber}`
    : WEB_ENVIRONMENT_LABELS[membership.environment] || membership.environment

const collator = new Intl.Collator('fr', { sensitivity: 'base' })
/** Groupes par nom (le serveur trie déjà ; l'ordre reste stable après une mise à jour locale). */
export const webSortGroups = <T extends { project: { name: string } }>(groups: readonly T[]) =>
  [...groups].sort((a, b) => collator.compare(a.project.name, b.project.name))

/** État de repli mémorisé ; tout contenu invalide donne un état vide. */
export const webParseGroupState = (raw: string | null | undefined): Record<string, boolean> => {
  if (!raw) return {}
  try {
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    const out: Record<string, boolean> = {}
    for (const [key, open] of Object.entries(value)) if (typeof open === 'boolean') out[key] = open
    return out
  } catch {
    return {}
  }
}

/** Projets ouverts par défaut, « Sans projet » fermé, sauf choix explicite mémorisé. */
export const webGroupOpen = (state: Record<string, boolean>, id: string, isUnassigned: boolean) =>
  typeof state[id] === 'boolean' ? state[id] : !isUnassigned

/**
 * Destinations proposées à « Déplacer vers… » :
 *
 * - Site d'un projet : les autres projets où l'acteur a `app.move`, à condition de l'avoir aussi sur le projet d'origine,
 *   puis « Sans projet » avec `app.detach` ;
 * - Application sans projet : tous les projets avec `app.attach` ;
 * - Preview : aucune.
 */
export const webMoveTargets = (
  groups: readonly GroupLike[],
  app: AppLike,
  globalCapabilities: Capabilities
): WebMoveTarget[] => {
  if (app.deletion || app.membership?.environment === 'preview') return []
  groups = groups.filter(({ project }) => !project.deletion)
  const current = app.access.projectId
  const label = (project: ProjectLike) =>
    `${project.name} · ${webSiteCountLabel(project.siteCount, project.previewCount)}`
  if (!current)
    return globalCapabilities?.['app.attach']
      ? groups.map(({ project }) => ({ projectId: project.id, label: label(project) }))
      : []
  const out: WebMoveTarget[] = app.access.capabilities?.['app.move']
    ? groups
        .filter(({ project }) => project.id !== current && project.capabilities?.['app.move'])
        .map(({ project }) => ({ projectId: project.id, label: label(project) }))
    : []
  if (globalCapabilities?.['app.detach']) out.push({ projectId: null, label: WEB_UNASSIGNED_LABEL })
  return out
}

/** Nom court proposé dans la cible : l'actuel s'il y est libre, sinon dérivé du nom (« vitrine-2 »…) ; '' sans cible. */
export const webMoveDefaultSlug = (app: AppLike & { name: string }, target: { apps: AppLike[] } | null) => {
  if (!target) return ''
  const taken = target.apps.map((item) => item.membership?.slug || '').filter(Boolean)
  const currentSlug = app.membership?.slug || ''
  if (currentSlug && !taken.includes(currentSlug)) return currentSlug
  return webUniqueSlug(webSiteSlug(app.name), taken)
}

const names = (items: readonly string[]) => items.join(', ')
/** Message de succès d'un déplacement (noms de variables uniquement, jamais leurs valeurs). */
export const webMoveSummary = (
  result: Pick<
    WebAppProjectMoveResult<unknown>,
    'droppedVariables' | 'copiedVariables' | 'previewsDisabled' | 'notificationsReset'
  >,
  fromName: string,
  toName: string | null
) => {
  const parts = [toName ? `Site déplacé vers « ${toName} ».` : `Site retiré du projet « ${fromName} ».`]
  const dropped = result.droppedVariables || [],
    copied = result.copiedVariables || []
  if (dropped.length)
    parts.push(
      dropped.length === 1
        ? `1 variable partagée ne s’applique plus : ${names(dropped)}.`
        : `${dropped.length} variables partagées ne s’appliquent plus : ${names(dropped)}.`
    )
  if (copied.length) parts.push(`Variables copiées : ${names(copied)}.`)
  if (result.previewsDisabled)
    parts.push(`Les previews de « ${fromName} » ont été désactivées : ce site en était le modèle.`)
  if (result.notificationsReset) parts.push('Les canaux d’alerte du site ont été réinitialisés.')
  return parts.join(' ')
}
