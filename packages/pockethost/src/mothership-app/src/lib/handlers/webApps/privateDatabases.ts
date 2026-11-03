import { selectedInstanceImages } from '../../../../../common/instanceImages'
import { canManageInstance } from '../../security/instanceAccess'
import { operationsAccess, recordInstanceAction } from '../instance/api/instanceOperationsAccess'
import { readConnectionLimits } from '../operatorAdmin/connectionLimits'
import { requireWebAppPermission, webCan } from './access'
import { decryptWebValue, encryptWebValue, webDirectLoopback, webInput, webJson } from './api'
import { runtimeProfile } from './runtimes'

const COLLECTION = 'web_private_db_bindings'
const ID = /^[a-z0-9]{15}$/
const TOKEN = /^[A-Za-z0-9]{64}$/
const STATUSES = ['pending', 'ready', 'unavailable']
const ERRORS = [
  '',
  'application_stopped',
  'base_stopped',
  'authorization_revoked',
  'broker_unavailable',
  'proxy_unavailable',
  'configuration',
]
const supported = () => $os.getenv('WEB_APPS_ENABLED') === 'true' && $os.getenv('WEB_DB_BRIDGE_ENABLED') === 'true'
const stamp = () => new Date().toISOString().replace('T', ' ')
const safeId = (value: unknown) => {
  if (typeof value !== 'string' || !ID.test(value)) throw new BadRequestError('Identifiant de liaison invalide.')
  return value
}
const find = (collection: string, id: string, store: any) => {
  try {
    return store.findRecordById(collection, id) as core.Record
  } catch {
    return null
  }
}
const rows = (filter: string, params: Record<string, unknown>, store: any = $app) =>
  store.findRecordsByFilter(COLLECTION, filter, 'created,id', 500, 0, params) as core.Record[]
const activeApps = (store: any) =>
  store.findRecordsByFilter('web_apps', 'deletionId=""', 'created,id', 1000, 0) as core.Record[]
const authorization = (row: core.Record, store: any = $app) => {
  const app = find('web_apps', row.getString('appId'), store)
  const instance = find('instances', row.getString('instanceId'), store)
  const actor = find('users', row.getString('actorId'), store)
  const owner = instance ? find('users', instance.getString('uid'), store) : null
  const authorized = !!(
    app &&
    instance &&
    actor &&
    owner &&
    !owner.getString('suspension') &&
    !app.getString('deletionId') &&
    !app.getString('suspension') &&
    !instance.getString('suspension') &&
    !actor.getString('suspension') &&
    canManageInstance(instance, actor) &&
    webCan(actor, { appId: app.id }, 'app.configure', store)
  )
  return { app, instance, actor, authorized, available: authorized && !!instance?.getBool('power') }
}
const tokenOf = (row: core.Record) => {
  try {
    const value = decryptWebValue(row.getString('tokenEncrypted'))?.token
    return typeof value === 'string' && TOKEN.test(value) ? value : ''
  } catch {
    return ''
  }
}
const publicBinding = (row: core.Record, store: any = $app) => {
  const state = authorization(row, store)
  const port = row.getInt('port')
  const enabled = row.getBool('enabled')
  const age = Date.now() - Date.parse(row.getString('lastSeenAt'))
  const fresh = age >= 0 && age < 45000
  const reason = !state.authorized ? 'authorization_revoked' : !state.available ? 'base_stopped' : ''
  const status =
    !enabled || !supported() || reason ? 'unavailable' : fresh ? row.getString('status') || 'pending' : 'pending'
  return {
    id: row.id,
    appId: row.getString('appId'),
    appName: state.app?.getString('name') || 'Application supprimée',
    instanceId: row.getString('instanceId'),
    instanceName: state.instance?.getString('subdomain') || 'Base supprimée',
    port,
    enabled,
    url: `http://127.0.0.1:${port}`,
    envName: `KOLAB_DB_${port}_URL`,
    authorized: state.authorized,
    available: enabled && state.available && supported() && status === 'ready',
    status,
    lastSeenAt: row.getString('lastSeenAt'),
    errorCode: reason || (supported() ? row.getString('errorCode') : 'broker_unavailable'),
    serviceCount: row.getInt('serviceCount'),
    needsRestart: row.getBool('needsRestart'),
  }
}
const reservedPorts = (app: core.Record, store: any) => {
  const profile = runtimeProfile(app, store)
  const ports = new Set<number>([3000])
  if (profile.port) ports.add(profile.port)
  for (const service of Object.values(profile.compose?.services || {}) as any[])
    if (service.port) ports.add(service.port)
  return ports
}
const instanceState = (e: core.RequestEvent) => operationsAccess(e)
const canReadApplication = (user: core.Record, appId: string, store: any) =>
  webCan(user, { appId }, 'app.configure', store)

export const HandleInstancePrivateAccess = (e: core.RequestEvent) => {
  const { instance, user } = instanceState(e)
  const applications = supported()
    ? activeApps($app)
        .filter((app) => canReadApplication(user, app.id, $app))
        .map((app) => ({ id: app.id, name: app.getString('name') }))
    : []
  return webJson(e, {
    supported: supported(),
    enabled: supported(),
    bindings: rows('instanceId={:id}', { id: instance.id }).map((row) => publicBinding(row)),
    applications,
  })
}

export const HandleInstancePrivateAccessPut = (e: core.RequestEvent) => {
  const { instance, user } = instanceState(e)
  const input = webInput(e)
  if (Object.keys(input).some((key) => !['appId', 'port', 'enabled'].includes(key)))
    throw new BadRequestError('Options de liaison invalides.')
  const appId = safeId(input.appId)
  if (typeof input.enabled !== 'boolean') throw new BadRequestError('Activation de liaison invalide.')
  if (input.enabled && !supported()) throw new BadRequestError('Accès privé indisponible sur ce serveur.')
  let saved: core.Record | undefined
  $app.runInTransaction((tx) => {
    const actor = tx.findRecordById('users', user.id),
      base = tx.findRecordById('instances', instance.id)
    if (!canManageInstance(base, actor) || base.getString('suspension')) throw new ForbiddenError('Accès refusé.')
    if (!canReadApplication(actor, appId, tx))
      throw new ForbiddenError('Droits de configuration requis sur cette application.')
    const app = tx.findRecordById('web_apps', appId)
    const existing = rows('appId={:app} && instanceId={:base}', { app: appId, base: instance.id }, tx)[0]
    const port = input.port === undefined ? existing?.getInt('port') || 4825 : input.port
    if (!Number.isInteger(port) || port < 1024 || port > 65535 || reservedPorts(app, tx).has(port))
      throw new BadRequestError('Port local invalide ou déjà utilisé par un service de l’application.')
    if (!existing && rows('appId={:app}', { app: appId }, tx).length >= 4)
      throw new BadRequestError('Quatre liaisons privées au maximum par application.')
    if (rows('appId={:app} && port={:port} && id!={:id}', { app: appId, port, id: existing?.id || '' }, tx).length)
      throw new BadRequestError('Ce port local est déjà utilisé par une autre liaison.')
    const row = existing || new Record(tx.findCollectionByNameOrId(COLLECTION))
    row.set('appId', appId)
    row.set('instanceId', instance.id)
    row.set('actorId', actor.id)
    row.set('port', port)
    row.set('enabled', input.enabled)
    row.set('revision', $security.randomString(24))
    row.set('tokenEncrypted', input.enabled ? encryptWebValue({ token: $security.randomString(64) }) : '')
    row.set('status', input.enabled ? 'pending' : 'unavailable')
    row.set('errorCode', '')
    row.set('lastSeenAt', '')
    row.set('serviceCount', 0)
    row.set('needsRestart', input.enabled)
    tx.save(row)
    saved = row
    recordInstanceAction(
      base,
      actor,
      input.enabled ? 'private_access.enabled' : 'private_access.disabled',
      { appId, bindingId: row.id, port },
      tx
    )
  })
  return webJson(e, { binding: publicBinding(saved!), restartRequired: input.enabled }, 200)
}

const deleteBinding = (e: core.RequestEvent, instanceSide: boolean) => {
  const bindingId = safeId(e.request!.pathValue('bindingId'))
  let targetId = ''
  if (instanceSide) targetId = instanceState(e).instance.id
  else targetId = requireWebAppPermission(e, 'app.configure').app!.id
  const row = $app.findRecordById(COLLECTION, bindingId)
  if (row.getString(instanceSide ? 'instanceId' : 'appId') !== targetId) throw new NotFoundError('Liaison introuvable.')
  // Each resource's manager may revoke even after losing access to the other side.
  $app.delete(row)
  return webJson(e, { removed: true })
}
export const HandleInstancePrivateAccessDelete = (e: core.RequestEvent) => deleteBinding(e, true)
export const HandleWebPrivateDatabasesDelete = (e: core.RequestEvent) => deleteBinding(e, false)
export const HandleWebPrivateDatabases = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'app.read').app!
  return webJson(e, {
    supported: supported(),
    enabled: supported(),
    bindings: rows('appId={:id}', { id: app.id }).map((row) => publicBinding(row)),
  })
}
const privateAuth = (e: core.RequestEvent, broker = false) => {
  const expected = $os.getenv(broker ? 'WEB_DB_BRIDGE_SECRET' : 'WEB_APPS_WORKER_SECRET')
  const supplied = e.request!.header.get(broker ? 'X-DB-Bridge' : 'X-Web-Worker')
  if (expected.length < 32 || !$security.equal(expected, supplied || ''))
    throw new UnauthorizedError('Authentification du service requise.')
  if (!webDirectLoopback(e)) throw new ForbiddenError('Connexion locale directe requise.')
}
const workerRows = (store: any = $app) => {
  if (!supported()) return []
  return rows('enabled=true', {}, store).flatMap((row) => {
    const grant = authorization(row, store),
      token = tokenOf(row)
    if (!grant.authorized || !token) return []
    const port = row.getInt('port')
    try {
      if (port < 1024 || port > 65535 || reservedPorts(grant.app!, store).has(port)) return []
    } catch {
      return []
    }
    return [
      {
        row,
        grant,
        token,
        id: row.id,
        appId: row.getString('appId'),
        instanceId: row.getString('instanceId'),
        port,
        revision: row.getString('revision'),
      },
    ]
  })
}
type TargetState = { available: boolean; targetSocket: string; targetContainerId: string; targetEpoch: string }
/** The static controller reconciles only trusted namespace sidecars; no published port is ever a target. */
const targetStates = (grants: ReturnType<typeof workerRows>) => {
  const empty = {} as Record<string, TargetState>
  if (!supported()) return empty
  const inferred = `${$app.dataDir()}`.replace(/\/mothership\/pb_data\/?$/, '')
  const root = $os.getenv('DATA_ROOT') || (inferred !== `${$app.dataDir()}` ? inferred : '')
  const image = $os.getenv('WEB_DB_TARGET_IMAGE')
  if (!root || !/^[a-zA-Z0-9][a-zA-Z0-9./_:-]*@sha256:[a-f0-9]{64}$/.test(image)) return empty
  const ids = [...new Set(grants.filter((g) => g.grant.available).map((g) => g.instanceId))]
  try {
    const output = JSON.parse(
      toString(
        $os
          .cmd(
            'python3',
            $os.getenv('WEB_DB_PROVIDER_SCRIPT') || '/opt/pockethost-web/web_private_db_provider.py',
            JSON.stringify({
              dataRoot: root,
              databaseImage: $os.getenv('DOCKER_INSTANCE_IMAGE_NAME') || 'benallfree/pockethost-instance',
              databaseImages: selectedInstanceImages(ids, $os.getenv('PH_INSTANCE_IMAGE_OVERRIDES')),
              targetImage: image,
              ids,
              connectionLimits: readConnectionLimits(),
              ...($os.getenv('WEB_DB_TARGET_ROOT') ? { targetRoot: $os.getenv('WEB_DB_TARGET_ROOT') } : {}),
              ...($os.getenv('WEB_DB_TARGET_SCRIPT') ? { targetScript: $os.getenv('WEB_DB_TARGET_SCRIPT') } : {}),
            })
          )
          .combinedOutput()
      )
    ) as Record<string, TargetState>
    const result = {} as Record<string, TargetState>
    for (const id of ids) {
      const target = output[id]
      if (
        target &&
        target.available === true &&
        /^[a-f0-9]{64}$/.test(target.targetContainerId) &&
        /^[a-f0-9]{64}$/.test(target.targetEpoch)
      ) {
        const directory = $security.sha256(`${target.targetContainerId}:${target.targetEpoch}`).slice(0, 32)
        const expected = `${$os.getenv('WEB_DB_TARGET_ROOT') || '/run/kolab-db-targets'}/${id}/${directory}/t.sock`
        if (target.targetSocket === expected) result[id] = target
      }
    }
    return result
  } catch {
    return empty
  }
}
export const HandleWebPrivateDatabaseWorker = (e: core.RequestEvent) => {
  privateAuth(e)
  const grants = workerRows(),
    targets = targetStates(grants)
  return webJson(e, {
    version: 1,
    limits: readConnectionLimits(),
    enabled: supported(),
    applications: supported() ? activeApps($app).map((app) => app.id) : [],
    bindings: grants.map(({ row, grant, ...item }) => ({
      ...item,
      databaseRunning: grant.available,
      available: grant.available && !!targets[item.instanceId]?.available,
      url: `http://127.0.0.1:${item.port}`,
      envName: `KOLAB_DB_${item.port}_URL`,
    })),
  })
}
export const HandleWebPrivateDatabaseBroker = (e: core.RequestEvent) => {
  privateAuth(e, true)
  const grants = workerRows(),
    targets = targetStates(grants)
  return webJson(e, {
    version: 1,
    limits: readConnectionLimits(),
    enabled: supported(),
    applications: supported() ? activeApps($app).map((app) => app.id) : [],
    bindings: grants.map(({ row, grant, token, ...item }) => {
      const target = grant.available ? targets[item.instanceId] : undefined
      return {
        ...item,
        tokenHash: $security.sha256(token),
        available: !!target?.available,
        targetSocket: target?.targetSocket || null,
        targetContainerId: target?.targetContainerId || null,
        targetEpoch: target?.targetEpoch || null,
      }
    }),
  })
}
export const HandleWebPrivateDatabaseStatus = (e: core.RequestEvent) => {
  privateAuth(e)
  const input = webInput(e)
  if (Object.keys(input).some((k) => k !== 'bindings') || !Array.isArray(input.bindings) || input.bindings.length > 500)
    throw new BadRequestError('Compte rendu de liaisons invalide.')
  for (const item of input.bindings) {
    if (
      !item ||
      Object.keys(item).some(
        (k) => !['id', 'appId', 'revision', 'status', 'errorCode', 'serviceCount', 'needsRestart'].includes(k)
      ) ||
      typeof item.id !== 'string' ||
      !ID.test(item.id) ||
      typeof item.appId !== 'string' ||
      !ID.test(item.appId) ||
      typeof item.revision !== 'string' ||
      !/^[A-Za-z0-9]{24}$/.test(item.revision) ||
      typeof item.status !== 'string' ||
      !STATUSES.includes(item.status) ||
      (item.errorCode !== undefined && typeof item.errorCode !== 'string') ||
      !ERRORS.includes(item.errorCode || '') ||
      (item.serviceCount !== undefined &&
        (!Number.isInteger(item.serviceCount) || item.serviceCount < 0 || item.serviceCount > 8)) ||
      (item.needsRestart !== undefined && typeof item.needsRestart !== 'boolean')
    )
      throw new BadRequestError('État de liaison invalide.')
  }
  let updated = 0
  $app.runInTransaction((tx) => {
    for (const item of input.bindings) {
      const row = find(COLLECTION, item.id, tx)
      if (
        !row ||
        !row.getBool('enabled') ||
        row.getString('appId') !== item.appId ||
        row.getString('revision') !== item.revision ||
        !authorization(row, tx).authorized
      )
        continue
      row.set('status', item.status)
      row.set('errorCode', item.errorCode || '')
      row.set('lastSeenAt', stamp())
      row.set('serviceCount', item.serviceCount || 0)
      row.set('needsRestart', !!item.needsRestart)
      tx.save(row)
      updated++
    }
  })
  return webJson(e, { ok: true, updated })
}
