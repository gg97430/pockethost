import { type WebPermission, isWebPlatformPermission, webRoleAllows } from '../../../../../common/webAccess'
import { nextWebQueuedJob } from '../../../../../common/webQueue'
import { canSetWebRepository } from '../../security/webProjectAccess'
import {
  isWebPlatformAdmin,
  requirePlatformAdmin,
  requireWebAppPermission,
  requireWebPermission,
  requireWebUser,
  webAppAccessQuery,
  webAppAccessView,
} from './access'
import { assertBackupAppRevision } from './backups'
import { scrubWebCiCredentials } from './ci'
import { assertWebNotDeleting, webDeletionView } from './deletionState'
import { auditWebAppEnv, planWebAppEnv } from './environment'
import { GITHUB_READER } from './github'
import {
  GITHUB_PRINCIPAL,
  applyWebGithubConnectionConfig,
  assertGithubConnectionUnique,
  githubReaderConfig,
  githubWebhookUrl,
  normalizeWebDeliveryConnections,
  resolvedConnectionId,
  webGithubCapability,
  webGithubConnection,
  webGithubWebhookFor,
  writeWebGithubConnection,
} from './githubConnections'
import {
  assertWebGithubRate,
  resolveWebGitCommit,
  webGitAppConfig,
  webGitJobCredentials,
  webGitSourceAppProjects,
} from './gitSource'
import { emitWebAlert } from './notifications'
import { WEB_LOG_READER } from './operationReaders'
import { assertWebPreviewRemovalJob } from './previews'
import {
  assertWebDeploymentInScope,
  assertWebEnvironmentDeployAllowed,
  assertWebEnvironmentRevision,
  resolveWebEnvironment,
  restoreWebEnvironmentBackup,
  webMembership,
  webProject,
  webProjectLimits,
} from './projects'
import {
  applyWebRuntimePublishedImage,
  restoreWebRuntimeBackup,
  runtimeNeedsSource,
  runtimeProfile,
  runtimeWorkerSnapshot,
  validateRuntimeProfile,
} from './runtimes'
import { restoreWebStorageBackup, storageWorkerSnapshot } from './storage'
export * from './access'

export const webRoot = () => $os.getenv('WEB_APPS_ROOT') || `${$app.dataDir()}/web-apps`
const key = () => {
  const value = $os.getenv('WEB_APPS_MASTER_KEY')
  if (!/^[\x21-\x7e]{32}$/.test(value)) throw new BadRequestError('Clé maître Applications web non configurée.')
  return value
}
const encrypt = (value: unknown) => $security.encrypt(JSON.stringify(value), key())
const decrypt = (value: string) => (value ? JSON.parse(toString($security.decrypt(value, key()))) : {})
export const webInput = (e: core.RequestEvent) => JSON.parse(JSON.stringify(e.requestInfo().body || {}))
export const webJson = (e: core.RequestEvent, value: unknown, status = 200) => {
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(status, value)
}
export const webEnabled = () => $os.getenv('WEB_APPS_ENABLED') === 'true'
/** Plafond de sites du serveur, previews comprises (un worker, un disque) ; WEB_APPS_MAX_SITES le remplace. */
const WEB_SITES_MAX = 100
const webSitesMax = () => {
  const value = Number($os.getenv('WEB_APPS_MAX_SITES'))
  return Number.isInteger(value) && value > 0 ? value : WEB_SITES_MAX
}
export const webSitesFull = (store: any = $app) => store.countRecords('web_apps') >= webSitesMax()
export const assertWebSiteCapacity = (store: any = $app) => {
  if (webSitesFull(store))
    throw new BadRequestError(`Limite de ${webSitesMax()} sites atteinte sur ce serveur. Contactez la plateforme.`)
}
/**
 * Routes du worker et des services locaux (`/api/web-apps/_*`) : connexion directe en boucle locale uniquement. Le
 * pare-feu et le proxy du dashboard tournent sur le même hôte : leur pair TCP est aussi 127.0.0.1. Ils refusent ces
 * chemins (404) et ajoutent toujours un en-tête de relais ; une requête qui en porte un n'est donc jamais locale.
 */
const WEB_RELAY_HEADERS = ['X-Forwarded-For', 'X-Forwarded-Host', 'X-Forwarded-Proto', 'X-Real-Ip', 'Forwarded', 'Via']
export const webDirectLoopback = (e: core.RequestEvent) =>
  /^127\.0\.0\.1:|^\[::1\]:/.test(e.request!.remoteAddr || '') &&
  WEB_RELAY_HEADERS.every((name) => !e.request!.header.values(name).length)
export const findWebApp = (id: string) => {
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Application invalide.')
  return $app.findRecordById('web_apps', id)
}
export const webConfig = (r: core.Record) => JSON.parse(r.getString('config') || '{}')
export const webSourceBuildSupported = (r: core.Record, store: any = $app) =>
  runtimeNeedsSource(runtimeProfile(r, store))

const environment = (r: core.Record) =>
  decrypt(r.getString('envEncrypted')) as Record<string, { value: string; build: boolean; runtime: boolean }>
const settingsRecord = () => {
  try {
    return $app.findFirstRecordByFilter('web_app_settings', 'key = "github"')
  } catch {
    return null
  }
}
const settings = () => {
  const r = settingsRecord()
  return r ? decrypt(r.getString('valueEncrypted')) : {}
}
/**
 * Lecteur GitHub avec UNE connexion : seuls {githubAppId, githubAppSlug, githubInstallationId, privateKey, ownerId,
 * ownerLogin} entrent dans le fichier 0600 (ni meta ni webhookSecret). Principale absente : config {}
 * (configured:false). Id explicite inconnu, ou connexion désactivée sans allowDisabled : 400.
 */
export const webGithub = (
  action: string,
  extra: Record<string, unknown> = {},
  connectionId = 'default',
  opts: { allowDisabled?: boolean } = {}
) => {
  const connection = webGithubConnection(connectionId, $app, { allowDisabled: opts.allowDisabled, allowMissing: true })
  const payload = `${$app.dataDir()}/.web-github-${$security.randomString(24)}.json`
  try {
    $os.writeFile(
      payload,
      JSON.stringify({ action, config: githubReaderConfig(connection.value), ...extra }),
      0o600 as any
    )
    const r = JSON.parse(toString($os.cmd('python3', '-c', GITHUB_READER, payload).combinedOutput()))
    if (r.error) throw new BadRequestError(r.error)
    return r
  } finally {
    try {
      $os.remove(payload)
    } catch {}
  }
}
export const serializeWebApp = (r: core.Record) => {
  const config = webConfig(r),
    env = environment(r)
  return {
    id: r.id,
    name: r.getString('name'),
    ...config,
    runtimeKind: runtimeProfile(r).kind,
    sourceDeployable: webSourceBuildSupported(r),
    status: r.getString('status') || 'stopped',
    deletion: webDeletionView(r),
    activeDeployment: r.getString('activeDeployment'),
    desiredCommit: r.getString('desiredCommit'),
    lastError: r.getString('lastError'),
    created: r.getString('created'),
    updated: r.getString('updated'),
    url: `https://${config.generatedDomain}`,
    env: Object.keys(env)
      .sort()
      .map((name) => ({
        name,
        build: env[name]!.build,
        runtime: env[name]!.runtime,
        hasValue: true,
        public: name.startsWith('NEXT_PUBLIC_'),
      })),
  }
}
const phaseKeys: Record<string, string> = {
  fetch: 'Récupération du commit',
  build: 'Construction Next.js',
  start: 'Démarrage de la nouvelle version',
  health: 'Contrôle de santé',
  publish: 'Publication',
  stop: 'Arrêt',
  domain: 'Certificat HTTPS',
  'domains-sync': 'Mise à jour des domaines',
}
const phasesOf = (r: core.Record): any[] => {
  try {
    const value = JSON.parse(r.getString('phases') || '[]')
    return Array.isArray(value) ? value.slice(0, 20) : []
  } catch {
    return []
  }
}
const changePhase = (r: core.Record, key: string, label: string, now: string, terminal = false) => {
  const phases = phasesOf(r),
    last = phases[phases.length - 1]
  if ((terminal || (key && last?.key !== key)) && last && !last.finishedAt) {
    last.finishedAt = now
    last.durationMs = Math.max(0, Date.parse(now) - Date.parse(last.startedAt))
  }
  if (!terminal && key && phaseKeys[key] && last?.key !== key && phases.length < 20)
    phases.push({ key, label: phaseKeys[key], startedAt: now })
  if (key && phaseKeys[key]) r.set('stageKey', key)
  if (label) r.set('stage', label.slice(0, 100))
  r.set('phases', phases)
}
const cancellable = (r: core.Record) =>
  ['deploy', 'ci-deploy'].includes(r.getString('action')) &&
  ['queued', 'running'].includes(r.getString('status')) &&
  !r.getString('publicationStartedAt')
const retryable = (r: core.Record) =>
  r.getString('action') === 'deploy' &&
  ['failed', 'cancelled'].includes(r.getString('status')) &&
  /^[a-f0-9]{40}$/.test(r.getString('commit')) &&
  !!r.getString('snapshotEncrypted')
export const serializeWebDeployment = (r: core.Record, active = '') => ({
  id: r.id,
  appId: r.getString('appId'),
  action: r.getString('action'),
  commit: r.getString('commit'),
  status: r.getString('status'),
  stage: r.getString('stage'),
  stageKey: r.getString('stageKey'),
  error: r.getString('error'),
  created: r.getString('created'),
  updated: r.getString('updated'),
  startedAt: r.getString('startedAt'),
  finishedAt: r.getString('finishedAt'),
  heartbeatAt: r.getString('heartbeatAt'),
  cancelRequested: r.getBool('cancelRequested'),
  cancellable: cancellable(r),
  retryable: retryable(r),
  retriedFrom: r.getString('retriedFrom'),
  phases: phasesOf(r).map((p) => ({
    ...p,
    durationMs: p.finishedAt ? p.durationMs : Math.max(0, Date.now() - Date.parse(p.startedAt)),
  })),
  elapsedSeconds: r.getString('startedAt')
    ? Math.max(
        0,
        Math.floor(
          ((r.getString('finishedAt') ? Date.parse(r.getString('finishedAt')) : Date.now()) -
            Date.parse(r.getString('startedAt'))) /
            1000
        )
      )
    : 0,
  active: active === r.id,
  available: (JSON.parse(r.getString('result') || '{}') || {}).available !== false,
})

const capability = () => {
  let worker: any = {}
  try {
    worker = JSON.parse(toString($os.readFile(`${webRoot()}/worker-status.json`)))
  } catch {}
  const age = Date.now() - Date.parse(worker.checkedAt || '')
  return {
    workerReady: age >= 0 && age < 60000 && worker.ready === true,
    rootlessReady: worker.rootless === true,
    // Au moins une connexion activée et configurée ; un NOMBRE seulement (capabilities visibles des membres).
    ...webGithubCapability(),
    // git et openssh-client présents sur le worker (source « Dépôt Git »).
    gitAvailable: worker.git === true,
    // Railpack (binaire, mise et frontend vérifiés) présent sur le worker.
    railpackAvailable: worker.railpack === true,
    allocationMode: ['adaptive', 'fixed'].includes(worker.allocationMode) ? worker.allocationMode : 'unknown',
    error: age < 60000 ? worker.error || '' : 'Worker non démarré.',
    buildCpu: 1,
    buildMemoryMiB: 3072,
    runtimeCpu: 0.5,
    runtimeMemoryMiB: 768,
  }
}
/** État du worker joint aux listes (GET /api/web-apps, GET /api/web-projects/groups). */
export const webAppsCapability = capability
const pageNumber = (e: core.RequestEvent) =>
  Math.max(1, Math.min(10000, Math.floor(Number(e.request!.url!.query().get('page')) || 1)))
const domainSnapshot = (appId: string) => {
  try {
    return $app
      .findRecordsByFilter('web_app_domains', 'appId = {:id}', '', 100, 0, { id: appId })
      .filter((r) => ['verified', 'pending_certificate', 'active'].includes(r.getString('status')))
      .map((r) => ({ id: r.id, host: r.getString('host'), certificate: r.getString('status') === 'active' }))
  } catch {
    return []
  }
}
/**
 * Plafond de jobs en attente par projet (une file, un worker par hôte). Application sans projet : plateforme, aucun
 * plafond. Un déploiement remplace ceux de cette application encore en file (enqueueWebDeployment) : ils ne comptent
 * pas.
 */
export const assertWebQueueCapacity = (app: core.Record, action: string, store: any = $app) => {
  const member = webMembership(app.id, store)
  if (!member) return
  const limits = webProjectLimits(webProject(member.getString('projectId'), store)).resources
  if (!limits.enabled) return
  const replaced = action === 'deploy' ? " AND NOT (appId={:app} AND action IN ('deploy','ci-deploy'))" : ''
  if (
    store.countRecords(
      'web_deployments',
      $dbx.exp(
        `status='queued'${replaced} AND appId IN (SELECT appId FROM web_app_environments WHERE projectId={:project})`,
        { app: app.id, project: member.getString('projectId') }
      )
    ) >= limits.queuedBuilds
  )
    throw new TooManyRequestsError('Trop de traitements en attente pour ce projet. Réessayez dans quelques minutes.')
}
const enqueueWebDeployment = (
  app: core.Record,
  action: string,
  actorId: string,
  extra: Record<string, any>,
  resolvedCommit: string,
  store: any
) => {
  let commit = resolvedCommit
  const sourceBuild = webSourceBuildSupported(app, store)
  if (action === 'deploy') {
    if (!/^[a-f0-9]{40}$/.test(commit) && sourceBuild) throw new BadRequestError('Commit invalide.')
    app.set('desiredCommit', commit)
    store.save(app)
    const pending = store.findRecordsByFilter(
      'web_deployments',
      'appId = {:id} && status = "queued" && (action = "deploy" || action = "ci-deploy")',
      '',
      500,
      0,
      {
        id: app.id,
      }
    )
    for (const p of pending) {
      p.set('status', 'cancelled')
      p.set('stage', 'Remplacé par un déploiement plus récent')
      p.set('finishedAt', new Date().toISOString())
      if (p.getString('action') === 'ci-deploy') scrubWebCiCredentials(p)
      store.save(p)
    }
  }
  const resolved = resolveWebEnvironment(app, store)
  let snapshot: any = {
    config: webConfig(app),
    env: resolved.env,
    projectEnvironment: resolved.context,
    runtime: runtimeWorkerSnapshot(app, store),
    storage: storageWorkerSnapshot(app, store),
    domains: domainSnapshot(app.id),
    ...extra,
  }
  if (action === 'rollback') {
    const old = store.findRecordById('web_deployments', extra.deploymentId)
    if (
      old.getString('appId') !== app.id ||
      old.getString('status') !== 'ready' ||
      !['deploy', 'rollback', 'start', 'apply-runtime', 'restore-backup', 'promote', 'ci-deploy'].includes(
        old.getString('action')
      ) ||
      !(JSON.parse(old.getString('result') || '{}') || {}).image ||
      (JSON.parse(old.getString('result') || '{}') || {}).available === false
    )
      throw new BadRequestError('Version de retour arrière indisponible.')
    snapshot = {
      ...decrypt(old.getString('snapshotEncrypted')),
      rollbackResult: JSON.parse(old.getString('result') || '{}'),
      domains: domainSnapshot(app.id),
    }
    commit = old.getString('commit')
  }
  if (action === 'start') {
    const old = app.getString('activeDeployment')
      ? store.findRecordById('web_deployments', app.getString('activeDeployment'))
      : null
    if (!old) throw new BadRequestError('Publiez une première version avant de démarrer.')
    snapshot = {
      ...decrypt(old.getString('snapshotEncrypted')),
      rollbackResult: JSON.parse(old.getString('result') || '{}'),
      domains: domainSnapshot(app.id),
    }
    commit = old.getString('commit')
  }
  // A later start/rollback reuses the published image; it must not replay a backup's dashboard-state restoration.
  if (action !== 'restore-backup') delete snapshot.restoreBackup
  if (['start', 'rollback'].includes(action)) {
    delete snapshot.promotion
    delete snapshot.ci
    snapshot.runtime ||= validateRuntimeProfile()
    snapshot.storage ||= []
  }
  const row = new Record(store.findCollectionByNameOrId('web_deployments'))
  row.set('appId', app.id)
  row.set('actorId', actorId)
  row.set('action', action)
  row.set('commit', commit || '')
  row.set('status', 'queued')
  row.set('stage', 'En attente')
  row.set('snapshotEncrypted', encrypt(snapshot))
  store.save(row)
  return row
}
/**
 * Hors plafond de file : le nettoyage qui suit une écriture déjà faite et le redémarrage d'une restauration de
 * stockage, signalé par le worker : les refuser perdrait le travail.
 */
const WEB_QUEUE_INTERNAL = ['preview-remove', 'domains-sync']
export const queueWebDeployment = (
  app: core.Record,
  action: string,
  actorId: string,
  extra: Record<string, any> = {},
  store: any = $app
) => {
  assertWebEnvironmentDeployAllowed(app, action, store)
  const capped = !WEB_QUEUE_INTERNAL.includes(action) && !extra.storageRestore
  // Avant tout appel GitHub et toute écriture ; relu ci-dessous dans la transaction (demandes simultanées).
  if (capped) assertWebQueueCapacity(app, action, store)
  let commit = extra.commit || app.getString('desiredCommit')
  const sourceBuild = webSourceBuildSupported(app, store),
    current = webConfig(app)
  if (action === 'deploy' && sourceBuild && !current.repository)
    throw new BadRequestError('Ce profil construit des sources. Renseignez un dépôt avant de déployer.')
  if (action === 'deploy' && !extra.commit && sourceBuild) {
    if (current.source !== 'git') assertWebGithubRate(app.id, actorId)
    commit =
      current.source === 'git'
        ? // Tête de branche par ls-remote avec la clé de déploiement (400 si la source n'est pas prête).
          resolveWebGitCommit(`${current.gitSourceId || ''}`, current.branch, store)
        : webGithub('commit', { repository: current.repository, branch: current.branch }, resolvedConnectionId(current))
            .sha
  }
  let row: core.Record | undefined
  store.runInTransaction((tx: any) => {
    assertWebNotDeleting(tx.findRecordById('web_apps', app.id))
    if (capped) assertWebQueueCapacity(app, action, tx)
    row = enqueueWebDeployment(app, action, actorId, extra, commit, tx)
  })
  return row!
}
/**
 * Source : GitHub App (défaut, `source` absent) ou « Dépôt Git » (`source:'git'`, `gitSourceId`). Pour une source Git,
 * `repository` est toujours réécrit depuis la ligne web_git_sources ('git:'+canonical) : un client ne peut pas forger
 * un dépôt qui ne correspond pas à sa source.
 */
const validateConfig = (input: any, existing: any = {}, store: any = $app) => {
  const c = { ...existing }
  for (const name of ['repository', 'branch', 'rootDirectory', 'healthPath'])
    if (input[name] !== undefined) c[name] = `${input[name]}`.trim()
  c.repository = c.repository || ''
  c.branch = c.branch || 'main'
  c.rootDirectory = c.rootDirectory || '.'
  c.healthPath = c.healthPath || '/'
  if (input.source !== undefined && input.source !== 'git' && input.source !== 'github')
    throw new BadRequestError('Source attendue : github ou git.')
  const git = input.source === 'git' || (input.source === undefined && existing.source === 'git')
  if (git) {
    const id = `${input.gitSourceId ?? existing.gitSourceId ?? ''}`
    if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Dépôt Git invalide.')
    try {
      Object.assign(c, webGitAppConfig(id, store))
    } catch (error) {
      // Source disparue : une configuration enregistrée reste lisible (et modifiable vers GitHub), sans dépôt forgé.
      if (input.source !== undefined || input.gitSourceId !== undefined) throw error
      Object.assign(c, { source: 'git', gitSourceId: id, repository: `${existing.repository || ''}` })
    }
  } else {
    delete c.source
    delete c.gitSourceId
    if (c.repository && (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(c.repository) || c.repository.includes('..')))
      throw new BadRequestError('Dépôt attendu au format propriétaire/dépôt.')
  }
  if (!/^[A-Za-z0-9_./-]{1,200}$/.test(c.branch) || c.branch.includes('..'))
    throw new BadRequestError('Branche invalide.')
  if (
    c.rootDirectory.startsWith('/') ||
    c.rootDirectory.split('/').includes('..') ||
    !/^[A-Za-z0-9_./-]{1,200}$/.test(c.rootDirectory)
  )
    throw new BadRequestError('Dossier du projet invalide.')
  if (!/^\/[A-Za-z0-9_./-]{0,200}$/.test(c.healthPath)) throw new BadRequestError('Chemin de contrôle invalide.')
  c.repositoryId = git ? 0 : Number(input.repositoryId ?? c.repositoryId) || 0
  c.autoDeploy = input.autoDeploy === undefined ? existing.autoDeploy !== false : input.autoDeploy === true
  // '' / 'default' -> clé absente ; source git -> clé absente ; sinon connexion existante et active si la valeur change.
  applyWebGithubConnectionConfig(c, input, existing, git, store)
  return c
}
export const HandleWebAppsList = (e: core.RequestEvent) => {
  const user = requireWebUser(e),
    page = pageNumber(e),
    // Jointure membres en base (pas de liste d'identifiants) : pagination identique pour tous les rôles.
    q = isWebPlatformAdmin(user) ? { filter: '', params: {} } : webAppAccessQuery(user, 'id'),
    rows = $app.findRecordsByFilter('web_apps', q.filter, '-created', 26, (page - 1) * 25, q.params)
  return webJson(e, {
    enabled: true,
    capabilities: capability(),
    items: rows.slice(0, 25).map((r) => ({ ...serializeWebApp(r), access: webAppAccessView(user, r) })),
    page,
    hasMore: rows.length > 25,
  })
}
export const HandleWebAppCreate = (e: core.RequestEvent) => {
  const user = requirePlatformAdmin(e),
    input = webInput(e),
    name = `${input.name || ''}`.trim(),
    profile = validateRuntimeProfile(input.runtime),
    config = validateConfig(input)
  if (!name || name.length > 80) throw new BadRequestError('Nom requis (80 caractères maximum).')
  const source = runtimeNeedsSource(profile)
  if (source && !config.repository) throw new BadRequestError('Un dépôt est requis pour construire ce profil.')
  if (!source) {
    config.repository = ''
    config.repositoryId = 0
    config.autoDeploy = false
    delete config.source
    delete config.gitSourceId
    delete config.githubConnectionId
  }
  let savedId = ''
  $app.runInTransaction((tx) => {
    // Relue dans la transaction d'écriture : sérialisée avec la suppression de la connexion (aucune app orpheline).
    if (config.githubConnectionId) webGithubConnection(config.githubConnectionId, tx)
    assertWebSiteCapacity(tx)
    const row = new Record(tx.findCollectionByNameOrId('web_apps'))
    row.set('name', name)
    row.set('uid', user.id)
    row.set('status', 'stopped')
    row.set('config', config)
    const plan = input.env === undefined ? null : planWebAppEnv(row, input.env)
    if (plan) row.set('envEncrypted', encrypt(plan.next))
    tx.save(row)
    if (plan) auditWebAppEnv(row.id, user.id, plan, tx)
    config.generatedDomain = `web-${row.id}.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`
    row.set('config', config)
    tx.save(row)
    restoreWebRuntimeBackup(row, profile, tx)
    savedId = row.id
  })
  return webJson(e, { app: serializeWebApp(findWebApp(savedId)) }, 201)
}

export const HandleWebAppGet = (e: core.RequestEvent) => {
  const { user, app } = requireWebAppPermission(e, 'app.read'),
    rows = $app.findRecordsByFilter('web_deployments', 'appId={:id}', '-created', 26, 0, { id: app!.id })
  return webJson(e, {
    app: serializeWebApp(app!),
    access: webAppAccessView(user, app!),
    deployments: rows.slice(0, 25).map((r) => serializeWebDeployment(r, app!.getString('activeDeployment'))),
    hasMore: rows.length > 25,
    capabilities: capability(),
  })
}
/**
 * Application de projet avec une source Git : la source appartient au même projet (cible RBAC de ses routes). Une
 * source sans projet, ou dont aucune autre application n'est dans un autre projet (rattachement resté après un
 * détachement), est adoptée par celui-ci ; une source encore utilisée dans un autre projet est refusée (une clé par
 * dépôt). `appId` : l'application rattachée, exclue du décompte.
 */
export const bindWebGitSourceProject = (config: any, projectId: string, store: any = $app, appId = '') => {
  if (config?.source !== 'git' || !projectId) return
  let row: core.Record
  try {
    row = store.findRecordById('web_git_sources', `${config.gitSourceId || ''}`)
  } catch {
    throw new NotFoundError('Dépôt Git introuvable.')
  }
  const current = row.getString('projectId')
  if (current === projectId) return
  if (current && webGitSourceAppProjects(row.id, store, appId).some((id) => !!id && id !== projectId))
    throw new BadRequestError('Ce dépôt Git est relié à un autre projet.')
  row.set('projectId', projectId)
  store.save(row)
}
const assertWebPreviewSourceRepositoryKept = (appId: string, store: any) => {
  const member = webMembership(appId, store)
  if (!member) return
  const project = webProject(member.getString('projectId'), store)
  if (
    project.getBool('multiRepository') &&
    project.getBool('previewEnabled') &&
    project.getString('previewSourceAppId') === appId
  )
    throw new BadRequestError(
      'Ce site sert de modèle aux previews du projet. Désactivez les previews avant de changer son dépôt.'
    )
}
/**
 * PATCH composite : dépôt -> app.repository (plateforme) sauf dépôt de la liste autorisée du projet (admin) ; source
 * (GitHub App / Dépôt Git) -> source.git.configure (plateforme) ; nom, branche, dossier, chemin de contrôle,
 * déploiement auto -> app.configure ; variables -> variables.write.
 */
export const HandleWebAppUpdate = (e: core.RequestEvent) => {
  const access = requireWebAppPermission(e, 'app.read'),
    row = access.app!,
    input = webInput(e),
    need = (permission: WebPermission) => {
      if (webRoleAllows(access.role, permission)) return
      throw new ForbiddenError(
        isWebPlatformPermission(permission)
          ? 'Action réservée aux administrateurs de la plateforme.'
          : 'Droits insuffisants sur ce projet.'
      )
    }
  // Changement de connexion demandé : droit vérifié AVANT toute résolution de l'identifiant (pas d'oracle
  // d'existence ou d'activation pour les membres du projet).
  if (input.githubConnectionId !== undefined) {
    const before = webConfig(row),
      raw = input.githubConnectionId,
      wanted = raw === null || raw === '' || raw === GITHUB_PRINCIPAL ? '' : `${raw}`,
      git = input.source === 'git' || (input.source === undefined && before.source === 'git'),
      current = before.source === 'git' ? '' : `${before.githubConnectionId || ''}`
    if (!git && wanted !== current) need('app.repository')
  }
  const base = validateConfig({}, webConfig(row)),
    next = validateConfig(input, webConfig(row))
  const isGit = next.source === 'git',
    sourceChanged =
      (next.source || 'github') !== (base.source || 'github') || (next.gitSourceId || '') !== (base.gitSourceId || '')
  // Les dépôts 'git:' sont sensibles à la casse hors des forges : comparaison exacte.
  const repositoryChanged =
    sourceChanged ||
    (isGit ? next.repository !== base.repository : next.repository.toLowerCase() !== base.repository.toLowerCase())
  // Rattacher une source Git relève de la plateforme ; allowedRepositories ne liste que des dépôts propriétaire/dépôt.
  if (sourceChanged) need(isGit ? 'source.git.configure' : 'app.repository')
  else if (repositoryChanged && !canSetWebRepository(access.project, access.user, next.repository, base.repository))
    need('app.repository')
  // Changer de connexion GitHub change les identifiants qui lisent le dépôt : plateforme seulement.
  const connectionChanged = !isGit && resolvedConnectionId(next) !== resolvedConnectionId(base)
  if (connectionChanged) need('app.repository')
  if (
    repositoryChanged ||
    next.repositoryId !== base.repositoryId ||
    ['repository', 'branch', 'rootDirectory', 'healthPath', 'autoDeploy'].some((k) => next[k] !== base[k]) ||
    (input.name !== undefined && `${input.name}`.trim() !== row.getString('name'))
  )
    need('app.configure')
  if (input.env !== undefined) need('variables.write')
  if (input.name !== undefined) {
    const name = `${input.name}`.trim()
    if (!name || name.length > 80) throw new BadRequestError('Nom invalide.')
    row.set('name', name)
  }
  row.set('config', next)
  // Dépôt changé par un admin de projet, ou source changée : il doit rester compatible avec le projet (sinon 400, rien
  // n'est enregistré). Une source Git appartient au projet de l'application.
  // Connexion changée : relue dans la transaction d'écriture, sérialisée avec sa suppression (aucune app orpheline).
  const bindProject = repositoryChanged && (!access.platform || sourceChanged)
  // Groupe de sites : le dépôt du projet suit le modèle des previews ; son dépôt ne change pas tant qu'elles sont actives.
  const previewSourceGuard = repositoryChanged && !!access.environment
  if (bindProject || connectionChanged || previewSourceGuard || input.env !== undefined)
    $app.runInTransaction((tx) => {
      if (previewSourceGuard) assertWebPreviewSourceRepositoryKept(row.id, tx)
      if (connectionChanged && next.githubConnectionId) webGithubConnection(next.githubConnectionId, tx)
      // Variables relues dans la transaction : même plan et même audit que /environment.
      const plan = input.env === undefined ? null : planWebAppEnv(tx.findRecordById('web_apps', row.id), input.env)
      if (plan) row.set('envEncrypted', encrypt(plan.next))
      if (bindProject || plan) resolveWebEnvironment(row, tx)
      if (bindProject) bindWebGitSourceProject(next, access.environment?.getString('projectId') || '', tx, row.id)
      tx.save(row)
      if (plan) auditWebAppEnv(row.id, access.user.id, plan, tx)
    })
  else $app.save(row)
  return webJson(e, { app: serializeWebApp(row) })
}
export const HandleWebAppAction = (e: core.RequestEvent) => {
  const { user, app: found, platform } = requireWebAppPermission(e, 'app.deploy'),
    app = found!,
    action = e.request!.pathValue('action')
  if (!['deploy', 'rollback', 'start', 'stop'].includes(action)) throw new BadRequestError('Action inconnue.')
  const input = webInput(e)
  if (action === 'rollback' && !/^[a-z0-9]{15}$/.test(input.deploymentId || ''))
    throw new BadRequestError('Version de retour arrière invalide.')
  // Retour arrière et redémarrage rejouent une image et des variables figées : jamais celles d'un autre projet.
  if (!platform && ['rollback', 'start'].includes(action)) {
    let old: core.Record | null = null
    try {
      old = $app.findRecordById(
        'web_deployments',
        action === 'rollback' ? input.deploymentId : app.getString('activeDeployment')
      )
    } catch {}
    if (old?.getString('appId') === app.id) assertWebDeploymentInScope(app, old)
  }
  const row = queueWebDeployment(
    app,
    action,
    user.id,
    action === 'rollback'
      ? { deploymentId: input.deploymentId }
      : // Reconstruction complète : le worker ignore l’image réutilisable et le cache BuildKit.
        action === 'deploy' && input.fullRebuild === true
        ? { fullRebuild: true }
        : {}
  )
  return webJson(e, { deployment: serializeWebDeployment(row) }, 202)
}
export const HandleWebDeployments = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'app.read').app!,
    page = pageNumber(e),
    rows = $app.findRecordsByFilter('web_deployments', 'appId={:id}', '-created', 26, (page - 1) * 25, { id: app.id })
  return webJson(e, {
    items: rows.slice(0, 25).map((r) => serializeWebDeployment(r, app.getString('activeDeployment'))),
    page,
    hasMore: rows.length > 25,
  })
}
const requestWebDeployment = (e: core.RequestEvent, permission: WebPermission) => {
  const { user, app: found, platform } = requireWebAppPermission(e, permission),
    app = found!,
    id = e.request!.pathValue('deploymentId')
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Déploiement invalide.')
  const job = $app.findRecordById('web_deployments', id)
  if (job.getString('appId') !== app.id) throw new ForbiddenError('Accès refusé.')
  return { user, app, job, platform }
}
export const HandleWebDeploymentLogs = (e: core.RequestEvent) => {
  const { job } = requestWebDeployment(e, 'logs.read'),
    cursor = e.request!.url!.query().get('cursor') || ''
  if (cursor.length > 1000 || (cursor && !/^[A-Za-z0-9_-]+$/.test(cursor)))
    throw new BadRequestError('Curseur de journal invalide.')
  try {
    return webJson(
      e,
      JSON.parse(toString($os.cmd('python3', '-c', WEB_LOG_READER, webRoot(), job.id, cursor).combinedOutput()))
    )
  } catch {
    throw new BadRequestError('Le journal est indisponible pour le moment.')
  }
}
export const HandleWebDeploymentCancel = (e: core.RequestEvent) => {
  const { app, job } = requestWebDeployment(e, 'app.deploy')
  let value: any
  $app.runInTransaction((tx) => {
    const row = tx.findRecordById('web_deployments', job.id)
    if (!cancellable(row))
      throw new BadRequestError(
        'Ce déploiement ne peut plus être annulé. La publication est déjà engagée ou le traitement est terminé.'
      )
    const now = new Date().toISOString()
    row.set('cancelRequested', true)
    row.set('cancelRequestedAt', now)
    if (row.getString('status') === 'queued') {
      row.set('status', 'cancelled')
      row.set('stage', 'Annulé avant démarrage')
      row.set('finishedAt', now)
      if (row.getString('action') === 'ci-deploy') scrubWebCiCredentials(row)
      changePhase(row, '', 'Annulé avant démarrage', now, true)
    }
    tx.save(row)
    value = serializeWebDeployment(row, app.getString('activeDeployment'))
  })
  return webJson(e, { deployment: value }, 202)
}
export const HandleWebDeploymentRetry = (e: core.RequestEvent) => {
  const { user, app, job, platform } = requestWebDeployment(e, 'app.deploy')
  if (!retryable(job)) throw new BadRequestError('Seuls les déploiements échoués ou annulés peuvent être relancés.')
  const original = decrypt(job.getString('snapshotEncrypted'))
  if (!original.config || !original.env)
    throw new BadRequestError('La configuration de ce déploiement est indisponible.')
  // Le worker relit le dépôt figé avec le jeton de toute l'installation GitHub : même dépôt et même projet exigés.
  if (!platform) {
    assertWebDeploymentInScope(app, job)
    const latest = webConfig(app),
      git = original.config.source === 'git' || latest.source === 'git'
    // Source Git : même dépôt exact ET même source (le claim lit la clé de la source figée dans l'instantané).
    if (
      git
        ? original.config.source !== latest.source ||
          `${original.config.gitSourceId || ''}` !== `${latest.gitSourceId || ''}` ||
          `${original.config.repository || ''}` !== `${latest.repository || ''}`
        : `${original.config.repository || ''}`.toLowerCase() !== `${latest.repository || ''}`.toLowerCase() ||
          resolvedConnectionId(original.config) !== resolvedConnectionId(latest)
    )
      throw new ForbiddenError(
        'Ce déploiement utilisait un autre dépôt Git. Déployez une nouvelle version ; seul un administrateur de la plateforme peut le relancer.'
      )
  }
  let row: core.Record | undefined
  $app.runInTransaction((tx) => {
    const currentApp = tx.findRecordById('web_apps', app.id),
      old = tx.findRecordById('web_deployments', job.id)
    if (!retryable(old)) throw new BadRequestError('Ce déploiement ne peut plus être relancé.')
    // Do not re-resolve a branch or use edited environment variables: retry this immutable revision.
    row = queueWebDeployment(currentApp, 'deploy', user.id, { commit: old.getString('commit') }, tx)
    row!.set('snapshotEncrypted', encrypt({ ...original, domains: domainSnapshot(app.id) }))
    row!.set('retriedFrom', old.id)
    tx.save(row!)
  })
  return webJson(e, { deployment: serializeWebDeployment(row!) }, 202)
}
export const HandleWebRuntime = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'app.read').app!
  let value: any
  try {
    value = JSON.parse(toString($os.readFile(`${webRoot()}/runtime-status.json`))).applications?.[app.id]
  } catch {}
  const age = value ? Date.now() - Date.parse(value.checkedAt || '') : Infinity
  if (!value || age < 0 || age > 90000)
    return webJson(e, {
      available: false,
      scope: 'application-container',
      checkedAt: value?.checkedAt || '',
      state: app.getString('status'),
      lastError: 'Les mesures du conteneur ne sont pas encore disponibles.',
    })
  // Never return Docker inspect output, container environment or another application's metrics.
  return webJson(e, {
    available: value.available === true,
    scope: value.scope === 'application-services' ? 'application-services' : 'application-container',
    serviceCount: Number.isInteger(value.services) && value.services > 0 && value.services <= 4 ? value.services : 1,
    checkedAt: value.checkedAt,
    state: value.state,
    cpuPercent: value.cpuPercent,
    memoryBytes: value.memoryBytes,
    memoryLimitBytes: value.memoryLimitBytes,
    pids: value.pids,
    lastError: value.lastError || '',
  })
}
export const HandleWebWebhookDeliveries = (e: core.RequestEvent) => {
  const c = webConfig(requireWebAppPermission(e, 'app.read').app!),
    page = pageNumber(e)
  const rows = $app.findRecordsByFilter(
    'web_webhook_deliveries',
    'repository={:repository} && branch={:branch} && connectionId={:connection}',
    '-created',
    26,
    (page - 1) * 25,
    { repository: c.repository, branch: c.branch, connection: resolvedConnectionId(c) }
  )
  return webJson(e, {
    page,
    hasMore: rows.length > 25,
    items: rows.slice(0, 25).map((r) => ({
      id: r.id,
      deliveryId: r.getString('deliveryId'),
      event: r.getString('event'),
      repository: r.getString('repository'),
      branch: r.getString('branch'),
      payloadCommit: r.getString('payloadCommit'),
      resolvedCommit: r.getString('resolvedCommit'),
      status: r.getString('status'),
      queued: r.getInt('queued'),
      duplicateCount: r.getInt('duplicateCount'),
      note: r.getString('note'),
      created: r.getString('created'),
      lastReceivedAt: r.getString('lastReceivedAt'),
    })),
  })
}

export const HandleWebSettingsGet = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const s = settings()
  return webJson(e, {
    settings: {
      githubAppId: s.githubAppId || '',
      githubAppSlug: s.githubAppSlug || '',
      githubInstallationId: s.githubInstallationId || '',
      hasPrivateKey: !!s.privateKey,
      hasWebhookSecret: !!s.webhookSecret,
      webhookUrl: githubWebhookUrl('default'),
    },
    capabilities: capability(),
  })
}
export const HandleWebSettingsPut = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const input = webInput(e),
    s = settings()
  for (const name of ['githubAppId', 'githubInstallationId'])
    if (input[name] !== undefined) {
      if (input[name] && !/^\d{1,20}$/.test(`${input[name]}`)) throw new BadRequestError('Identifiant GitHub invalide.')
      s[name] = `${input[name]}`
    }
  if (input.githubAppSlug !== undefined) {
    if (!/^[a-z0-9-]{0,100}$/.test(input.githubAppSlug)) throw new BadRequestError('Nom GitHub App invalide.')
    s.githubAppSlug = input.githubAppSlug
  }
  if (input.privateKey) {
    if (!/^-----BEGIN (RSA )?PRIVATE KEY-----/.test(input.privateKey) || input.privateKey.length > 20000)
      throw new BadRequestError('Clé privée PEM invalide.')
    s.privateKey = input.privateKey
  }
  if (input.webhookSecret) {
    if (input.webhookSecret.length < 32 || input.webhookSecret.length > 256)
      throw new BadRequestError('Secret webhook : 32 à 256 caractères.')
    s.webhookSecret = input.webhookSecret
  }
  if (input.githubAppId !== undefined || input.githubInstallationId !== undefined)
    assertGithubConnectionUnique($app, s.githubAppId, s.githubInstallationId, 'github')
  const r = settingsRecord() || new Record($app.findCollectionByNameOrId('web_app_settings'))
  r.set('key', 'github')
  r.set('valueEncrypted', encrypt(s))
  $app.save(r)
  return HandleWebSettingsGet(e)
}
const githubPage = (e: core.RequestEvent) => {
  const query = e.request!.url!.query()
  return {
    page: Math.max(1, Math.min(1000, Number(query.get('page')) || 1)),
    perPage: Math.max(1, Math.min(100, Number(query.get('perPage')) || 50)),
    search: (query.get('search') || '').slice(0, 100),
  }
}
export const HandleWebRepositories = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  return webJson(e, webGithub('repositories', githubPage(e), e.request!.url!.query().get('connectionId') || 'default'))
}
/**
 * SuperAdmin : toute branche de l'App GitHub partagée. Admin de projet : ?appId= requis, uniquement le dépôt de cette
 * application (identifiant ET nom renvoyé par GitHub), pour ne jamais lister le dépôt privé d'une autre équipe.
 */
export const HandleWebBranches = (e: core.RequestEvent) => {
  const user = requireWebUser(e),
    repositoryId = e.request!.pathValue('repositoryId')
  if (isWebPlatformAdmin(user)) {
    if (!/^[1-9][0-9]{0,19}$/.test(repositoryId)) throw new BadRequestError('Dépôt GitHub invalide.')
    const query = e.request!.url!.query(),
      appId = query.get('appId')
    // Spec §5 : avec ?appId, toujours la connexion de l'application (un connectionId client est ignoré) ; sinon
    // ?connectionId, sinon la principale. (Le dashboard plateforme n'envoie jamais ?appId, seulement ?connectionId.)
    const connectionId = appId
      ? resolvedConnectionId(webConfig(findWebApp(appId))) || 'default'
      : query.get('connectionId') || 'default'
    assertWebGithubRate(appId || connectionId, user.id)
    return webJson(e, webGithub('branches', { ...githubPage(e), repositoryId }, connectionId))
  }
  const target = e.request!.url!.query().get('appId')
  if (!target) throw new ForbiddenError('Action réservée aux administrateurs de la plateforme.')
  const c = webConfig(requireWebPermission(e, { appId: target }, 'app.configure').app!)
  if (c.source === 'git') throw new ForbiddenError('Utilisez la liste des branches du dépôt Git.')
  if (!/^[1-9][0-9]{0,19}$/.test(repositoryId) || String(c.repositoryId || '') !== repositoryId || !c.repository)
    throw new ForbiddenError('Seules les branches du dépôt de cette application sont consultables.')
  assertWebGithubRate(target, user.id)
  const result = webGithub('branches', { ...githubPage(e), repositoryId }, resolvedConnectionId(c))
  if (`${result.repository || ''}`.toLowerCase() !== `${c.repository}`.toLowerCase())
    throw new ForbiddenError('Seules les branches du dépôt de cette application sont consultables.')
  return webJson(e, result)
}
/**
 * Push vérifié (GitHub App ou webhook « Dépôt Git ») pour une application concernée, dans la transaction de la
 * livraison : fixe desiredCommit, puis ajoute un déploiement sauf si cette tête est déjà publiée ou en cours (les
 * déploiements en attente d'une autre révision sont alors remplacés). True si un déploiement est ajouté.
 */
export const queueWebGitPush = (tx: any, row: core.Record, head: string): boolean => {
  const pending = tx.findRecordsByFilter(
    'web_deployments',
    'appId={:id} && (status="queued" || status="running")',
    '',
    500,
    0,
    { id: row.id }
  ) as core.Record[]
  const active = row.getString('activeDeployment')
    ? (tx.findRecordById('web_deployments', row.getString('activeDeployment')) as core.Record)
    : null
  row.set('desiredCommit', head)
  tx.save(row)
  if (pending.some((job) => job.getString('commit') === head) || active?.getString('commit') === head) {
    for (const job of pending)
      if (job.getString('status') === 'queued' && job.getString('commit') !== head) {
        job.set('status', 'cancelled')
        job.set('stage', 'Remplacé par la branche actuelle')
        job.set('finishedAt', new Date().toISOString())
        tx.save(job)
      }
    return false
  }
  queueWebDeployment(row, 'deploy', '', { commit: head }, tx)
  return true
}
/** Chemin legacy /api/web-apps/github/webhook : connexion principale (routage et HMAC dans githubConnections.ts). */
export const HandleWebGithubWebhook = (e: core.RequestEvent) => webGithubWebhookFor(e, 'default')

export const requireWorker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const expected = $os.getenv('WEB_APPS_WORKER_SECRET'),
    actual = e.request!.header.get('X-Web-Worker') || ''
  if (expected.length < 32 || !$security.equal(expected, actual)) throw new UnauthorizedError('Worker non autorisé.')
  if (!webDirectLoopback(e)) throw new ForbiddenError('Accès local requis.')
}
/** No credentials: the rootless worker publishes this registry for live ingress and admission checks. */
export const HandleWebWorkerProjectLimits = (e: core.RequestEvent) => {
  requireWorker(e)
  const projects: Record<string, unknown> = {},
    apps: Record<string, string> = {}
  for (const row of $app.findRecordsByFilter('web_projects', '', '', 10000, 0)) projects[row.id] = webProjectLimits(row)
  for (const row of $app.findRecordsByFilter('web_app_environments', 'appId!=""', '', 10000, 0)) {
    const projectId = row.getString('projectId')
    if (projects[projectId]) apps[row.getString('appId')] = projectId
  }
  return webJson(e, { version: 1, projects, apps })
}
export const HandleWebWorkerClaim = (e: core.RequestEvent) => {
  requireWorker(e)
  let job: any = null
  $app.runInTransaction((tx) => {
    const running = tx.findRecordsByFilter('web_deployments', 'status="running"', '', 1, 0)
    if (running.length) {
      const stale = running[0]!,
        last = Date.parse(stale.getString('heartbeatAt') || stale.getString('startedAt') || '')
      if (!last || Date.now() - last < 5 * 60000) return
      const now = new Date().toISOString(),
        cancelled = stale.getBool('cancelRequested')
      stale.set('status', cancelled ? 'cancelled' : 'failed')
      stale.set('finishedAt', now)
      stale.set(
        'error',
        cancelled
          ? ''
          : 'Le worker ne confirme plus ce déploiement. Il a été interrompu ; la version publiée est conservée.'
      )
      changePhase(stale, '', cancelled ? 'Annulé après interruption' : 'Interrompu', now, true)
      if (stale.getString('action') === 'ci-deploy') scrubWebCiCredentials(stale)
      tx.save(stale)
      const owner = tx.findRecordById('web_apps', stale.getString('appId'))
      if (!owner.getString('activeDeployment') && !cancelled) {
        owner.set('status', 'failed')
        owner.set('lastError', stale.getString('error'))
        tx.save(owner)
      }
    }
    const queue = (
      tx.findRecordsByFilter('web_deployments', 'status="queued"', 'created,id', 500, 0) as core.Record[]
    ).map((row) => ({ row, appId: row.getString('appId') }))
    const next = nextWebQueuedJob(
      queue,
      (appId) => webMembership(appId, tx)?.getString('projectId') || '',
      (appId) =>
        Date.parse(
          tx
            .findRecordsByFilter('web_deployments', 'appId={:id} && startedAt!=""', '-startedAt', 1, 0, {
              id: appId,
            })[0]
            ?.getString('startedAt') || ''
        ) || 0
    )
    if (!next) return
    const r = next.row
    if (r.getString('action') === 'preview-remove')
      assertWebPreviewRemovalJob(
        tx.findRecordById('web_apps', r.getString('appId')),
        decrypt(r.getString('snapshotEncrypted')),
        tx
      )
    const claimedSnapshot = decrypt(r.getString('snapshotEncrypted'))
    const policyMember = webMembership(r.getString('appId'), tx)
    claimedSnapshot.projectPolicy = policyMember
      ? {
          projectId: policyMember.getString('projectId'),
          limits: webProjectLimits(webProject(policyMember.getString('projectId'), tx)),
          appIds: tx
            .findRecordsByFilter('web_app_environments', 'projectId={:id}', '', 10000, 0, {
              id: policyMember.getString('projectId'),
            })
            .map((row) => row.getString('appId'))
            .filter(Boolean),
        }
      : null
    if (r.getString('action') === 'ci-deploy' && Date.parse(claimedSnapshot.ci?.expiresAt || '') <= Date.now()) {
      r.set('status', 'failed')
      r.set('error', 'Les identifiants CI éphémères ont expiré. Relancez le workflow GitHub.')
      r.set('finishedAt', new Date().toISOString())
      scrubWebCiCredentials(r)
      tx.save(r)
      return
    }
    // Source « Dépôt Git » : identifiants lus sur la ligne ACTUELLE (une rotation s'applique aux jobs en file et aux
    // relances). Seul un déploiement qui construit des sources les reçoit.
    const claimedConfig = claimedSnapshot.config || {}
    let git: any = null
    if (
      claimedConfig.source === 'git' &&
      r.getString('action') === 'deploy' &&
      runtimeNeedsSource(claimedSnapshot.runtime || {})
    ) {
      try {
        // Repli par branche du worker (serveur qui refuse un fetch par commit) : la branche d'où vient le commit, celle de
        // l'environnement source pour une promotion reconstruite, jamais celle de la cible.
        git = webGitJobCredentials(
          `${claimedConfig.gitSourceId || ''}`,
          r.getString('commit'),
          `${claimedSnapshot.promotion?.sourceBranch || claimedConfig.branch || ''}`,
          tx
        )
      } catch {
        const message = 'Source Git indisponible : confirmez l’empreinte et la clé de déploiement.'
        r.set('status', 'failed')
        r.set('error', message)
        r.set('finishedAt', new Date().toISOString())
        tx.save(r)
        const owner = tx.findRecordById('web_apps', r.getString('appId'))
        owner.set('lastError', message)
        if (!owner.getString('activeDeployment')) owner.set('status', 'failed')
        tx.save(owner)
        return
      }
    }
    // GitHub App : connexion figée dans l'instantané, identifiants de la ligne ACTUELLE (une rotation s'applique aux
    // jobs en file). Seul un déploiement qui construit des sources échoue si la connexion est absente ou désactivée ;
    // principale absente = identifiants vides comme avant (dépôts publics).
    let github: { githubAppId: string; githubInstallationId: string; privateKey: string } | null = null
    if (claimedConfig.source !== 'git') {
      try {
        const connection = webGithubConnection(resolvedConnectionId(claimedConfig), tx, { allowMissing: true })
        github = {
          githubAppId: `${connection.value.githubAppId || ''}`,
          githubInstallationId: `${connection.value.githubInstallationId || ''}`,
          privateKey: `${connection.value.privateKey || ''}`,
        }
      } catch {
        if (r.getString('action') === 'deploy' && runtimeNeedsSource(claimedSnapshot.runtime || {})) {
          const message =
            'Connexion GitHub indisponible : rattachez l’application à une connexion active, puis redéployez.'
          r.set('status', 'failed')
          r.set('error', message)
          r.set('finishedAt', new Date().toISOString())
          tx.save(r)
          const owner = tx.findRecordById('web_apps', r.getString('appId'))
          owner.set('lastError', message)
          if (!owner.getString('activeDeployment')) owner.set('status', 'failed')
          tx.save(owner)
          return
        }
        github = { githubAppId: '', githubInstallationId: '', privateKey: '' }
      }
    }
    r.set('status', 'running')
    r.set('stage', 'Récupération')
    const now = new Date().toISOString()
    r.set('startedAt', now)
    r.set('heartbeatAt', now)
    const stageKey =
      r.getString('action') === 'deploy'
        ? 'fetch'
        : ['start', 'rollback'].includes(r.getString('action'))
          ? 'start'
          : r.getString('action')
    changePhase(r, stageKey, phaseKeys[stageKey] || 'Préparation', now)
    tx.save(r)
    job = {
      id: r.id,
      appId: r.getString('appId'),
      action: r.getString('action'),
      commit: r.getString('commit'),
      snapshot: claimedSnapshot,
      ...(git ? { git } : {}),
      ...(github ? { github } : {}),
    }
  })
  if (job) {
    job.currentDesiredCommit = findWebApp(job.appId).getString('desiredCommit')
  }
  return webJson(e, { job })
}
export const HandleWebWorkerReport = (e: core.RequestEvent) => {
  requireWorker(e)
  const input = webInput(e)
  if (!/^[a-z0-9]{15}$/.test(input.id || '')) throw new BadRequestError('Déploiement invalide.')
  let response: any = { ok: true }
  $app.runInTransaction((tx) => {
    const job = tx.findRecordById('web_deployments', input.id),
      app = tx.findRecordById('web_apps', job.getString('appId'))
    if (input.inspect) {
      response = {
        status: job.getString('status'),
        desiredCommit: app.getString('desiredCommit'),
        cancelRequested: job.getBool('cancelRequested'),
        publicationStartedAt: job.getString('publicationStartedAt'),
      }
      return
    }
    if (input.runtimeStopped) {
      if (app.getString('activeDeployment') === job.id) {
        const first = app.getString('status') !== 'failed'
        app.set('status', 'failed')
        app.set('lastError', `${input.runtimeStopped}`.slice(0, 1000))
        tx.save(app)
        // Arrêt par la plateforme (cache ou disque) : une alerte par arrêt, motif fixe (le texte du worker n'est pas
        // transmis), et une copie pour le canal plateforme.
        if (first) {
          const key = 'runtime:' + job.id + ':' + new Date().toISOString().slice(0, 13)
          emitWebAlert(tx, 'app', app.id, 'runtime_stopped', key, { deploymentId: job.id, reason: 'cache_or_disk' })
          emitWebAlert(tx, 'platform', '', 'runtime_stopped', key + ':' + app.id, {
            appId: app.id,
            appName: app.getString('name').slice(0, 60),
            deploymentId: job.id,
            reason: 'cache_or_disk',
          })
        }
      }
      return
    }
    if (input.retainedImages !== undefined) {
      const imageRef = new RegExp(`^ph-web/${app.id}:[a-z0-9]{15}(?:-[a-z][a-z0-9-]{0,30})?$`)
      if (
        job.getString('status') !== 'ready' ||
        !Array.isArray(input.retainedImages) ||
        input.retainedImages.length > 128 ||
        !input.retainedImages.every(
          (value: unknown) => typeof value === 'string' && (imageRef.test(value) || /^sha256:[a-f0-9]{64}$/.test(value))
        )
      )
        throw new BadRequestError('Inventaire des images conservées invalide.')
      // Backups can pin more than three images. Reconcile the entire history in stable pages.
      for (let offset = 0; ; offset += 200) {
        const rows = tx.findRecordsByFilter('web_deployments', 'appId={:id} && status="ready"', 'id', 200, offset, {
          id: app.id,
        })
        for (const row of rows) {
          const result = JSON.parse(row.getString('result') || '{}') || {}
          if (result.image) {
            const available = input.retainedImages.includes(result.image)
            if (result.available !== available) {
              result.available = available
              row.set('result', result)
              tx.save(row)
            }
          }
        }
        if (rows.length < 200) break
      }
      return
    }
    if (job.getString('status') !== 'running') throw new BadRequestError('Déploiement non actif.')
    const now = new Date().toISOString()
    job.set('heartbeatAt', now)
    let phaseKey =
      typeof input.stageKey === 'string' && phaseKeys[input.stageKey]
        ? input.stageKey
        : Object.keys(phaseKeys).find((k) => phaseKeys[k] === input.stage) || ''
    if (input.stage === 'Publication vérifiée') phaseKey = 'publish'
    if (phaseKey === 'publish' || input.status === 'ready') {
      const promotion = decrypt(job.getString('snapshotEncrypted')).promotion
      if (promotion) assertWebEnvironmentRevision(app, promotion.expectedTargetRevision, tx)
    }
    if (job.getString('action') === 'restore-backup' && (phaseKey === 'publish' || input.status === 'ready')) {
      const restore = decrypt(job.getString('snapshotEncrypted')).restoreBackup
      assertBackupAppRevision(app, restore?.expectedAppRevision, tx)
    }
    if (phaseKey === 'publish' && ['deploy', 'ci-deploy'].includes(job.getString('action'))) {
      // A transaction arbitrates cancellation versus publication: once admitted, publication cannot be cancelled.
      if (!job.getBool('cancelRequested')) job.set('publicationStartedAt', job.getString('publicationStartedAt') || now)
      else phaseKey = ''
    }
    if (input.stage && !(job.getBool('cancelRequested') && input.stageKey === 'publish'))
      changePhase(job, phaseKey, `${input.stage}`, now)
    if (input.status) {
      if (!['ready', 'failed', 'cancelled'].includes(input.status)) throw new BadRequestError('État invalide.')
      if (
        input.status === 'ready' &&
        ['deploy', 'ci-deploy'].includes(job.getString('action')) &&
        job.getBool('cancelRequested')
      )
        throw new BadRequestError('Annulation demandée : cette version ne peut pas être publiée.')
      const status =
        ['deploy', 'ci-deploy'].includes(job.getString('action')) &&
        job.getBool('cancelRequested') &&
        input.status !== 'ready'
          ? 'cancelled'
          : input.status
      job.set('status', status)
      job.set('finishedAt', now)
      changePhase(job, '', status === 'cancelled' ? 'Annulé' : `${input.stage || job.getString('stage')}`, now, true)
      job.set('error', job.getString('status') === 'cancelled' ? '' : `${input.error || ''}`.slice(0, 1000))
      job.set('result', input.result || {})
      const action = job.getString('action')
      if (job.getString('status') === 'ready') {
        if (action === 'ci-deploy') {
          const ciSnapshot = decrypt(job.getString('snapshotEncrypted'))
          applyWebRuntimePublishedImage(app, ciSnapshot.runtime.image, tx, ciSnapshot.ci.runtimeRevision)
        }
        if (action === 'restore-backup') {
          const restored = decrypt(job.getString('snapshotEncrypted')).restoreBackup.app
          app.set('name', restored.name)
          app.set('config', restored.config)
          app.set('envEncrypted', encrypt(restored.env))
          app.set('desiredCommit', restored.desiredCommit)
          const backupState = decrypt(job.getString('snapshotEncrypted')).restoreBackup
          restoreWebEnvironmentBackup(app, backupState.projectState, tx)
          restoreWebRuntimeBackup(app, backupState.runtime, tx)
          restoreWebStorageBackup(app, backupState.storage, tx)
        }
        if (action === 'stop' || action === 'preview-remove') app.set('status', 'stopped')
        else if (!['domain', 'domains-sync'].includes(action)) {
          app.set('status', 'running')
          app.set('activeDeployment', job.id)
          app.set('lastError', '')
        }
        if (action === 'domain') {
          const snapshot = decrypt(job.getString('snapshotEncrypted')),
            domain = tx.findRecordById('web_app_domains', snapshot.domainId)
          if (domain.getString('appId') !== app.id) throw new BadRequestError('Domaine invalide.')
          domain.set('status', 'active')
          domain.set('lastError', '')
          if (input.result?.certificateExpiresAt) domain.set('certificateExpiresAt', input.result.certificateExpiresAt)
          tx.save(domain)
        }
      } else if (job.getString('status') === 'failed') {
        app.set('lastError', `${input.error || 'Échec du déploiement.'}`.slice(0, 1000))
        if (!app.getString('activeDeployment')) app.set('status', 'failed')
        if (action === 'domain') {
          const snapshot = decrypt(job.getString('snapshotEncrypted')),
            domain = tx.findRecordById('web_app_domains', snapshot.domainId)
          domain.set('lastError', `${input.error || 'Échec du certificat.'}`.slice(0, 1000))
          if (domain.getString('status') !== 'active') domain.set('status', 'error')
          tx.save(domain)
        }
      }
      tx.save(app)
    }
    if (job.getString('action') === 'ci-deploy' && ['ready', 'failed', 'cancelled'].includes(job.getString('status')))
      scrubWebCiCredentials(job)
    tx.save(job)
    response = {
      ok: true,
      status: job.getString('status'),
      desiredCommit: app.getString('desiredCommit'),
      cancelRequested: job.getBool('cancelRequested'),
      publicationStartedAt: job.getString('publicationStartedAt'),
    }
  })
  return webJson(e, response)
}

/** Renewal is queued through the same single worker; current certificates stay active on failure. */
export const HandleWebMaintenance = () => {
  if (!webEnabled()) return
  try {
    $app.findCollectionByNameOrId('web_app_domains')
  } catch {
    return
  }
  // Demande de domaine jamais prouvée : elle expire (7 jours) au lieu de garder indéfiniment le nom.
  const stale = new Date(Date.now() - 7 * 86400000).toISOString().replace('T', ' ')
  for (const row of $app.findRecordsByFilter(
    'web_app_domains',
    'status = "pending_dns" && created < {:stale}',
    'created',
    100,
    0,
    { stale }
  ))
    if (row) $app.delete(row)
  const limit = new Date(Date.now() + 30 * 86400000).toISOString().replace('T', ' ')
  const domains = $app.findRecordsByFilter(
    'web_app_domains',
    'status="active" && certificateExpiresAt != "" && certificateExpiresAt <= {:limit}',
    'certificateExpiresAt',
    10,
    0,
    { limit }
  )
  for (const domain of domains) {
    const recent = $app.findRecordsByFilter('web_deployments', 'appId={:id} && action="domain"', '-created', 20, 0, {
      id: domain.getString('appId'),
    })
    if (
      recent.some((row) => {
        const snapshot = decrypt(row.getString('snapshotEncrypted'))
        return (
          snapshot.domainId === domain.id &&
          (['queued', 'running'].includes(row.getString('status')) ||
            Date.parse(row.getString('created')) > Date.now() - 12 * 3600000)
        )
      })
    )
      continue
    queueWebDeployment(findWebApp(domain.getString('appId')), 'domain', '', {
      domainId: domain.id,
      domain: domain.getString('host'),
    })
  }
  normalizeWebDeliveryConnections()
  const old = $app.findRecordsByFilter('web_webhook_deliveries', 'created < {:date}', 'created', 500, 0, {
    date: new Date(Date.now() - 7 * 86400000).toISOString().replace('T', ' '),
  })
  for (const row of old) $app.delete(row)
}

/** Shared only by the trusted GitHub connection handlers; never serialize raw settings. */
export const readWebGithubSettings = (connectionId = 'default') =>
  webGithubConnection(connectionId, $app, { allowDisabled: true, allowMissing: true }).value
export const writeWebGithubSettings = (value: Record<string, unknown>, connectionId = 'default') =>
  writeWebGithubConnection($app, connectionId, value)
export const encryptWebValue = encrypt
export const decryptWebValue = decrypt
