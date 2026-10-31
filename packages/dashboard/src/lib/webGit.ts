/**
 * Source « Dépôt Git (clé de déploiement) » : analyse d’URL SSH, consignes par hébergeur et client des routes
 * /api/web-git-sources. Module pur (aucun import du client PocketBase) pour rester testable sous vitest ; le composant
 * injecte l’émetteur via webGitApi(send). Le serveur reste seul juge : ces contrôles ne font que guider. Les règles
 * d’adresse et la clé d’unicité (canonical) viennent du module commun, identique à celui du contrôleur.
 */
import {
  canonicalGitRemote as canonicalCommonRemote,
  parseGitRemote as parseCommonRemote,
  webGitDisplayUrl,
} from '../../../pockethost/src/common/webGit'
export type WebGitProvider = 'github' | 'gitlab' | 'gitea' | 'bitbucket' | 'other'
export type WebGitRemote = {
  user: string
  host: string
  port: number
  /** Chemin tel qu’envoyé au serveur Git : relatif (`o/r.git`) ou absolu (`/srv/r.git`), sans « / » final. */
  path: string
  /** Clé d’unicité calculée comme sur le serveur (une clé de déploiement par dépôt). */
  canonical: string
  /** Forme affichable et réutilisable : user@host:path (port 22) ou ssh://user@host:port/path. */
  sshUrl: string
  provider: WebGitProvider
}
export type WebGitHostKey = { type: string; fingerprint: string; preset: boolean }
export type WebGitSource = {
  id: string
  host: string
  port: number
  sshUser: string
  path: string
  canonical: string
  projectId: string
  publicKey: string
  fingerprint: string
  hostKeys: WebGitHostKey[]
  hostKeysConfirmed: boolean
  hostKeysConfirmedAt: string
  /** Un secret de webhook existe (serveur : webhook.configured). */
  webhookConfigured: boolean
  /** Adresse du webhook (serveur : webhook.url), affichable même sans secret. */
  webhookUrl: string
  lastCheckAt: string
  lastCheckStatus: string
  lastError: string
  rotatedAt: string
  /** Nombre d’applications (toutes, y compris celle affichée si elle est enregistrée) qui utilisent cette clé. */
  appCount: number
}
export type WebGitScan = {
  nonce: string
  expiresAt: string
  hostKeys: WebGitHostKey[]
  /** Hôte à empreintes publiées : épinglé côté serveur sans confirmation. */
  preset: boolean
  confirmed: boolean
  source: WebGitSource | null
}
export type WebGitTest = {
  ok: boolean
  checkedAt: string
  error: string
  defaultBranch: string
  source: WebGitSource | null
}
export type WebGitBranch = { name: string; commit: string }
export type WebGitBranches = { items: WebGitBranch[]; defaultBranch: string; truncated: boolean }
/** Toutes les branches en un appel (plafond du serveur = plafond de refs du lecteur) : filtrage local, pas de pages. */
export const WEB_GIT_BRANCHES_PER_PAGE = 5000
export type WebGitKeyRotation = { source: WebGitSource | null; publicKey: string; fingerprint: string }
export type WebGitWebhookSecret = { webhookUrl: string; secret: string; source: WebGitSource | null }
export type WebGitSendOptions = { method?: 'GET' | 'POST' | 'DELETE'; body?: Record<string, unknown> }
export type WebGitSend = <T>(path: string, options?: WebGitSendOptions) => Promise<T>

export const WEB_GIT_SOURCES_API = '/api/web-git-sources'
export const WEB_GIT_REPOSITORY_PREFIX = 'git:'
const DEFAULT_PORT = 22
const HOST = /^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/
const USER = /^[A-Za-z0-9._][A-Za-z0-9._-]{0,63}$/
const SEGMENT = /^[A-Za-z0-9._~][A-Za-z0-9._~-]{0,127}$/
const SSH_FORM = /^ssh:\/\/(?:([^@/]+)@)?([^/:@]+)(?::(\d{1,5}))?\/(.*)$/i
const SCP_FORM = /^([^@/:]+)@([^/:@]+):(.*)$/

export const webGitError = (message: string) => new Error(message)

/** Accepte git@hôte:chemin et ssh://user@hôte[:port]/chemin ; refuse tout autre transport (https, file:, ext::…). */
export const parseGitRemote = (input: string): WebGitRemote => {
  const value = String(input ?? '').trim()
  if (!value) throw webGitError('Collez l’adresse SSH du dépôt.')
  if (value.length > 500 || /[\u0000-\u001f\u007f\s]/.test(value))
    throw webGitError('L’adresse contient des caractères non autorisés.')
  if (value.startsWith('-')) throw webGitError('L’adresse ne peut pas commencer par un tiret.')
  if (
    value.includes('::') ||
    /^file:/i.test(value) ||
    (/^[a-z][a-z0-9+.-]*:\/\//i.test(value) && !/^ssh:\/\//i.test(value))
  )
    throw webGitError('Utilisez une adresse SSH : git@hôte:organisation/dépôt.git ou ssh://git@hôte/dépôt.git.')
  const ssh = SSH_FORM.exec(value)
  const scp = ssh ? null : SCP_FORM.exec(value)
  if (!ssh && !scp)
    throw webGitError('Utilisez une adresse SSH : git@hôte:organisation/dépôt.git ou ssh://git@hôte/dépôt.git.')
  const user = (ssh ? ssh[1] : scp![1]) ?? 'git'
  const host = (ssh ? ssh[2] : scp![2])!.toLowerCase()
  const rawPort = ssh?.[3]
  const port = rawPort === undefined ? DEFAULT_PORT : Number(rawPort)
  if (!USER.test(user)) throw webGitError('Utilisateur SSH invalide.')
  if (!HOST.test(host)) throw webGitError('Nom d’hôte invalide.')
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw webGitError('Port SSH invalide (1 à 65535).')
  const path = (ssh ? ssh[4] : scp![3])!.replace(/^\/+/, '').replace(/\/+$/, '')
  const segments = path.split('/')
  if (
    !path ||
    path.length > 300 ||
    path.includes('..') ||
    segments.some((segment) => !SEGMENT.test(segment) || /^\.+$/.test(segment))
  )
    throw webGitError('Chemin du dépôt invalide.')
  const bare = path.replace(/\.git$/i, '')
  if (!bare) throw webGitError('Chemin du dépôt invalide.')
  // Règles finales du serveur (IP littérales, noms locaux…) ; le chemin garde son « / » initial : sur un sshd classique,
  // `hôte:/srv/r.git` (absolu) et `hôte:srv/r.git` (relatif au dossier de l’utilisateur) sont deux dépôts différents.
  let remote: ReturnType<typeof parseCommonRemote>
  try {
    remote = parseCommonRemote(value)
  } catch (e) {
    throw webGitError(e instanceof Error ? e.message : 'Adresse SSH du dépôt invalide.')
  }
  return {
    user: remote.sshUser,
    host: remote.host,
    port: remote.port,
    path: remote.path,
    canonical: remote.canonical,
    sshUrl: webGitDisplayUrl(remote),
    provider: webGitProvider(remote.host),
  }
}
/** Même clé que le serveur : utilisateur si ≠ git, chemin absolu distinct du relatif, casse ignorée sur les forges. */
export const canonicalGitRemote = (remote: { host: string; port?: number; sshUser?: string; path: string }) =>
  canonicalCommonRemote({
    host: remote.host,
    port: remote.port || DEFAULT_PORT,
    sshUser: remote.sshUser,
    path: remote.path.replace(/(.)\/+$/, '$1'),
  })
/** Valeur de web_apps.config.repository pour une source Git. */
export const webGitRepository = (canonical: string) => WEB_GIT_REPOSITORY_PREFIX + canonical
export const isWebGitRepository = (repository?: string) => !!repository?.startsWith(WEB_GIT_REPOSITORY_PREFIX)
export const webGitRepositoryLabel = (repository?: string) =>
  isWebGitRepository(repository) ? repository!.slice(WEB_GIT_REPOSITORY_PREFIX.length) : repository || ''

/** Déduction depuis l’hôte ; un hôte auto-hébergé reste « other » sauf indice explicite dans son nom. */
export const webGitProvider = (host: string): WebGitProvider => {
  const value = host.toLowerCase()
  if (value === 'github.com' || value === 'ssh.github.com') return 'github'
  if (value === 'gitlab.com' || /^gitlab\./.test(value)) return 'gitlab'
  if (value === 'bitbucket.org') return 'bitbucket'
  if (['codeberg.org', 'gitea.com'].includes(value) || /^(gitea|forgejo)\./.test(value)) return 'gitea'
  return 'other'
}
export const WEB_GIT_PROVIDER_LABELS: Record<WebGitProvider, string> = {
  github: 'GitHub',
  gitlab: 'GitLab',
  gitea: 'Gitea / Forgejo',
  bitbucket: 'Bitbucket',
  other: 'Autre serveur Git',
}
/** Hôtes dont les empreintes publiées sont épinglées par le serveur (pas de confirmation manuelle). */
export const WEB_GIT_PRESET_HOSTS = ['github.com', 'gitlab.com', 'bitbucket.org'] as const
export const isWebGitPresetHost = (host: string, port = DEFAULT_PORT) =>
  port === DEFAULT_PORT && (WEB_GIT_PRESET_HOSTS as readonly string[]).includes(host.toLowerCase())

const webBase = (remote: Pick<WebGitRemote, 'host' | 'path'>) =>
  `https://${remote.host}/${remote.path
    .replace(/^\/+/, '')
    .replace(/\.git$/i, '')
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`
/** Page de l’hébergeur où ajouter la clé publique (null si l’interface web est inconnue). */
export const webGitDeployKeysUrl = (
  remote: Pick<WebGitRemote, 'host' | 'path'>,
  provider = webGitProvider(remote.host)
) =>
  provider === 'github' || provider === 'gitea'
    ? `${webBase(remote)}/settings/keys`
    : provider === 'gitlab'
      ? `${webBase(remote)}/-/settings/repository#js-deploy-keys-settings`
      : provider === 'bitbucket'
        ? `${webBase(remote)}/admin/access-keys/`
        : null
export const webGitWebhookSettingsUrl = (
  remote: Pick<WebGitRemote, 'host' | 'path'>,
  provider = webGitProvider(remote.host)
) =>
  provider === 'github'
    ? `${webBase(remote)}/settings/hooks/new`
    : provider === 'gitea'
      ? `${webBase(remote)}/settings/hooks`
      : provider === 'gitlab'
        ? `${webBase(remote)}/-/hooks`
        : provider === 'bitbucket'
          ? `${webBase(remote)}/admin/webhooks`
          : null
export const webGitRepositoryUrl = (
  remote: Pick<WebGitRemote, 'host' | 'path'>,
  provider = webGitProvider(remote.host)
) => (provider === 'other' ? null : webBase(remote))

/** Une ligne par hébergeur : où coller la clé, toujours sans droit d’écriture. */
export const WEB_GIT_DEPLOY_KEY_STEPS: Record<WebGitProvider, string> = {
  github: 'GitHub : Settings → Deploy keys → Add deploy key, laissez « Allow write access » décoché (lecture seule).',
  gitlab:
    'GitLab : Settings → Repository → Deploy keys → Add new key, sans « Grant write permissions » (lecture seule).',
  gitea:
    'Gitea / Forgejo : Paramètres du dépôt → Clés de déploiement → Ajouter une clé, sans accès en écriture (lecture seule).',
  bitbucket: 'Bitbucket : Repository settings → Access keys → Add key (accès en lecture seule).',
  other:
    'Serveur Git : ajoutez la clé à l’utilisateur SSH du dépôt, en lecture seule (par exemple dans authorized_keys avec git-upload-pack uniquement).',
}
export type WebGitWebhookGuide = { supported: boolean; steps: string[] }
/** Consignes webhook par hébergeur ; toutes les signatures citées sont vérifiées par le contrôleur. */
export const webGitWebhookGuide = (provider: WebGitProvider): WebGitWebhookGuide =>
  provider === 'github'
    ? {
        supported: true,
        steps: [
          'Settings → Webhooks → Add webhook.',
          'Payload URL : l’adresse ci-dessus · Content type : application/json · Secret : le secret ci-dessus.',
          'Événements : « Just the push event ».',
        ],
      }
    : provider === 'gitlab'
      ? {
          supported: true,
          steps: [
            'Settings → Webhooks → Add new webhook.',
            'URL : l’adresse ci-dessus · Secret token : le secret ci-dessus.',
            'Déclencheur : « Push events » uniquement.',
          ],
        }
      : provider === 'gitea'
        ? {
            supported: true,
            steps: [
              'Paramètres du dépôt → Webhooks → Ajouter un webhook → Gitea (ou Forgejo).',
              'URL cible : l’adresse ci-dessus · POST · application/json · Secret : le secret ci-dessus.',
              'Événements : « Push » uniquement.',
            ],
          }
        : provider === 'bitbucket'
          ? {
              supported: true,
              steps: [
                'Repository settings → Webhooks → Add webhook.',
                'URL : l’adresse ci-dessus · Secret : le secret ci-dessus (signature X-Hub-Signature).',
                'Triggers : « Repository push » uniquement. Un push groupant plusieurs branches ne déploie que la première.',
              ],
            }
          : {
              supported: true,
              steps: [
                'Envoyez un POST JSON sur l’adresse ci-dessus à chaque push.',
                'Signature acceptée : X-Hub-Signature-256, X-Hub-Signature (sha256=…) ou X-Gitea-Signature (HMAC-SHA256 du corps avec le secret), ou X-Gitlab-Token égal au secret.',
              ],
            }

/** Libellé court d’un type de clé hôte (ssh-ed25519 → Ed25519). */
export const webGitKeyType = (type: string) =>
  type === 'ssh-ed25519'
    ? 'Ed25519'
    : type.startsWith('ecdsa-sha2-')
      ? 'ECDSA'
      : type === 'ssh-rsa' || type.startsWith('rsa-sha2-')
        ? 'RSA'
        : type || 'Inconnu'
/** Empreinte au format OpenSSH « SHA256:… » ; chaîne vide si la valeur ne ressemble pas à une empreinte SHA-256. */
export const webGitFingerprint = (value?: string) => {
  const text = String(value ?? '').trim()
  const body = text.replace(/^SHA256:/i, '')
  return /^[A-Za-z0-9+/]{43}$/.test(body) ? 'SHA256:' + body : ''
}
export const webGitSameFingerprints = (left: string[], right: string[]) => {
  const a = [...new Set(left.map(webGitFingerprint))].sort()
  const b = [...new Set(right.map(webGitFingerprint))].sort()
  return a.length > 0 && !a.includes('') && a.length === b.length && a.every((value, index) => value === b[index])
}
export const webGitBranchValid = (name: string) =>
  /^[A-Za-z0-9_./-]{1,200}$/.test(name) &&
  !name.includes('..') &&
  !name.startsWith('-') &&
  !name.startsWith('/') &&
  !name.endsWith('/') &&
  !name.endsWith('.lock')
/** Ligne de clé publique OpenSSH à coller chez l’hébergeur (une seule ligne, type ssh-ed25519). */
export const webGitPublicKeyValid = (value?: string) =>
  /^ssh-ed25519 [A-Za-z0-9+/]{68}(?: [\x21-\x7e][\x20-\x7e]{0,99})?$/.test(String(value ?? '').trim())

type Raw = Record<string, unknown>
const record = (value: unknown): Raw => (value && typeof value === 'object' ? (value as Raw) : {})
const text = (value: unknown) => (typeof value === 'string' ? value : '')
const hostKeys = (value: unknown): WebGitHostKey[] =>
  (Array.isArray(value) ? value : []).map(record).flatMap((item) => {
    const fingerprint = webGitFingerprint(text(item.fingerprint))
    return fingerprint ? [{ type: text(item.type), fingerprint, preset: item.preset === true }] : []
  })
/** Normalise une source renvoyée par l’API ({source} ou objet nu). Aucun champ secret n’est conservé. */
export const webGitSource = (value: unknown): WebGitSource | null => {
  const outer = record(value)
  const raw = outer.source && typeof outer.source === 'object' ? record(outer.source) : outer
  const id = text(raw.id)
  if (!/^[a-z0-9]{15}$/.test(id)) return null
  const port = Number(raw.port) || DEFAULT_PORT
  const keys = hostKeys(raw.hostKeys)
  const webhook = record(raw.webhook)
  const count = Number(raw.appCount)
  return {
    id,
    host: text(raw.host).toLowerCase(),
    port,
    sshUser: text(raw.sshUser) || 'git',
    path: text(raw.path),
    canonical:
      text(raw.canonical) ||
      canonicalGitRemote({ host: text(raw.host), port, sshUser: text(raw.sshUser) || 'git', path: text(raw.path) }),
    projectId: text(raw.projectId),
    publicKey: text(raw.publicKey).trim(),
    fingerprint: webGitFingerprint(text(raw.fingerprint)),
    hostKeys: keys,
    hostKeysConfirmed: raw.hostKeysConfirmed === true || (!!text(raw.hostKeysConfirmedAt) && keys.length > 0),
    hostKeysConfirmedAt: text(raw.hostKeysConfirmedAt),
    webhookConfigured: raw.webhookConfigured === true || webhook.configured === true,
    webhookUrl: text(raw.webhookUrl) || text(webhook.url),
    lastCheckAt: text(raw.lastCheckAt),
    lastCheckStatus: text(raw.lastCheckStatus),
    lastError: text(raw.lastError),
    rotatedAt: text(raw.rotatedAt),
    appCount: Number.isInteger(count) && count > 0 ? count : 0,
  }
}
/** Remote affichable d’une source (même forme que parseGitRemote). */
export const webGitSourceRemote = (source: WebGitSource): WebGitRemote => ({
  user: source.sshUser,
  host: source.host,
  port: source.port,
  path: source.path,
  canonical: source.canonical,
  sshUrl: webGitDisplayUrl(source),
  provider: webGitProvider(source.host),
})
/** Sans adresse fournie par le serveur, le webhook vit sur l’hôte de l’API. */
export const webGitWebhookUrl = (baseUrl: string, webhookId: string) => {
  if (!/^[A-Za-z0-9]{24}$/.test(webhookId)) return ''
  return `${baseUrl.replace(/\/+$/, '')}${WEB_GIT_SOURCES_API}/webhook/${webhookId}`
}

const sourcePath = (sourceId: string, action = '') => {
  if (!/^[a-z0-9]{15}$/.test(sourceId)) throw webGitError('Source Git invalide.')
  return `${WEB_GIT_SOURCES_API}/${sourceId}${action ? '/' + action : ''}`
}
const required = (source: WebGitSource | null) => {
  if (!source) throw webGitError('Réponse inattendue du serveur pour la source Git.')
  return source
}
/** Client des routes prévues ; `send` reçoit le chemin absolu et {method, body}. */
export const webGitApi = (send: WebGitSend) => ({
  create: async (url: string, projectId = '') => {
    // Pré-contrôle local puis adresse saisie (rognée) : le serveur la ré-analyse et reste seul juge.
    parseGitRemote(url)
    return required(
      webGitSource(
        await send<unknown>(WEB_GIT_SOURCES_API, {
          method: 'POST',
          body: { url: url.trim(), ...(projectId ? { projectId } : {}) },
        })
      )
    )
  },
  get: async (sourceId: string) => required(webGitSource(await send<unknown>(sourcePath(sourceId)))),
  scan: async (sourceId: string): Promise<WebGitScan> => {
    const raw = record(await send<unknown>(sourcePath(sourceId, 'scan'), { method: 'POST', body: {} }))
    return {
      nonce: text(raw.nonce),
      expiresAt: text(raw.expiresAt),
      hostKeys: hostKeys(raw.hostKeys),
      preset: raw.preset === true,
      // Hôte publié : le serveur vérifie la concordance et épingle lui-même ({preset, matches}), sans nonce.
      confirmed: raw.confirmed === true || (raw.preset === true && raw.matches === true),
      source: webGitSource(raw.source),
    }
  },
  confirmHostKeys: async (sourceId: string, nonce: string, fingerprints: string[]) =>
    required(
      webGitSource(
        await send<unknown>(sourcePath(sourceId, 'confirm-host-keys'), {
          method: 'POST',
          body: { nonce, fingerprints: fingerprints.map(webGitFingerprint) },
        })
      )
    ),
  test: async (sourceId: string): Promise<WebGitTest> => {
    const raw = record(await send<unknown>(sourcePath(sourceId, 'test'), { method: 'POST', body: {} }))
    return {
      ok: raw.ok === true,
      checkedAt: text(raw.checkedAt),
      error: text(raw.error),
      defaultBranch: text(raw.defaultBranch),
      source: webGitSource(raw.source),
    }
  },
  branches: async (sourceId: string): Promise<WebGitBranches> => {
    const raw = record(await send<unknown>(`${sourcePath(sourceId, 'branches')}?perPage=${WEB_GIT_BRANCHES_PER_PAGE}`))
    const items = (Array.isArray(raw.items) ? raw.items : [])
      .map(record)
      .map((item) => ({ name: text(item.name), commit: text(item.commit) }))
      .filter((item) => webGitBranchValid(item.name))
    return {
      items,
      defaultBranch: text(raw.defaultBranch),
      truncated: raw.truncated === true || raw.hasMore === true,
    }
  },
  rotateKey: async (sourceId: string): Promise<WebGitKeyRotation> => {
    const raw = record(
      await send<unknown>(sourcePath(sourceId, 'rotate-key'), { method: 'POST', body: { confirm: true } })
    )
    const source = webGitSource(raw.source)
    return {
      source,
      publicKey: text(raw.publicKey).trim() || source?.publicKey || '',
      fingerprint: webGitFingerprint(text(raw.fingerprint)) || source?.fingerprint || '',
    }
  },
  rotateWebhookSecret: async (sourceId: string): Promise<WebGitWebhookSecret> => {
    const raw = record(await send<unknown>(sourcePath(sourceId, 'rotate-webhook-secret'), { method: 'POST', body: {} }))
    const source = webGitSource(raw.source)
    return {
      webhookUrl: text(raw.url) || text(raw.webhookUrl) || source?.webhookUrl || '',
      secret: text(raw.secret),
      source,
    }
  },
  remove: (sourceId: string) => send<unknown>(sourcePath(sourceId), { method: 'DELETE' }),
})
export type WebGitApi = ReturnType<typeof webGitApi>
