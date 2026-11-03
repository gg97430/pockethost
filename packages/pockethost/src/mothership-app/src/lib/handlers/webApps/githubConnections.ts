/**
 * Plusieurs connexions GitHub App par serveur. Une connexion = une GitHub App installée sur un compte ou une
 * organisation, stockée chiffrée dans `web_app_settings` :
 *
 * - `key = "github"` : connexion principale (`default`), ligne historique intacte, webhook legacy
 *   `/api/web-apps/github/webhook` ;
 * - `key = "github:<15 car.>"` : connexions supplémentaires, webhook `/api/web-apps/github/webhook/<id>`.
 *
 * Le blob chiffré garde le format historique et reçoit `meta` ({label, disabled, createdAt, createdBy}) ; l'ancien code
 * ne lit que des champs nommés. `web_apps.config.githubConnectionId` absent = connexion principale.
 *
 * Aucune réponse ne contient `privateKey`, `webhookSecret` ni `valueEncrypted`.
 */
import { requirePlatformAdmin } from './access'
import {
  decryptWebValue,
  encryptWebValue,
  queueWebGitPush,
  webConfig,
  webEnabled,
  webGithub,
  webInput,
  webJson,
  webSourceBuildSupported,
} from './api'
import { handleWebPreviewWebhook } from './previews'

export const GITHUB_PRINCIPAL = 'default'
/** Identifiant de connexion accepté partout (principale ou supplémentaire). */
export const GITHUB_CONNECTION_ID = /^(default|[a-z0-9]{15})$/
/** Identifiant d'une connexion supplémentaire (chemin `/webhook/{connectionId}`). */
export const GITHUB_EXTRA_CONNECTION_ID = /^[a-z0-9]{15}$/
export const GITHUB_CONNECTION_MAX = 10
export const GITHUB_PRINCIPAL_LABEL = 'Connexion principale'
const SETTINGS = 'web_app_settings'
const SESSIONS = 'web_github_setup_sessions'
const KEY = /^github(:[a-z0-9]{15})?$/
const UNAVAILABLE = 'Connexion GitHub introuvable ou désactivée.'
const DUPLICATE = 'Cette GitHub App est déjà connectée.'

type RepositoryWebhook = { repository: string; repositoryId: string; hookId: string }
export type WebGithubConnectionMeta = {
  label: string
  disabled: boolean
  createdAt: string
  createdBy: string
  repositoryWebhook?: RepositoryWebhook
}
/** Repository hooks have no installation in their payload. Opt in for one exact repository and hook only. */
const repositoryWebhookConfig = (input: any): RepositoryWebhook | null => {
  if (input === null) return null
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).sort().join(',') !== 'hookId,repository,repositoryId' ||
    typeof input.repository !== 'string' ||
    !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(input.repository) ||
    input.repository.includes('..') ||
    input.repository.length > 250 ||
    !/^[1-9][0-9]{0,19}$/.test(`${input.repositoryId}`) ||
    !/^[1-9][0-9]{0,19}$/.test(`${input.hookId}`)
  )
    throw new BadRequestError('Webhook de dépôt : nom, identifiant du dépôt et identifiant du webhook obligatoires.')
  return { repository: input.repository, repositoryId: `${input.repositoryId}`, hookId: `${input.hookId}` }
}
export type WebGithubConnection = {
  id: string
  key: string
  row: core.Record | null
  value: Record<string, any>
  meta: WebGithubConnectionMeta
}

/** `default` -> `github`, `<id>` -> `github:<id>`. Lève une 400 pour tout autre identifiant. */
export const githubSettingsKey = (id: string) => {
  if (!GITHUB_CONNECTION_ID.test(`${id}`)) throw new BadRequestError('Connexion GitHub invalide.')
  return id === GITHUB_PRINCIPAL ? 'github' : `github:${id}`
}
/** Clé `web_app_settings` -> identifiant de connexion ('' si la clé n'est pas une connexion GitHub). */
export const githubConnectionIdOfKey = (key: string) =>
  !KEY.test(`${key}`) ? '' : key === 'github' ? GITHUB_PRINCIPAL : key.slice('github:'.length)
/** Connexion utilisée par une configuration d'application : '' pour une source « Dépôt Git ». */
export const resolvedConnectionId = (config: any) =>
  config?.source === 'git'
    ? ''
    : (typeof config?.githubConnectionId === 'string' && config.githubConnectionId) || GITHUB_PRINCIPAL
export const githubConnectionConfigured = (value: any) =>
  !!(value?.githubAppId && value?.githubInstallationId && value?.privateKey && value?.webhookSecret)
/** Métadonnées lues (la ligne principale historique n'a pas de `meta`). */
export const githubConnectionMeta = (id: string, value: any): WebGithubConnectionMeta => {
  const meta = value?.meta && typeof value.meta === 'object' ? value.meta : {}
  const label =
    typeof meta.label === 'string' && meta.label
      ? meta.label
      : id === GITHUB_PRINCIPAL
        ? GITHUB_PRINCIPAL_LABEL
        : `${value?.ownerLogin || value?.githubAppSlug || 'Connexion GitHub'}`
  return {
    label: label.slice(0, 60),
    disabled: meta.disabled === true,
    createdAt: typeof meta.createdAt === 'string' ? meta.createdAt : '',
    createdBy: typeof meta.createdBy === 'string' ? meta.createdBy : '',
    ...(meta.repositoryWebhook !== undefined && meta.repositoryWebhook !== null
      ? { repositoryWebhook: repositoryWebhookConfig(meta.repositoryWebhook)! }
      : {}),
  }
}
/** Libellé saisi : 1 à 60 caractères, sans caractère de contrôle ni retour à la ligne. */
export const githubConnectionLabel = (value: unknown) => {
  const label = typeof value === 'string' ? value.trim() : ''
  if (
    typeof value !== 'string' ||
    !label ||
    label.length > 60 ||
    /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(value)
  )
    throw new BadRequestError('Libellé de connexion : 1 à 60 caractères, sans retour à la ligne.')
  return label
}
/** URL du webhook de la connexion (legacy pour la principale). */
export const githubWebhookUrl = (id: string) => {
  const host = $os.getenv('APP_HOST') || `app.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`
  return `https://${host}/api/web-apps/github/webhook${id === GITHUB_PRINCIPAL ? '' : `/${id}`}`
}
/** Seuls champs transmis au lecteur GitHub : ni `meta` ni `webhookSecret`. */
export const githubReaderConfig = (value: any) => {
  const config: Record<string, string> = {}
  for (const name of ['githubAppId', 'githubAppSlug', 'githubInstallationId', 'privateKey', 'ownerId', 'ownerLogin'])
    if (value?.[name] !== undefined && value[name] !== null && value[name] !== '') config[name] = `${value[name]}`
  return config
}

const connectionOf = (row: core.Record): WebGithubConnection => {
  const key = row.getString('key'),
    id = githubConnectionIdOfKey(key),
    value = decryptWebValue(row.getString('valueEncrypted')) || {}
  return { id, key, row, value, meta: githubConnectionMeta(id, value) }
}
/** Ligne d'une connexion, ou null. Filtre toujours paramétré. */
export const githubConnectionRow = (id: string, store: any = $app): core.Record | null => {
  if (!GITHUB_CONNECTION_ID.test(`${id}`)) return null
  try {
    return store.findFirstRecordByFilter(SETTINGS, 'key = {:key}', { key: githubSettingsKey(id) }) as core.Record
  } catch {
    return null
  }
}
/** Toutes les connexions, déchiffrées, dans l'ordre de création. */
export const listWebGithubConnections = (store: any = $app): WebGithubConnection[] =>
  (store.findRecordsByFilter(SETTINGS, '', 'created', 50, 0) as core.Record[])
    .filter((row) => KEY.test(row.getString('key')))
    .map(connectionOf)
/**
 * Connexion demandée. `allowMissing` : la principale absente donne une configuration vide (le lecteur répond
 * `configured:false`, comme avant). Id explicite inconnu, ou connexion désactivée sans `allowDisabled` : 400.
 */
export const webGithubConnection = (
  id: string = GITHUB_PRINCIPAL,
  store: any = $app,
  opts: { allowDisabled?: boolean; allowMissing?: boolean } = {}
): WebGithubConnection => {
  const target = `${id || GITHUB_PRINCIPAL}`
  if (!GITHUB_CONNECTION_ID.test(target)) throw new BadRequestError(UNAVAILABLE)
  const row = githubConnectionRow(target, store)
  if (!row) {
    if (target === GITHUB_PRINCIPAL && opts.allowMissing)
      return { id: target, key: 'github', row: null, value: {}, meta: githubConnectionMeta(target, {}) }
    throw new BadRequestError(UNAVAILABLE)
  }
  const connection = connectionOf(row)
  if (connection.meta.disabled && !opts.allowDisabled) throw new BadRequestError(UNAVAILABLE)
  return connection
}
/** Capacités publiques : un nombre seulement (jamais de libellé, visibles des membres de projet). */
export const webGithubCapability = (store: any = $app) => {
  let connections: WebGithubConnection[] = []
  try {
    connections = listWebGithubConnections(store)
  } catch {}
  const usable = connections.filter((c) => !c.meta.disabled && githubConnectionConfigured(c.value))
  return { githubConfigured: usable.length > 0, githubConnectionCount: usable.length }
}
/**
 * Une application (ou un instantané de job) utilise la connexion `id`. Les applications implicites comptent pour
 * `default`.
 */
export const githubConnectionUses = (config: any, id: string) =>
  !!config && config.source !== 'git' && !!`${config.repository || ''}` && resolvedConnectionId(config) === id
/** Parcourt TOUTES les lignes (pages de 500, ordre stable par id) : aucun plafond fixe ne fausse un décompte. */
export const eachWebRecord = (
  store: any,
  collection: string,
  filter: string,
  visit: (row: core.Record) => void,
  params: Record<string, unknown> = {}
) => {
  for (let offset = 0; ; offset += 500) {
    const rows = store.findRecordsByFilter(collection, filter, 'id', 500, offset, params) as core.Record[]
    for (const row of rows) visit(row)
    if (rows.length < 500) return
  }
}
/** Applications et jobs en file ou en cours rattachés à la connexion. */
export const githubConnectionUsage = (id: string, store: any = $app) => {
  const apps = new Set<string>()
  eachWebRecord(store, 'web_apps', '', (row) => {
    if (githubConnectionUses(webConfig(row), id)) apps.add(row.id)
  })
  let jobCount = 0
  eachWebRecord(store, 'web_deployments', 'status = "queued" || status = "running"', (job) => {
    let snapshot: any = {}
    try {
      snapshot = decryptWebValue(job.getString('snapshotEncrypted')) || {}
    } catch {}
    if (githubConnectionUses(snapshot.config, id)) {
      jobCount++
      apps.add(job.getString('appId'))
    }
  })
  return { appCount: apps.size, jobCount }
}
/** Unicité de l'App et de l'installation entre connexions (à appeler dans la transaction d'écriture). */
export const assertGithubConnectionUnique = (
  store: any,
  githubAppId: unknown,
  githubInstallationId: unknown,
  exceptKey = ''
) => {
  const appId = `${githubAppId || ''}`,
    installationId = `${githubInstallationId || ''}`
  for (const c of listWebGithubConnections(store)) {
    if (c.key === exceptKey) continue
    if (
      (appId && `${c.value.githubAppId || ''}` === appId) ||
      (installationId && `${c.value.githubInstallationId || ''}` === installationId)
    )
      throw new BadRequestError(DUPLICATE)
  }
}
/** Nouvel identifiant libre de connexion supplémentaire. */
export const newGithubConnectionId = (existing: string[] = []) => {
  for (let i = 0; i < 10; i++) {
    const id = $security.randomStringWithAlphabet(15, 'abcdefghijklmnopqrstuvwxyz0123456789')
    if (!existing.includes(id)) return id
  }
  throw new BadRequestError('Identifiant de connexion indisponible. Réessayez.')
}
export const writeWebGithubConnection = (store: any, id: string, value: Record<string, unknown>) => {
  const row = githubConnectionRow(id, store) || new Record(store.findCollectionByNameOrId(SETTINGS))
  row.set('key', githubSettingsKey(id))
  row.set('valueEncrypted', encryptWebValue(value))
  store.save(row)
  return row
}
/**
 * Cible d'une session de l'assistant. Session antérieure à la mise à jour (sans `connectionId`, `legacy`) : la
 * principale, remplacée si elle existe, créée sinon (comportement de l'ancien code).
 */
export const githubSessionTarget = (info: any): { connectionId: string; mode: 'add' | 'replace'; legacy: boolean } =>
  typeof info?.connectionId === 'string' && GITHUB_CONNECTION_ID.test(info.connectionId)
    ? { connectionId: info.connectionId as string, mode: info.mode === 'replace' ? 'replace' : 'add', legacy: false }
    : { connectionId: GITHUB_PRINCIPAL, mode: 'replace', legacy: true }
/**
 * Livraisons GitHub App sans connexion (enregistrées par l'ancien code pendant un retour arrière du code seul, la
 * migration étant déjà appliquée) : rattachées à la principale, seule connexion de l'ancien code. Les livraisons «
 * Dépôt Git » (`git-…`) gardent ''. Idempotent, appelé au démarrage et par la maintenance.
 */
export const normalizeWebDeliveryConnections = (store: any = $app) => {
  try {
    store
      .db()
      .newQuery(
        "UPDATE web_webhook_deliveries SET connectionId = 'default' WHERE connectionId = '' AND deliveryId NOT LIKE 'git-%'"
      )
      .execute()
  } catch {}
}

export type WebGithubConnectionDelivery = { at: string; status: string } | null
export const githubConnectionLastDelivery = (id: string, store: any = $app): WebGithubConnectionDelivery => {
  try {
    const last = (
      store.findRecordsByFilter('web_webhook_deliveries', 'connectionId = {:id}', '-created', 1, 0, {
        id,
      }) as core.Record[]
    )[0]
    return last
      ? { at: last.getString('lastReceivedAt') || last.getString('created'), status: last.getString('status') }
      : null
  } catch {
    return null
  }
}
/** Sérialisation sans secret. */
export const serializeWebGithubConnection = (
  c: WebGithubConnection,
  extra: { appCount?: number; lastDelivery?: WebGithubConnectionDelivery } = {}
) => ({
  id: c.id,
  label: c.meta.label,
  isPrincipal: c.id === GITHUB_PRINCIPAL,
  disabled: c.meta.disabled,
  configured: githubConnectionConfigured(c.value),
  app: {
    id: `${c.value.githubAppId || ''}`,
    slug: `${c.value.githubAppSlug || ''}`,
    owner: `${c.value.ownerLogin || ''}`,
  },
  installationId: `${c.value.githubInstallationId || ''}`,
  hasPrivateKey: !!c.value.privateKey,
  hasWebhookSecret: !!c.value.webhookSecret,
  webhookUrl: githubWebhookUrl(c.id),
  appCount: extra.appCount || 0,
  lastDelivery: extra.lastDelivery || null,
  repositoryWebhook: c.meta.repositoryWebhook || null,
})

/** Validations de la saisie manuelle (mêmes règles que PUT /settings). Les secrets absents sont conservés. */
const applyCredentials = (target: Record<string, any>, input: any) => {
  for (const name of ['githubAppId', 'githubInstallationId'])
    if (input[name] !== undefined && input[name] !== null) {
      if (!/^\d{1,20}$/.test(`${input[name]}`)) throw new BadRequestError('Identifiant GitHub invalide.')
      target[name] = `${input[name]}`
    }
  if (input.githubAppSlug !== undefined && input.githubAppSlug !== null) {
    if (typeof input.githubAppSlug !== 'string' || !/^[a-z0-9-]{0,100}$/.test(input.githubAppSlug))
      throw new BadRequestError('Nom GitHub App invalide.')
    target.githubAppSlug = input.githubAppSlug
  }
  if (input.privateKey) {
    if (
      typeof input.privateKey !== 'string' ||
      !/^-----BEGIN (RSA )?PRIVATE KEY-----/.test(input.privateKey) ||
      input.privateKey.length > 20000
    )
      throw new BadRequestError('Clé privée PEM invalide.')
    target.privateKey = input.privateKey
  }
  if (input.webhookSecret) {
    if (typeof input.webhookSecret !== 'string' || input.webhookSecret.length < 32 || input.webhookSecret.length > 256)
      throw new BadRequestError('Secret webhook : 32 à 256 caractères.')
    target.webhookSecret = input.webhookSecret
  }
  return target
}
/** Identifiant du chemin, contrôlé APRÈS requirePlatformAdmin (403 avant toute résolution). */
const pathConnectionId = (e: core.RequestEvent) => {
  const id = e.request!.pathValue('connectionId')
  if (!GITHUB_CONNECTION_ID.test(id)) throw new NotFoundError('Connexion GitHub introuvable.')
  return id
}

/** GET /api/web-apps/github/connections — plateforme. */
export const HandleWebGithubConnectionsList = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const connections = listWebGithubConnections($app),
    counts: Record<string, number> = {}
  eachWebRecord($app, 'web_apps', '', (row) => {
    const config = webConfig(row),
      id = resolvedConnectionId(config)
    if (githubConnectionUses(config, id)) counts[id] = (counts[id] || 0) + 1
  })
  return webJson(e, {
    items: connections.map((c) =>
      serializeWebGithubConnection(c, { appCount: counts[c.id] || 0, lastDelivery: githubConnectionLastDelivery(c.id) })
    ),
    max: GITHUB_CONNECTION_MAX,
  })
}
/** POST /api/web-apps/github/connections — saisie manuelle. Sans connexion principale, la connexion créée la devient. */
export const HandleWebGithubConnectionCreate = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    input = webInput(e),
    label =
      input.label === undefined || input.label === null || input.label === '' ? '' : githubConnectionLabel(input.label),
    value = applyCredentials({}, input)
  if (!value.githubAppId || !value.githubInstallationId || !value.privateKey || !value.webhookSecret)
    throw new BadRequestError('Renseignez l’App ID, l’Installation ID, la clé privée PEM et le secret webhook.')
  let id = ''
  $app.runInTransaction((tx) => {
    const all = listWebGithubConnections(tx)
    if (all.length >= GITHUB_CONNECTION_MAX)
      throw new BadRequestError(`${GITHUB_CONNECTION_MAX} connexions GitHub maximum.`)
    assertGithubConnectionUnique(tx, value.githubAppId, value.githubInstallationId)
    id = all.some((c) => c.id === GITHUB_PRINCIPAL) ? newGithubConnectionId(all.map((c) => c.id)) : GITHUB_PRINCIPAL
    writeWebGithubConnection(tx, id, {
      ...value,
      meta: {
        label: label || (id === GITHUB_PRINCIPAL ? GITHUB_PRINCIPAL_LABEL : value.githubAppSlug || 'Connexion GitHub'),
        disabled: false,
        createdAt: new Date().toISOString(),
        createdBy: user.id,
        ...(input.repositoryWebhook !== undefined && input.repositoryWebhook !== null
          ? {
              repositoryWebhook: (() => {
                if (id === GITHUB_PRINCIPAL)
                  throw new BadRequestError('Utilisez une connexion supplémentaire dédiée à ce dépôt.')
                return repositoryWebhookConfig(input.repositoryWebhook)
              })(),
            }
          : {}),
      },
    })
  })
  const created = webGithubConnection(id, $app, { allowDisabled: true })
  return webJson(e, { connection: serializeWebGithubConnection(created), max: GITHUB_CONNECTION_MAX }, 201)
}
/** PATCH /api/web-apps/github/connections/{connectionId} — renommer, (dés)activer, corriger les identifiants. */
export const HandleWebGithubConnectionUpdate = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const id = pathConnectionId(e),
    input = webInput(e),
    label = input.label === undefined ? undefined : githubConnectionLabel(input.label)
  if (input.disabled !== undefined && typeof input.disabled !== 'boolean')
    throw new BadRequestError('Le choix d’activation doit être explicite.')
  const repositoryWebhook =
    input.repositoryWebhook === undefined ? undefined : repositoryWebhookConfig(input.repositoryWebhook)
  if (repositoryWebhook && id === GITHUB_PRINCIPAL)
    throw new BadRequestError('Utilisez une connexion supplémentaire dédiée à ce dépôt.')
  $app.runInTransaction((tx) => {
    const row = githubConnectionRow(id, tx)
    if (!row) throw new NotFoundError('Connexion GitHub introuvable.')
    const current = connectionOf(row),
      value = applyCredentials({ ...current.value }, input)
    if (input.githubAppId !== undefined || input.githubInstallationId !== undefined)
      assertGithubConnectionUnique(tx, value.githubAppId, value.githubInstallationId, current.key)
    // `meta` n'est écrit qu'au premier renommage ou à la première (dés)activation.
    if (label !== undefined || input.disabled !== undefined || repositoryWebhook !== undefined)
      value.meta = {
        ...current.meta,
        ...(label !== undefined ? { label } : {}),
        ...(input.disabled !== undefined ? { disabled: input.disabled } : {}),
        ...(repositoryWebhook !== undefined ? { repositoryWebhook } : {}),
      }
    writeWebGithubConnection(tx, id, value)
  })
  const updated = webGithubConnection(id, $app, { allowDisabled: true })
  return webJson(e, {
    connection: serializeWebGithubConnection(updated, {
      appCount: githubConnectionUsage(id).appCount,
      lastDelivery: githubConnectionLastDelivery(id),
    }),
  })
}
/** DELETE /api/web-apps/github/connections/{connectionId} — refusé tant qu'une application ou un job l'utilise. */
export const HandleWebGithubConnectionDelete = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const id = pathConnectionId(e)
  let manageUrl = ''
  $app.runInTransaction((tx) => {
    const row = githubConnectionRow(id, tx)
    if (!row) throw new NotFoundError('Connexion GitHub introuvable.')
    const { appCount } = githubConnectionUsage(id, tx)
    if (appCount)
      throw new BadRequestError(
        `Cette connexion GitHub est encore utilisée par ${appCount} application(s). Rattachez-les à une autre connexion avant de la supprimer.`
      )
    let slug = ''
    try {
      slug = `${connectionOf(row).value.githubAppSlug || ''}`
    } catch {}
    manageUrl = /^[a-z0-9-]{1,100}$/.test(slug) ? `https://github.com/apps/${slug}` : ''
    // Une session de remplacement de cette connexion ne peut plus aboutir.
    let sessions: core.Record[] = []
    try {
      sessions = tx.findRecordsByFilter(
        SESSIONS,
        'status != "completed" && status != "cancelled"',
        '',
        500,
        0
      ) as core.Record[]
    } catch {}
    for (const session of sessions) {
      let info: any = {}
      try {
        info = JSON.parse(session.getString('publicInfo') || '{}') || {}
      } catch {}
      const target = githubSessionTarget(info)
      if (target.mode !== 'replace' || target.connectionId !== id) continue
      session.set('status', 'cancelled')
      session.set('pendingEncrypted', '')
      tx.save(session)
    }
    tx.delete(row)
  })
  return webJson(e, { deleted: true, notice: 'Supprimez aussi la GitHub App sur GitHub.', manageUrl })
}

const repositoryOf = (body: any) =>
  typeof body?.repository?.full_name === 'string' &&
  body.repository.full_name.length <= 250 &&
  /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(body.repository.full_name)
    ? body.repository.full_name.slice(0, 250)
    : ''
const branchOf = (body: any) =>
  typeof body?.ref === 'string' && body.ref.startsWith('refs/heads/') && body.ref.length <= 211
    ? body.ref.slice(11)
    : ''
/** Application déployée automatiquement par un push de cette connexion sur ce dépôt et cette branche. */
const pushTarget = (row: core.Record, repository: string, branch: string, connectionId: string, store: any) => {
  const c = webConfig(row)
  // Une application « Dépôt Git » ne se déploie jamais sur un push de la GitHub App, même pour le même dépôt.
  return (
    c.source !== 'git' &&
    c.autoDeploy &&
    webSourceBuildSupported(row, store) &&
    `${c.repository || ''}`.toLowerCase() === repository.toLowerCase() &&
    c.branch === branch &&
    resolvedConnectionId(c) === connectionId
  )
}
/** Livraison déjà vue : compteur de doublons. True si elle existait. */
const bumpDuplicate = (tx: any, delivery: string) => {
  let row: core.Record | undefined
  try {
    row = tx.findFirstRecordByFilter('web_webhook_deliveries', 'deliveryId={:id}', { id: delivery })
  } catch {}
  if (!row) return false
  row.set('duplicateCount', Math.min(100000, row.getInt('duplicateCount') + 1))
  row.set('lastReceivedAt', new Date().toISOString())
  tx.save(row)
  return true
}

/**
 * Webhook GitHub App d'une connexion. Routage par le chemin seulement (`default` = chemin legacy), puis UNE HMAC avec
 * le secret de cette seule ligne : ligne inconnue, id mal formé, secret vide ou mauvaise signature donnent la même 401.
 * La désactivation n'est révélée qu'après une signature valide (200 ignoré, livraison enregistrée).
 */
export const webGithubWebhookFor = (e: core.RequestEvent, connectionId: string) => {
  if (!webEnabled()) throw new NotFoundError()
  const row = GITHUB_CONNECTION_ID.test(`${connectionId}`) ? githubConnectionRow(connectionId) : null
  let value: Record<string, any> = {}
  if (row)
    try {
      value = decryptWebValue(row.getString('valueEncrypted')) || {}
    } catch {
      value = {}
    }
  const secret = typeof value.webhookSecret === 'string' ? value.webhookSecret : '',
    raw = readerToString(e.request!.body),
    signature = e.request!.header.get('X-Hub-Signature-256') || '',
    deliveryHeader = e.request!.header.get('X-GitHub-Delivery') || ''
  // Toujours une HMAC (secret factice tiré à chaque requête) : aucun oracle d'existence par le temps de réponse.
  const mac = $security.hs256(raw, secret || $security.randomString(48)),
    ok = !!row && !!secret && raw.length <= 10000000 && $security.equal('sha256=' + mac, signature)
  if (!ok) throw new UnauthorizedError('Signature GitHub invalide.')
  if (!/^[A-Za-z0-9-]{1,100}$/.test(deliveryHeader)) throw new BadRequestError('Livraison invalide.')
  // X-GitHub-Delivery n'est pas signé : un corps signé rejoué sous un autre identifiant repasserait. Comme « Dépôt Git »,
  // la clé porte sur le corps signé (id de connexion : le même corps reçu par deux connexions reste deux livraisons).
  // Compromis : « Redeliver » renvoie ce corps. Une livraison déjà enregistrée reste un doublon pendant 7 jours (purge),
  // même si elle avait été ignorée, comme avec l'ancienne clé (GitHub garde l'identifiant) ; une livraison échouée, jamais
  // enregistrée, se relivre normalement. Pour republier : bouton Déployer.
  const delivery = `gh-${connectionId}-${$security.sha256(raw)}`,
    event = (e.request!.header.get('X-GitHub-Event') || '').slice(0, 60),
    body = JSON.parse(raw),
    repository = repositoryOf(body),
    branch = branchOf(body),
    meta = githubConnectionMeta(connectionId, value)
  if (meta.disabled) {
    let duplicate = false
    $app.runInTransaction((tx) => {
      if (bumpDuplicate(tx, delivery)) {
        duplicate = true
        return
      }
      const seen = new Record(tx.findCollectionByNameOrId('web_webhook_deliveries'))
      seen.set('deliveryId', delivery)
      seen.set('connectionId', connectionId)
      seen.set('event', event)
      seen.set('repository', repository)
      seen.set('branch', branch)
      seen.set('payloadCommit', /^[a-f0-9]{40}$/.test(body.after || '') ? body.after : '')
      seen.set('resolvedCommit', '')
      seen.set('lastReceivedAt', new Date().toISOString())
      seen.set('queued', 0)
      seen.set('status', 'ignored')
      seen.set('note', 'Connexion GitHub désactivée : aucun déploiement.')
      tx.save(seen)
    })
    return webJson(e, { accepted: true, ignored: true, ...(duplicate ? { duplicate: true } : {}) })
  }
  const installation = `${value.githubInstallationId || ''}`
  if (event === 'pull_request') {
    if (meta.repositoryWebhook) throw new ForbiddenError('Cette connexion dédiée ne reçoit que les push du dépôt.')
    if (!installation || `${body.installation?.id || ''}` !== installation)
      throw new ForbiddenError('Installation GitHub inconnue.')
    return webJson(e, handleWebPreviewWebhook(body, delivery, '', connectionId))
  }
  let early = false
  $app.runInTransaction((tx) => {
    early = bumpDuplicate(tx, delivery)
  })
  if (early) return webJson(e, { accepted: true, duplicate: true })
  const ignored = event !== 'push' || body.deleted === true || !branch
  if (!ignored) {
    const bound = meta.repositoryWebhook
    if (
      bound &&
      (connectionId === GITHUB_PRINCIPAL ||
        repository.toLowerCase() !== bound.repository.toLowerCase() ||
        `${body.repository?.id || ''}` !== bound.repositoryId ||
        e.request!.header.get('X-GitHub-Hook-ID') !== bound.hookId)
    )
      throw new ForbiddenError('Webhook ou dépôt GitHub inattendu.')
    // Existing GitHub App connections always require their installation. An explicitly bound repository
    // hook may omit it, but can never supply another installation. HMAC remains mandatory above.
    if (!installation || (body.installation ? `${body.installation.id || ''}` !== installation : !bound))
      throw new ForbiddenError('Installation GitHub inconnue.')
  }
  if (!ignored && !/^[a-f0-9]{40}$/.test(body.after || '')) throw new BadRequestError('Commit invalide.')
  const matching = ignored
    ? []
    : ($app.findRecordsByFilter('web_apps', '', '', 500, 0) as core.Record[]).filter((app) =>
        pushTarget(app, repository, branch, connectionId, $app)
      )
  // Tête relue hors transaction avec CETTE connexion : une livraison retardée ne publie pas une révision plus ancienne.
  let head = ignored ? '' : body.after
  if (matching.length && value.privateKey && value.githubAppId && installation)
    head = webGithub('commit', { repository, branch }, connectionId).sha
  if (!ignored && !/^[a-f0-9]{40}$/.test(head)) throw new BadRequestError('Commit GitHub invalide.')
  let queued = 0,
    wasDuplicate = false
  $app.runInTransaction((tx) => {
    // Nouveau contrôle après la lecture externe : sérialise les livraisons dupliquées concurrentes.
    if (bumpDuplicate(tx, delivery)) {
      wasDuplicate = true
      return
    }
    const seen = new Record(tx.findCollectionByNameOrId('web_webhook_deliveries'))
    seen.set('deliveryId', delivery)
    seen.set('connectionId', connectionId)
    seen.set('event', event)
    seen.set('repository', repository)
    seen.set('branch', branch)
    seen.set('payloadCommit', /^[a-f0-9]{40}$/.test(body.after || '') ? body.after : '')
    seen.set('resolvedCommit', head)
    seen.set('lastReceivedAt', new Date().toISOString())
    if (!ignored)
      for (const app of tx.findRecordsByFilter('web_apps', '', '', 500, 0) as core.Record[])
        if (pushTarget(app, repository, branch, connectionId, tx) && queueWebGitPush(tx, app, head)) queued++
    seen.set('queued', queued)
    seen.set('status', queued ? 'queued' : 'ignored')
    seen.set(
      'note',
      queued
        ? 'Déploiement ajouté à la file.'
        : ignored
          ? body.deleted
            ? 'Suppression de branche : aucun déploiement.'
            : 'Événement sans déploiement.'
          : !matching.length
            ? 'Aucune application automatique ne correspond à ce dépôt et à cette branche.'
            : 'Cette version est déjà publiée ou en cours de déploiement.'
    )
    tx.save(seen)
  })
  return webJson(
    e,
    { accepted: true, queued, ...(wasDuplicate ? { duplicate: true } : {}) },
    ignored || wasDuplicate ? 200 : 202
  )
}
/** POST /api/web-apps/github/webhook/{connectionId} — public, HMAC. `default` n'est jamais routé par ce chemin. */
export const HandleWebGithubConnectionWebhook = (e: core.RequestEvent) => {
  const id = e.request!.pathValue('connectionId')
  return webGithubWebhookFor(e, GITHUB_EXTRA_CONNECTION_ID.test(id) ? id : '')
}

/**
 * Normalise `githubConnectionId` dans une configuration validée (appelé par validateConfig d'api.ts). '' ou 'default'
 * -> clé absente ; source git -> clé absente ; sinon la connexion doit exister et être active SI la valeur change.
 */
export const applyWebGithubConnectionConfig = (
  config: Record<string, any>,
  input: any,
  existing: any = {},
  git = false,
  store: any = $app
) => {
  if (git) {
    delete config.githubConnectionId
    return config
  }
  const raw = input?.githubConnectionId === undefined ? existing?.githubConnectionId : input.githubConnectionId,
    id = raw === undefined || raw === null || raw === '' || raw === GITHUB_PRINCIPAL ? '' : `${raw}`
  if (!id) {
    delete config.githubConnectionId
    return config
  }
  if (!GITHUB_EXTRA_CONNECTION_ID.test(id)) throw new BadRequestError('Connexion GitHub invalide.')
  if (id !== `${existing?.githubConnectionId || ''}`) webGithubConnection(id, store)
  config.githubConnectionId = id
  return config
}
