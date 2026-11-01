import { canManageInstance, instanceAccessQuery } from '../../../security/instanceAccess'
export const operationsAccess = (e: core.RequestEvent) => {
  const user = e.auth
  if (!user || user.collection().name !== 'users') throw new UnauthorizedError('Connexion au dashboard requise.')
  const id = e.request!.pathValue('id')
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Instance invalide.')
  const instance = $app.findRecordById('instances', id)
  if (!canManageInstance(instance, user)) throw new ForbiddenError('Accès refusé.')
  if (user.getString('suspension') || instance.getString('suspension')) throw new ForbiddenError('Accès suspendu.')
  return { instance, user }
}
export const operationJson = (e: core.RequestEvent, value: unknown, status = 200) => {
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(status, value)
}
export const recordInstanceAction = (
  instance: core.Record,
  user: core.Record | undefined,
  action: string,
  details: Record<string, unknown> = {},
  app = $app
) => {
  const row = new Record(app.findCollectionByNameOrId('instance_action_log'))
  row.set('instanceId', instance.id)
  row.set('ownerId', instance.getString('uid'))
  row.set('actorId', user?.id || '')
  row.set('actorLabel', user?.getString('email') || 'Automatisation')
  row.set('action', action)
  row.set('details', details)
  app.save(row)
}
export const HandleInstanceActionLog = (e: core.RequestEvent) => {
  const { instance } = operationsAccess(e)
  const page = Math.max(1, Math.min(100000, Math.floor(Number(e.request!.url!.query().get('page'))) || 1))
  const items = $app.findRecordsByFilter(
    'instance_action_log',
    'instanceId = {:id}',
    '-created,-id',
    51,
    (page - 1) * 50,
    { id: instance.id }
  )
  return operationJson(e, {
    items: items.slice(0, 50).map((r) => ({
      id: r.id,
      actor: r.getString('actorLabel'),
      action: r.getString('action'),
      details: JSON.parse(r.getString('details') || '{}'),
      created: r.getString('created'),
    })),
    page,
    hasMore: items.length > 50,
  })
}
/** Records successful dashboard mutations; only an explicit metadata allowlist is retained. */
export const AuditInstanceRequest = (e: core.RequestEvent) => {
  const path = e.request!.url!.path,
    method = e.request!.method
  if (method === 'GET' || method === 'OPTIONS' || /\/operations\/consume$/.test(path)) return e.next()
  let instance: core.Record,
    action: string,
    target = ''
  if (/^\/api\/instance\/[a-z0-9]{15}(?:\/|$)/.test(path)) {
    try {
      instance = $app.findRecordById('instances', path.split('/')[3]!)
    } catch {
      return e.next()
    }
    const section = path.split('/')[4] || 'instance'
    if (['service-accounts', 'backup-checks'].includes(section)) return e.next()
    action = `${method} ${section}`
    const segments = path.split('/')
    if (section === 'backups' && segments[6]) action = `backup.${segments[6]}`
    if (/^[a-z0-9]{15}$/.test(segments[5] || '')) target = segments[5]!
  } else if (/^\/api\/instance-transfers\/[a-z0-9]{15}\/(cancel|retry|link)$/.test(path)) {
    try {
      const transfer = $app.findRecordById('instance_transfers', path.split('/')[3]!)
      instance = $app.findRecordById('instances', transfer.getString('instance'))
    } catch {
      return e.next()
    }
    action = `transfer.${path.split('/')[4]}`
    target = path.split('/')[3]!
  } else return e.next()
  const user = e.auth
  if (!user || user.collection().name !== 'users' || !canManageInstance(instance, user)) return e.next()
  const details: Record<string, unknown> = target ? { target } : {}
  if (action === 'PUT instance') {
    const input = JSON.parse(JSON.stringify(e.requestInfo().body || {})),
      fields = input.fields || {}
    details.fields = [
      'subdomain',
      'power',
      'version',
      'secrets',
      'webhooks',
      'syncAdmin',
      'autoVacuum',
      'metricsHistoryEnabled',
      'dev',
      'cname',
    ].filter((key) => fields[key] !== undefined && fields[key] !== null)
    if (typeof fields.power === 'boolean' && fields.power !== instance.getBool('power')) {
      action = fields.power ? 'instance.started' : 'instance.stopped'
      details.previousPower = instance.getBool('power')
      details.power = fields.power
    }
  }
  e.next()
  if (e.status() >= 400) return
  try {
    recordInstanceAction(instance, user, action, details)
  } catch {
    console.warn('Écriture du journal d’administration impossible.')
  }
}

export const HandleAccountActionLog = (e: core.RequestEvent) => {
  const user = e.auth
  if (!user || user.collection().name !== 'users' || user.getString('suspension'))
    throw new ForbiddenError('Accès refusé.')
  const page = Math.max(1, Math.min(100000, Math.floor(Number(e.request!.url!.query().get('page')) || 1)))
  const { filter, params } = instanceAccessQuery(user, 'instanceId', 'ownerId')
  const rows = $app
    .findRecordsByFilter('instance_action_log', filter, '-created,-id', 51, (page - 1) * 50, params)
    .filter((r): r is core.Record => !!r)
  return operationJson(e, {
    items: rows.slice(0, 50).map((r) => ({
      id: r.id,
      instanceId: r.getString('instanceId'),
      actor: r.getString('actorLabel'),
      action: r.getString('action'),
      details: JSON.parse(r.getString('details') || '{}'),
      created: r.getString('created'),
    })),
    hasMore: rows.length > 50,
    page,
  })
}
