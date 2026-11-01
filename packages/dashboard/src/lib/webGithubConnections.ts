/**
 * Applications web — plusieurs connexions GitHub App par serveur (logique d'affichage pure, testable sans PocketBase).
 * Le serveur reste seul juge : ces fonctions ne font que préparer les requêtes et les libellés.
 */
import type { WebGitHubConnectionSummary, WebGitHubHealth } from './webApps'

/** Connexion principale : la ligne `github` historique, servie par l'URL de webhook legacy. */
export const WEB_GITHUB_PRINCIPAL = 'default'
export const WEB_GITHUB_CONNECTION_ID = /^(default|[a-z0-9]{15})$/
export const WEB_GITHUB_MAX_CONNECTIONS = 10
export const WEB_GITHUB_LABEL_MAX = 60

export const webGithubConnectionIdValid = (id: unknown): id is string =>
  typeof id === 'string' && WEB_GITHUB_CONNECTION_ID.test(id)

/** Connexion résolue d'une application : '' pour un Dépôt Git, la principale quand la clé est absente. */
export const webAppGithubConnectionId = (app: { source?: string; githubConnectionId?: string } | null | undefined) =>
  !app || app.source === 'git' ? '' : app.githubConnectionId || WEB_GITHUB_PRINCIPAL

/**
 * Paramètre `connectionId` des routes `/repositories`, `/branches` et `/github/health`. Omis pour la principale : sans
 * paramètre, le serveur utilise la connexion principale (et un serveur plus ancien ignore le paramètre).
 */
export const webGithubConnectionQuery = (id: string | null | undefined): { connectionId?: string } =>
  id && id !== WEB_GITHUB_PRINCIPAL && webGithubConnectionIdValid(id) ? { connectionId: id } : {}

/** Valeur envoyée dans la config d'une application ; 'default' est normalisé en absence par le serveur. */
export const webGithubConnectionPayload = (id: string | null | undefined) =>
  id && webGithubConnectionIdValid(id) ? id : WEB_GITHUB_PRINCIPAL

/** Connexions utilisables pour rattacher une application : configurées et activées. */
export const webGithubActiveConnections = (items: WebGitHubConnectionSummary[] | null | undefined) =>
  (items || []).filter((item) => item.configured && !item.disabled)

/**
 * Sélection par défaut : la connexion courante si elle reste active, sinon la principale active, sinon la première
 * connexion active, sinon la principale (comportement historique).
 */
export const webGithubDefaultSelection = (
  items: WebGitHubConnectionSummary[] | null | undefined,
  current = ''
): string => {
  const active = webGithubActiveConnections(items)
  if (current && active.some((item) => item.id === current)) return current
  return (
    active.find((item) => item.isPrincipal || item.id === WEB_GITHUB_PRINCIPAL)?.id ||
    active[0]?.id ||
    WEB_GITHUB_PRINCIPAL
  )
}

/** Libellé affiché : celui choisi par l'administrateur, sinon un libellé déduit sans secret. */
export const webGithubConnectionLabel = (
  connection: Pick<WebGitHubConnectionSummary, 'id' | 'label' | 'isPrincipal' | 'app'> | null | undefined
) => {
  if (!connection) return 'Connexion GitHub'
  const label = (connection.label || '').trim()
  if (label) return label
  if (connection.isPrincipal || connection.id === WEB_GITHUB_PRINCIPAL) return 'Connexion principale'
  return connection.app?.owner || connection.app?.slug || 'Connexion GitHub'
}

/** Option d'un sélecteur : libellé, puis compte GitHub quand il apporte une information. */
export const webGithubConnectionOption = (connection: WebGitHubConnectionSummary) => {
  const label = webGithubConnectionLabel(connection)
  const owner = connection.app?.owner || ''
  return `${label}${owner && owner !== label ? ` · ${owner}` : ''}${connection.isPrincipal ? ' (principale)' : ''}`
}

/** Validation locale du libellé (le serveur applique la même règle : 1 à 60 caractères, sans contrôle). */
export const webGithubLabelError = (value: string) => {
  const label = value.trim()
  if (!label) return 'Indiquez un libellé.'
  // Même mesure que le serveur (unités UTF-16 de la chaîne JavaScript, après trim).
  if (label.length > WEB_GITHUB_LABEL_MAX) return `Le libellé ne doit pas dépasser ${WEB_GITHUB_LABEL_MAX} caractères.`
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(label))
    return 'Le libellé ne doit pas contenir de retour à la ligne.'
  return ''
}

export type WebGithubConnectionTone = 'success' | 'warning' | 'neutral' | 'danger' | 'working'
/** Badge de santé d'une carte : « Désactivée », « Opérationnelle » ou « À vérifier ». */
export const webGithubConnectionStatus = (
  connection: Pick<WebGitHubConnectionSummary, 'disabled' | 'configured'>,
  health: Pick<WebGitHubHealth, 'ok' | 'configured' | 'webhook'> | null | undefined,
  checking = false
): { label: string; tone: WebGithubConnectionTone } => {
  if (connection.disabled) return { label: 'Désactivée', tone: 'neutral' }
  if (!connection.configured) return { label: 'À terminer', tone: 'warning' }
  if (!health) return checking ? { label: 'Vérification…', tone: 'working' } : { label: 'À vérifier', tone: 'warning' }
  if (health.configured && health.ok && !health.webhook?.urlMismatch)
    return { label: 'Opérationnelle', tone: 'success' }
  return { label: 'À vérifier', tone: 'warning' }
}

export const webGithubAppCountLabel = (count: number | null | undefined) => {
  const value = Math.max(0, Math.trunc(Number(count) || 0))
  if (!value) return 'Aucune application rattachée'
  return `Utilisée par ${value.toLocaleString('fr-FR')} application${value > 1 ? 's' : ''}`
}

const deliveryStatus: Record<string, string> = {
  queued: 'déployée',
  ignored: 'ignorée',
  failed: 'en échec',
  duplicate: 'doublon',
}
/** « il y a 3 min (déployée) » ; « Aucune livraison reçue » sans livraison. */
export const webGithubDeliveryLabel = (
  delivery: { at?: string; status?: string } | null | undefined,
  now: number = Date.now()
) => {
  const at = delivery?.at ? Date.parse(delivery.at) : NaN
  if (!delivery || !Number.isFinite(at)) return 'Aucune livraison reçue'
  const seconds = Math.max(0, Math.round((now - at) / 1000))
  const relative =
    seconds < 60
      ? 'à l’instant'
      : seconds < 3600
        ? `il y a ${Math.floor(seconds / 60)} min`
        : seconds < 86400
          ? `il y a ${Math.floor(seconds / 3600)} h`
          : `il y a ${Math.floor(seconds / 86400)} j`
  const status = delivery.status ? deliveryStatus[delivery.status] || delivery.status : ''
  return status ? `${relative} (${status})` : relative
}

export const webGithubConnectionsHeading = (count: number, max = WEB_GITHUB_MAX_CONNECTIONS) =>
  `Connexions GitHub (${Math.max(0, count)}/${max})`

/** Ancre d'une carte (`/applications/settings#connection-{id}`) ; vide pour un id non reconnu. */
export const webGithubConnectionAnchor = (id: string | null | undefined) =>
  webGithubConnectionIdValid(id) ? `connection-${id}` : ''
export const webGithubConnectionHref = (id: string | null | undefined) => {
  const anchor = webGithubConnectionAnchor(id)
  return `/applications/settings${anchor ? `#${anchor}` : ''}`
}

/** Les routes de création/modification renvoient `{connection}` ; accepte aussi `{item}` ou l'objet seul. */
export const webGithubConnectionFromResponse = (value: unknown): WebGitHubConnectionSummary | null => {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  for (const candidate of [record.connection, record.item, record]) {
    if (candidate && typeof candidate === 'object' && webGithubConnectionIdValid((candidate as { id?: unknown }).id))
      return candidate as WebGitHubConnectionSummary
  }
  return null
}

/** Message de fin de l'assistant (callback GitHub). */
export const webGithubCompletionMessage = (value: {
  label?: string
  account?: string
  mode?: string
  repositoryCount?: number
}) => {
  const name = (value.label || value.account || '').trim()
  const subject = name ? `Connexion « ${name} »` : 'Connexion GitHub'
  const count = Math.max(0, Math.trunc(Number(value.repositoryCount) || 0))
  const repositories = `${count.toLocaleString('fr-FR')} dépôt${count > 1 ? 's' : ''} autorisé${count > 1 ? 's' : ''}.`
  return value.mode === 'replace' ? `${subject} mise à jour. ${repositories}` : `${subject} ajoutée. ${repositories}`
}

/** Corps de `POST /github/connect/start` : ajout par défaut, remplacement seulement si demandé explicitement. */
export const webGithubConnectStartBody = (input: {
  mode?: 'add' | 'replace'
  connectionId?: string
  organization?: string
  label?: string
}) => {
  const organization = (input.organization || '').trim()
  const label = (input.label || '').trim()
  const body: Record<string, string> = { mode: input.mode === 'replace' ? 'replace' : 'add' }
  if (organization) body.organization = organization
  if (label) body.label = label
  if (body.mode === 'replace') {
    if (!webGithubConnectionIdValid(input.connectionId)) throw new Error('Connexion GitHub à recréer introuvable.')
    body.connectionId = input.connectionId
  }
  return body
}
