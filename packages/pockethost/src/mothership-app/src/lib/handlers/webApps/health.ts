import { requireWebAppPermission, webConfig, webDirectLoopback, webEnabled, webInput, webJson } from './api'

const CONFIG = 'web_app_health',
  EVENTS = 'web_app_health_events',
  MAX_EVENTS = 1000
const id = /^[a-z0-9]{15}$/
const PUBLIC_ERRORS = [
  '',
  'not_checked',
  'public_dns',
  'public_address_refused',
  'public_timeout',
  'public_connection',
  'public_tls',
  'public_http_error',
]
const PRIVATE_SUFFIX = /\.(local|internal|localhost|lan|home|invalid|test|example)$/
const ipLiteral = (host: string) => /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(':')
/**
 * Hôte public sondé par le worker en HTTPS:443 (DNS, TLS et proxy vus depuis Internet) : domaine personnalisé actif
 * vérifié, sinon domaine généré. Vide quand l'hôte n'est pas public, ou quand le serveur est une installation locale
 * (WEB_HEALTH_PUBLIC_CHECK=false) : la sonde rendrait sinon « adresse privée refusée », donc « dégradé ».
 */
const publicHostOf = (appId: string, store = $app) => {
  if ($os.getenv('WEB_HEALTH_PUBLIC_CHECK') === 'false') return ''
  let host = ''
  try {
    const domain = store.findRecordsByFilter('web_app_domains', 'appId={:id} && status="active"', 'created', 1, 0, {
      id: appId,
    })[0]
    host = domain?.getString('host') || ''
  } catch {}
  if (!host)
    try {
      host = webConfig(store.findRecordById('web_apps', appId)).generatedDomain || ''
    } catch {}
  host = `${host}`.toLowerCase().replace(/\.$/, '').replace(/:\d+$/, '')
  if (
    !/^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host) ||
    PRIVATE_SUFFIX.test(host) ||
    ipLiteral(host)
  )
    return ''
  return host
}
const pathSafe = (value: unknown): value is string =>
  typeof value === 'string' &&
  value.length <= 512 &&
  /^\/[A-Za-z0-9/_~.-]*$/.test(value) &&
  !value.includes('//') &&
  !value.split('/').some((part) => part === '.' || part === '..')
const defaults = (app: core.Record) => {
  const existing = webConfig(app).healthPath
  return {
    enabled: false,
    path: pathSafe(existing) ? existing : '/',
    intervalSeconds: 60,
    timeoutSeconds: 5,
    failureThreshold: 3,
  }
}
const record = (appId: string, store = $app) =>
  store.findRecordsByFilter(CONFIG, 'appId={:id}', '', 1, 0, { id: appId })[0]
const config = (app: core.Record, row?: core.Record) =>
  row
    ? {
        enabled: row.getBool('enabled'),
        path: row.getString('path'),
        intervalSeconds: row.getInt('intervalSeconds'),
        timeoutSeconds: row.getInt('timeoutSeconds'),
        failureThreshold: row.getInt('failureThreshold'),
        revision: row.getString('revision'),
      }
    : { ...defaults(app), revision: '' }
const snapshot = (app: core.Record, row?: core.Record) => {
  const cfg = config(app, row),
    checkedAt = row?.getString('lastCheckedAt') || ''
  const sameDeployment = row?.getString('deploymentId') === app.getString('activeDeployment')
  const reference = sameDeployment
    ? checkedAt || row?.getString('statusSince') || ''
    : app.getString('updated') || row?.getString('statusSince') || ''
  const stale =
    !!reference && Date.now() - Date.parse(reference) > cfg.intervalSeconds * 2000 + cfg.timeoutSeconds * 1000 + 30000
  let background = false
  try {
    const profile = $app.findRecordsByFilter('web_runtime_profiles', 'appId={:id}', '', 1, 0, { id: app.id })[0]
    background = !!profile && JSON.parse(profile.getString('profile') || '{}').kind === 'worker'
  } catch {}
  const status = background
    ? 'not_applicable'
    : !cfg.enabled
      ? 'disabled'
      : app.getString('status') !== 'running'
        ? 'paused'
        : !sameDeployment || !checkedAt
          ? stale
            ? 'stale'
            : 'unknown'
          : stale
            ? 'stale'
            : row?.getString('status') || 'unknown'
  return {
    config: cfg,
    state: {
      status,
      checkedAt: sameDeployment ? checkedAt : '',
      statusSince: sameDeployment ? row?.getString('statusSince') || '' : '',
      httpStatus: sameDeployment ? row?.getInt('httpStatus') || 0 : 0,
      durationMs: sameDeployment ? row?.getInt('durationMs') || 0 : 0,
      consecutiveFailures: sameDeployment ? row?.getInt('consecutiveFailures') || 0 : 0,
      lastResult: sameDeployment ? row?.getString('lastResult') || '' : '',
    },
    retention: { days: 30, maxEvents: MAX_EVENTS },
    automaticRecovery: false,
  }
}
const requireWorker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const secret = $os.getenv('WEB_APPS_WORKER_SECRET')
  if (secret.length < 32 || !$security.equal(secret, e.request!.header.get('X-Web-Worker') || ''))
    throw new UnauthorizedError('Contrôle de santé non autorisé.')
  if (!webDirectLoopback(e)) throw new ForbiddenError('Accès local requis.')
}
const publication = (appId: string, store = $app) => {
  try {
    const app = store.findRecordById('web_apps', appId),
      deploymentId = app.getString('activeDeployment')
    if (app.getString('status') !== 'running' || !id.test(deploymentId)) return null
    const deployment = store.findRecordById('web_deployments', deploymentId),
      result = JSON.parse(deployment.getString('result') || '{}')
    if (!result || typeof result !== 'object' || Array.isArray(result)) return null
    const target = /^http:\/\/127\.0\.0\.1:([1-9][0-9]{3,4})$/.exec(result.target || '')
    if (
      deployment.getString('appId') !== appId ||
      deployment.getString('status') !== 'ready' ||
      result.available === false ||
      result.container !== `ph-web-${appId}-${deploymentId}` ||
      !target ||
      Number(target[1]) < 1024 ||
      Number(target[1]) > 65535
    )
      return null
    return { deploymentId, containerName: result.container, expectedTarget: result.target }
  } catch {
    return null
  }
}
const event = (row: core.Record, previous: string, reason: string, store = $app, actorId = '') => {
  const item = new Record(store.findCollectionByNameOrId(EVENTS))
  for (const key of ['appId', 'deploymentId', 'httpStatus', 'durationMs', 'consecutiveFailures'])
    item.set(key, row.get(key))
  item.set('actorId', actorId)
  item.set('fromStatus', previous)
  item.set('status', row.getString('status'))
  item.set('reason', reason)
  item.set('checkedAt', row.getString('lastCheckedAt') || new Date().toISOString())
  store.save(item)
  const appId = row.getString('appId'),
    cutoff = new Date(Date.now() - 30 * 86400000).toISOString().replace('T', ' ')
  for (const old of store.findRecordsByFilter(EVENTS, 'appId={:id} && checkedAt < {:cutoff}', '', MAX_EVENTS, 0, {
    id: appId,
    cutoff,
  }))
    store.delete(old)
  for (const old of store.findRecordsByFilter(EVENTS, 'appId={:id}', '-created,-id', MAX_EVENTS, MAX_EVENTS, {
    id: appId,
  }))
    store.delete(old)
}
export const HandleWebHealthGet = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'app.read').app!
  return webJson(e, snapshot(app, record(app.id)))
}
export const HandleWebHealthUpdate = (e: core.RequestEvent) => {
  const { user: actor, app: found } = requireWebAppPermission(e, 'health.manage'),
    app = found!,
    input = webInput(e)
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).some(
      (key) => !['enabled', 'path', 'intervalSeconds', 'timeoutSeconds', 'failureThreshold', 'revision'].includes(key)
    )
  )
    throw new BadRequestError('Configuration de surveillance invalide.')
  $app.runInTransaction((tx) => {
    let row = record(app.id, tx)
    if (input.revision !== undefined && input.revision !== (row?.getString('revision') || ''))
      throw new BadRequestError('La configuration a changé. Rechargez cette page.')
    const cfg: any = { ...config(app, row), ...input }
    if (typeof cfg.enabled !== 'boolean' || !pathSafe(cfg.path))
      throw new BadRequestError('Indiquez un chemin local sans paramètres, par exemple /api/health.')
    for (const [key, min, max] of [
      ['intervalSeconds', 60, 3600],
      ['timeoutSeconds', 1, 10],
      ['failureThreshold', 1, 10],
    ] as const)
      if (!Number.isInteger(cfg[key]) || cfg[key] < min || cfg[key] > max)
        throw new BadRequestError('Intervalle, délai ou seuil de santé invalide.')
    const previous = row?.getString('status') || 'disabled'
    if (!row) {
      row = new Record(tx.findCollectionByNameOrId(CONFIG))
      row.set('appId', app.id)
    }
    for (const key of ['enabled', 'path', 'intervalSeconds', 'timeoutSeconds', 'failureThreshold'])
      row.set(key, cfg[key])
    // Correction du chemin pendant un incident : le rétablissement reste dû. Désactiver la surveillance l'annule.
    row.set('alertState', cfg.enabled ? row.getString('alertState') : '')
    row.set('revision', $security.randomString(24))
    row.set('status', cfg.enabled ? 'unknown' : 'disabled')
    row.set('statusSince', new Date().toISOString())
    for (const key of ['deploymentId', 'containerId', 'lastCheckedAt', 'lastResult']) row.set(key, '')
    for (const key of ['consecutiveFailures', 'httpStatus', 'durationMs']) row.set(key, 0)
    tx.save(row)
    event(row, previous, cfg.enabled ? 'configuration_changed' : 'disabled', tx, actor.id)
  })
  return webJson(e, snapshot(app, record(app.id)))
}
export const HandleWebHealthHistory = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'app.read').app!,
    query = e.request!.url!.query()
  const page = Math.max(1, Math.min(10000, Math.floor(Number(query.get('page')) || 1))),
    perPage = Math.max(1, Math.min(100, Math.floor(Number(query.get('perPage')) || 20)))
  const rows = $app.findRecordsByFilter(EVENTS, 'appId={:id}', '-created,-id', perPage, (page - 1) * perPage, {
      id: app.id,
    }),
    totalItems = $app.countRecords(EVENTS, $dbx.exp('appId={:id}', { id: app.id }))
  return webJson(e, {
    items: rows.map((row) => ({
      id: row.id,
      deploymentId: row.getString('deploymentId'),
      fromStatus: row.getString('fromStatus'),
      status: row.getString('status'),
      reason: row.getString('reason'),
      checkedAt: row.getString('checkedAt'),
      httpStatus: row.getInt('httpStatus'),
      durationMs: row.getInt('durationMs'),
      consecutiveFailures: row.getInt('consecutiveFailures'),
    })),
    page,
    perPage,
    totalItems,
    totalPages: Math.ceil(totalItems / perPage),
    hasMore: page * perPage < totalItems,
  })
}
export const HandleWebHealthConfig = (e: core.RequestEvent) => {
  requireWorker(e)
  const input = webInput(e),
    page = Math.max(1, Math.min(10000, Math.floor(Number(input.page) || 1))),
    perPage = 100
  const rows = $app.findRecordsByFilter(CONFIG, 'enabled=true', 'appId', perPage + 1, (page - 1) * perPage)
  const items: any[] = []
  for (const row of rows.slice(0, perPage)) {
    try {
      const current = publication(row.getString('appId'))
      if (!current) continue
      items.push({
        appId: row.getString('appId'),
        ...current,
        path: row.getString('path'),
        intervalSeconds: row.getInt('intervalSeconds'),
        timeoutSeconds: row.getInt('timeoutSeconds'),
        failureThreshold: row.getInt('failureThreshold'),
        revision: row.getString('revision'),
        publicHost: publicHostOf(row.getString('appId')),
      })
    } catch {}
  }
  return webJson(e, { items, page, hasMore: rows.length > perPage })
}
export const HandleWebHealthReport = (e: core.RequestEvent) => {
  requireWorker(e)
  const input = webInput(e)
  if (
    typeof input.appId !== 'string' ||
    !id.test(input.appId) ||
    typeof input.deploymentId !== 'string' ||
    !id.test(input.deploymentId) ||
    typeof input.revision !== 'string' ||
    !/^[A-Za-z0-9]{24}$/.test(input.revision) ||
    !['', 'http_error', 'timeout', 'connection_error', 'container_unavailable', 'target_mismatch'].includes(
      input.errorCode
    ) ||
    !Number.isInteger(input.statusCode) ||
    (input.statusCode !== 0 && input.statusCode < 100) ||
    input.statusCode > 599 ||
    !Number.isInteger(input.durationMs) ||
    input.durationMs < 0 ||
    input.durationMs > 120000 ||
    typeof input.checkedAt !== 'string' ||
    typeof input.containerId !== 'string' ||
    (input.containerId !== '' && !/^[a-f0-9]{64}$/.test(input.containerId))
  )
    throw new BadRequestError('Résultat de santé invalide.')
  // Sonde publique facultative (domaine réel en HTTPS:443) : champs absents des anciens workers.
  if (
    (input.publicHost !== undefined && (typeof input.publicHost !== 'string' || input.publicHost.length > 253)) ||
    (input.publicStatusCode !== undefined &&
      (!Number.isInteger(input.publicStatusCode) || input.publicStatusCode < 0 || input.publicStatusCode > 599)) ||
    (input.publicDurationMs !== undefined &&
      (!Number.isInteger(input.publicDurationMs) || input.publicDurationMs < 0 || input.publicDurationMs > 120000)) ||
    (input.publicErrorCode !== undefined && !PUBLIC_ERRORS.includes(input.publicErrorCode))
  )
    throw new BadRequestError('Résultat de santé invalide.')
  const publicError =
    typeof input.publicErrorCode === 'string' && input.publicErrorCode !== 'not_checked' ? input.publicErrorCode : ''
  const checked = Date.parse(input.checkedAt)
  if (!Number.isFinite(checked) || checked > Date.now() + 5000 || checked < Date.now() - 5 * 60000)
    throw new BadRequestError('Date de contrôle invalide.')
  let accepted = false
  $app.runInTransaction((tx) => {
    const row = record(input.appId, tx),
      current = publication(input.appId, tx)
    if (
      !row?.getBool('enabled') ||
      row.getString('revision') !== input.revision ||
      !current ||
      current.deploymentId !== input.deploymentId
    )
      return
    if (row.getString('deploymentId') === input.deploymentId && Date.parse(row.getString('lastCheckedAt')) >= checked)
      return
    let previous = row.getString('status') || 'unknown'
    // Une alerte « Site rétabli » est due tant qu'un « Site indisponible » n'a pas été suivi d'un succès, même après un
    // redéploiement ou un changement de configuration (le statut repart alors de « unknown »).
    const owed = row.getString('alertState') === 'degraded'
    if (row.getString('deploymentId') !== input.deploymentId) {
      row.set('consecutiveFailures', 0)
      row.set('statusSince', new Date(checked).toISOString())
      previous = 'unknown'
    }
    const success = !input.errorCode && input.statusCode >= 200 && input.statusCode < 400 && !!input.containerId
    const failures = success ? 0 : Math.min(100000, row.getInt('consecutiveFailures') + 1)
    const next = success ? 'healthy' : failures >= row.getInt('failureThreshold') ? 'degraded' : previous
    row.set('deploymentId', input.deploymentId)
    row.set('containerId', input.containerId)
    row.set('consecutiveFailures', failures)
    row.set('status', next)
    row.set('lastCheckedAt', new Date(checked).toISOString())
    row.set('httpStatus', input.statusCode)
    row.set('durationMs', input.durationMs)
    // Échec vu depuis Internet (DNS, TLS, proxy) : son code précis remplace le code agrégé du worker.
    row.set('lastResult', success ? 'ok' : publicError || input.errorCode || 'http_error')
    if (next !== previous || !row.getString('statusSince')) row.set('statusSince', new Date(checked).toISOString())
    if (next === 'degraded') row.set('alertState', 'degraded')
    const recovered = success && owed
    if (recovered) row.set('alertState', '')
    tx.save(row)
    if (next !== previous || recovered)
      event(
        row,
        recovered ? 'degraded' : previous,
        success
          ? recovered || previous === 'degraded'
            ? 'recovered'
            : 'healthy'
          : publicError && next === 'degraded'
            ? publicError
            : 'failure_threshold',
        tx
      )
    accepted = true
  })
  return webJson(e, { accepted })
}
export const HandleWebHealthMaintenance = () => {
  if (!webEnabled()) return
  try {
    $app.findCollectionByNameOrId(EVENTS)
  } catch {
    return
  }
  const cutoff = new Date(Date.now() - 30 * 86400000).toISOString().replace('T', ' ')
  for (const row of $app.findRecordsByFilter(EVENTS, 'checkedAt < {:cutoff}', 'checkedAt', 500, 0, { cutoff }))
    $app.delete(row)
}
