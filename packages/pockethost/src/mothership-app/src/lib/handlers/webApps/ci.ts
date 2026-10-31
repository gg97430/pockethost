import { webImageDigestValid, webRegistryHostAllowed } from '../../../../../common/webRegistry'
import {
  decryptWebValue,
  encryptWebValue,
  findWebApp,
  queueWebDeployment,
  requireWebAppPermission,
  serializeWebDeployment,
  webActorCan,
  webConfig,
  webEnabled,
  webGithub,
  webInput,
  webJson,
} from './api'
import { assertWebGithubRate, resolveWebGitCommit } from './gitSource'
import { resolvedConnectionId } from './githubConnections'
import { resolveWebEnvironment } from './projects'
import { currentWebRuntimeRevision, runtimeWorkerSnapshot } from './runtimes'
import { storageWorkerSnapshot } from './storage'

const TOKEN = 'web_ci_tokens',
  DELIVERY = 'web_ci_deliveries'
const date = () => new Date().toISOString().replace('T', ' ')
const repository = (value: unknown) => {
  if (
    typeof value !== 'string' ||
    value.length > 250 ||
    !/^(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,})(?::[1-9][0-9]{0,4})?\/[a-z0-9][a-z0-9._/-]*$/.test(value) ||
    // Même règle que le moteur Image Docker : ni réseau interne (`.local`, `.internal`), ni boucle locale.
    !webRegistryHostAllowed(value.split('/')[0]) ||
    value.includes('..') ||
    value.includes('//') ||
    value.endsWith('/')
  )
    throw new BadRequestError('Indiquez un dépôt d’images complet, par exemple ghcr.io/organisation/site.')
  return value
}
const metadata = (row: core.Record) => ({
  id: row.id,
  name: row.getString('name'),
  imageRepository: row.getString('imageRepository'),
  expiresAt: row.getString('expiresAt'),
  revokedAt: row.getString('revokedAt'),
  lastUsedAt: row.getString('lastUsedAt'),
  created: row.getString('created'),
})
const origin = () => `https://${$os.getenv('APP_HOST') || `app.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`}`
const authenticate = (e: core.RequestEvent, store: any = $app) => {
  if (!webEnabled()) throw new NotFoundError()
  const raw = e.request!.header.get('Authorization') || ''
  const match = /^Bearer phci\.([a-z0-9]{15})\.([A-Za-z0-9]{80})$/.exec(raw)
  if (!match) throw new UnauthorizedError('Jeton de déploiement invalide.')
  let token: core.Record
  try {
    token = store.findRecordById(TOKEN, match[1]!)
  } catch {
    throw new UnauthorizedError('Jeton de déploiement invalide.')
  }
  if (
    token.getString('revokedAt') ||
    Date.parse(token.getString('expiresAt')) <= Date.now() ||
    !$security.equal($security.sha256(match[2]!), token.getString('tokenHash'))
  )
    throw new UnauthorizedError('Jeton de déploiement expiré ou révoqué.')
  let actor: core.Record
  try {
    actor = store.findRecordById('users', token.getString('actorId'))
  } catch {
    throw new UnauthorizedError('Accès de déploiement révoqué.')
  }
  // Identifiant longue durée émis sous ci.manage : revérifié à chaque appel sur le projet actuel. Un auteur rétrogradé
  // sous admin (qui ne peut plus lister ni révoquer ses jetons), retiré ou suspendu perd ses jetons.
  if (!webActorCan(actor.id, { appId: token.getString('appId') }, 'ci.manage', store))
    throw new UnauthorizedError('Accès de déploiement révoqué.')
  return { token, actor }
}
export const HandleWebCiTokensList = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'ci.manage').app!
  return webJson(e, {
    items: $app.findRecordsByFilter(TOKEN, 'appId={:id}', '-created', 100, 0, { id: app.id }).map(metadata),
    deployUrl: `${origin()}/api/web-apps/ci/deploy`,
  })
}
export const HandleWebCiTokensCreate = (e: core.RequestEvent) => {
  const { user: actor, app: found } = requireWebAppPermission(e, 'ci.manage'),
    app = found!,
    input = webInput(e)
  if (
    Object.keys(input).some((key) => !['name', 'imageRepository', 'expiresInDays'].includes(key)) ||
    typeof input.name !== 'string' ||
    !input.name.trim() ||
    input.name.trim().length > 80
  )
    throw new BadRequestError('Nom du jeton requis (80 caractères maximum).')
  const imageRepository = repository(input.imageRepository),
    days = input.expiresInDays ?? 30
  if (!Number.isInteger(days) || days < 1 || days > 90) throw new BadRequestError('Durée du jeton entre 1 et 90 jours.')
  const secret = $security.randomString(80)
  let id = ''
  $app.runInTransaction((tx) => {
    if (
      tx.findRecordsByFilter(TOKEN, 'appId={:id} && revokedAt="" && expiresAt>{:now}', '', 10, 0, {
        id: app.id,
        now: date(),
      }).length >= 10
    )
      throw new BadRequestError('Dix jetons actifs maximum par application.')
    const row = new Record(tx.findCollectionByNameOrId(TOKEN))
    row.set('appId', app.id)
    row.set('actorId', actor.id)
    row.set('name', input.name.trim())
    row.set('imageRepository', imageRepository)
    row.set('tokenHash', $security.sha256(secret))
    row.set('expiresAt', new Date(Date.now() + days * 86400000).toISOString())
    tx.save(row)
    id = row.id
  })
  e.response.header().set('Pragma', 'no-cache')
  return webJson(
    e,
    {
      token: `phci.${id}.${secret}`,
      item: metadata($app.findRecordById(TOKEN, id)),
      deployUrl: `${origin()}/api/web-apps/ci/deploy`,
    },
    201
  )
}
export const HandleWebCiTokenRevoke = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'ci.manage').app!,
    id = e.request!.pathValue('tokenId')
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Jeton invalide.')
  const row = $app.findRecordById(TOKEN, id)
  if (row.getString('appId') !== app.id) throw new NotFoundError()
  row.set('revokedAt', date())
  $app.save(row)
  return webJson(e, { item: metadata(row) })
}
export const HandleWebCiDeploy = (e: core.RequestEvent) => {
  const { token, actor } = authenticate(e),
    input = webInput(e)
  if (Object.keys(input).some((key) => !['image', 'commit', 'branch', 'requestId', 'registry'].includes(key)))
    throw new BadRequestError('Options de déploiement CI invalides.')
  const expected = token.getString('imageRepository'),
    image = input.image
  if (
    typeof image !== 'string' ||
    !image.startsWith(expected + '@sha256:') ||
    !/^[a-f0-9]{64}$/.test(image.slice((expected + '@sha256:').length)) ||
    // Jeton émis avant le refus des registres internes : son dépôt enregistré ne vaut pas autorisation.
    !webImageDigestValid(image)
  )
    throw new BadRequestError('L’image doit appartenir au dépôt autorisé et être fixée par son digest SHA256.')
  if (
    typeof input.commit !== 'string' ||
    !/^[a-f0-9]{40}$/.test(input.commit) ||
    typeof input.requestId !== 'string' ||
    !/^[A-Za-z0-9_.:-]{1,100}$/.test(input.requestId)
  )
    throw new BadRequestError('Commit et identifiant unique d’exécution CI requis.')
  let registry: any = null
  if (input.registry !== undefined) {
    const value = input.registry,
      host = expected.split('/')[0]
    if (
      !value ||
      Object.keys(value).some((key) => !['server', 'username', 'password'].includes(key)) ||
      value.server !== host ||
      typeof value.username !== 'string' ||
      !value.username ||
      value.username.length > 200 ||
      typeof value.password !== 'string' ||
      value.password.length < 10 ||
      value.password.length > 10000 ||
      /[\r\n\u0000]/.test(value.username + value.password)
    )
      throw new BadRequestError('Identifiants éphémères du registre invalides.')
    registry = { server: value.server, username: value.username, password: value.password }
  }
  const app = findWebApp(token.getString('appId')),
    config = webConfig(app)
  if (input.branch !== undefined && input.branch !== config.branch)
    throw new BadRequestError('La branche CI ne correspond pas à cet environnement.')
  const fingerprint = $security.sha256(JSON.stringify([image, input.commit, input.branch || '']))
  const previous = $app.findRecordsByFilter(DELIVERY, 'tokenId={:token} && requestId={:request}', '', 1, 0, {
    token: token.id,
    request: input.requestId,
  })[0]
  if (previous) {
    if (previous.getString('payloadHash') !== fingerprint)
      throw new BadRequestError('Cet identifiant CI a déjà servi pour une autre image.')
    return webJson(
      e,
      {
        deployment: serializeWebDeployment($app.findRecordById('web_deployments', previous.getString('deploymentId'))),
        duplicate: true,
      },
      202
    )
  }
  // A late workflow must not replace a newer branch revision (GitHub App, or ls-remote with the deploy key).
  if (config.repository && config.branch) {
    if (config.source !== 'git') assertWebGithubRate(app.id, actor.id)
    const head =
      config.source === 'git'
        ? resolveWebGitCommit(`${config.gitSourceId || ''}`, config.branch)
        : webGithub('commit', { repository: config.repository, branch: config.branch }, resolvedConnectionId(config))
            .sha
    if (head !== input.commit)
      throw new BadRequestError('Cette construction ne correspond plus au dernier commit de la branche.')
  }
  let result: any
  $app.runInTransaction((tx) => {
    authenticate(e, tx)
    const duplicate = tx.findRecordsByFilter(DELIVERY, 'tokenId={:token} && requestId={:request}', '', 1, 0, {
      token: token.id,
      request: input.requestId,
    })[0]
    if (duplicate) {
      if (duplicate.getString('payloadHash') !== fingerprint) throw new BadRequestError('Identifiant CI déjà utilisé.')
      result = {
        deployment: serializeWebDeployment(tx.findRecordById('web_deployments', duplicate.getString('deploymentId'))),
        duplicate: true,
      }
      return
    }
    const current = tx.findRecordById('web_apps', app.id),
      env = resolveWebEnvironment(current, tx),
      runtime = runtimeWorkerSnapshot(current, tx)
    if (!['image', 'worker'].includes(runtime.kind))
      throw new BadRequestError('Choisissez le moteur Image Docker ou Worker pour recevoir une image construite en CI.')
    runtime.image = image
    if (registry) runtime.registry = registry
    current.set('desiredCommit', input.commit)
    tx.save(current)
    for (const pending of tx.findRecordsByFilter(
      'web_deployments',
      'appId={:id} && status="queued" && action="ci-deploy"',
      '',
      100,
      0,
      { id: app.id }
    )) {
      pending.set('status', 'cancelled')
      pending.set('stage', 'Remplacé par une construction CI plus récente')
      pending.set('finishedAt', date())
      scrubWebCiCredentials(pending)
      tx.save(pending)
    }
    const job = queueWebDeployment(
      current,
      'ci-deploy',
      actor.id,
      {
        commit: input.commit,
        env: env.env,
        projectEnvironment: env.context,
        runtime,
        storage: storageWorkerSnapshot(current, tx),
        ci: {
          tokenId: token.id,
          runtimeRevision: currentWebRuntimeRevision(current, tx),
          expiresAt: new Date(Date.now() + 3600000).toISOString(),
          ephemeralRegistry: !!registry,
        },
      },
      tx
    )
    const seen = new Record(tx.findCollectionByNameOrId(DELIVERY))
    seen.set('tokenId', token.id)
    seen.set('requestId', input.requestId)
    seen.set('payloadHash', fingerprint)
    seen.set('deploymentId', job.id)
    tx.save(seen)
    const used = tx.findRecordById(TOKEN, token.id)
    used.set('lastUsedAt', date())
    tx.save(used)
    result = { deployment: serializeWebDeployment(job), duplicate: false }
  })
  return webJson(e, result, 202)
}
export const scrubWebCiCredentials = (job: core.Record) => {
  const snapshot = decryptWebValue(job.getString('snapshotEncrypted'))
  if (snapshot.ci?.ephemeralRegistry && snapshot.runtime?.registry) {
    delete snapshot.runtime.registry
    snapshot.ci.ephemeralRegistry = false
    job.set('snapshotEncrypted', encryptWebValue(snapshot))
  }
}
export const HandleWebCiMaintenance = () => {
  if (!webEnabled()) return
  const cutoff = new Date(Date.now() - 30 * 86400000).toISOString().replace('T', ' ')
  for (const row of $app.findRecordsByFilter(DELIVERY, 'created<{:cutoff}', '', 500, 0, { cutoff })) $app.delete(row)
  for (const job of $app.findRecordsByFilter(
    'web_deployments',
    'action="ci-deploy" && (status="ready" || status="failed" || status="cancelled")',
    '-created',
    1000,
    0
  )) {
    const before = job.getString('snapshotEncrypted')
    scrubWebCiCredentials(job)
    if (before !== job.getString('snapshotEncrypted')) $app.save(job)
  }
}
