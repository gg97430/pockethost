import { requireWebAppPermission, webActorCan, webDirectLoopback, webEnabled, webInput, webJson } from './api'

const COLLECTION = 'web_terminal_sessions'
const stamp = () => new Date().toISOString()
const closedReasons = [
  'disconnected',
  'idle',
  'expired',
  'revoked',
  'container_changed',
  'access_revoked',
  'connect_failed',
  'shell_exited',
  'broker_shutdown',
  'protocol_error',
]
const active = (row: core.Record, now = Date.now()) => {
  if (!['issued', 'connecting', 'open'].includes(row.getString('status'))) return false
  if (Date.parse(row.getString('absoluteExpiresAt')) <= now) return false
  if (row.getString('status') === 'issued') return Date.parse(row.getString('expiresAt')) > now
  return Date.parse(row.getString('lastSeenAt')) > now - 90000
}
const close = (row: core.Record, reason: string, store = $app) => {
  if (row.getString('status') === 'closed') return
  row.set('status', 'closed')
  row.set('reason', closedReasons.includes(reason) ? reason : 'connect_failed')
  row.set('closedAt', stamp())
  row.set('grantHash', '')
  row.set('leaseHash', '')
  store.save(row)
}
const currentPublication = (appId: string, deploymentId = '', store = $app) => {
  const app = store.findRecordById('web_apps', appId)
  const id = app.getString('activeDeployment')
  if (app.getString('status') !== 'running' || !/^[a-z0-9]{15}$/.test(id) || (deploymentId && deploymentId !== id))
    throw new BadRequestError('Le terminal nécessite une version publiée et en fonctionnement.')
  const deployment = store.findRecordById('web_deployments', id)
  const result = JSON.parse(deployment.getString('result') || '{}')
  const expected = `ph-web-${appId}-${id}`
  if (
    deployment.getString('appId') !== appId ||
    deployment.getString('status') !== 'ready' ||
    result.container !== expected
  )
    throw new BadRequestError('Le conteneur publié ne permet pas l’ouverture du terminal.')
  return { app, deployment, containerName: expected }
}
/** Revalidation périodique (broker) : l'auteur doit encore pouvoir ouvrir un terminal sur le projet actuel. */
const validateActor = (row: core.Record, store = $app) => {
  if (!webActorCan(row.getString('actorId'), { appId: row.getString('appId') }, 'terminal.open', store))
    throw new ForbiddenError('Accès au terminal révoqué.')
}
const requireBroker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const expected = $os.getenv('WEB_APPS_WORKER_SECRET'),
    received = e.request!.header.get('X-Web-Worker') || ''
  if (expected.length < 32 || !$security.equal(expected, received))
    throw new UnauthorizedError('Terminal non autorisé.')
  if (!webDirectLoopback(e)) throw new ForbiddenError('Accès local requis.')
}
const sessionId = (value: unknown) => {
  if (typeof value !== 'string' || !/^[a-z0-9]{15}$/.test(value)) throw new BadRequestError('Session invalide.')
  return value
}
const lease = (input: any, store = $app) => {
  const row = store.findRecordById(COLLECTION, sessionId(input.sessionId))
  if (
    typeof input.proof !== 'string' ||
    !/^[A-Za-z0-9]{80}$/.test(input.proof) ||
    !row.getString('leaseHash') ||
    !$security.equal(row.getString('leaseHash'), $security.sha256(input.proof))
  )
    throw new UnauthorizedError('Session de terminal invalide.')
  return row
}
const metadata = (row: core.Record) => ({
  id: row.id,
  actorId: row.getString('actorId'),
  status: row.getString('status'),
  shell: row.getString('shell'),
  created: row.getString('created'),
  openedAt: row.getString('openedAt'),
  closedAt: row.getString('closedAt'),
  reason: row.getString('reason'),
})
export const HandleWebTerminalSession = (e: core.RequestEvent) => {
  const { user: actor, app: found } = requireWebAppPermission(e, 'terminal.open'),
    app = found!,
    input = webInput(e)
  if (Object.keys(input).some((key) => key !== 'shell')) throw new BadRequestError('Options de terminal invalides.')
  const shell =
    input.shell === undefined || input.shell === 'sh' ? '/bin/sh' : input.shell === 'bash' ? '/bin/bash' : ''
  if (!shell) throw new BadRequestError('Choisissez sh ou bash.')
  const token = $security.randomString(80)
  let rowId = '',
    expiresAt = ''
  $app.runInTransaction((tx) => {
    const publication = currentPublication(app.id, '', tx)
    const rows = tx.findRecordsByFilter(COLLECTION, 'status != "closed"', 'created', 100, 0)
    const live: core.Record[] = []
    // Une session `issued` non réclamée sort d'active() à expiresAt (60 s) : elle ne compte dans aucun plafond.
    for (const row of rows) {
      if (active(row)) live.push(row)
      else close(row, 'expired', tx)
    }
    if (live.length >= 16) throw new BadRequestError('Seize terminaux sont déjà ouverts sur ce serveur.')
    if (live.filter((r) => r.getString('appId') === app.id).length >= 2)
      throw new BadRequestError(
        'Deux terminaux sont déjà ouverts sur cette application. Fermez une session avant de continuer.'
      )
    if (live.filter((r) => r.getString('actorId') === actor.id).length >= 2)
      throw new BadRequestError(
        'Deux terminaux sont déjà ouverts pour ce compte. Fermez une session avant de continuer.'
      )
    const row = new Record(tx.findCollectionByNameOrId(COLLECTION))
    expiresAt = new Date(Date.now() + 60000).toISOString()
    row.set('appId', app.id)
    row.set('actorId', actor.id)
    row.set('deploymentId', publication.deployment.id)
    row.set('containerName', publication.containerName)
    row.set('grantHash', $security.sha256(token))
    row.set('shell', shell)
    row.set('status', 'issued')
    row.set('expiresAt', expiresAt)
    row.set('absoluteExpiresAt', new Date(Date.now() + 30 * 60000).toISOString())
    tx.save(row)
    rowId = row.id
  })
  return webJson(
    e,
    {
      sessionId: rowId,
      token,
      expiresAt,
      wsPath: '/api/web-terminal/connect',
      shell,
      idleTimeoutSeconds: 600,
      maxDurationSeconds: 1800,
    },
    201
  )
}
export const HandleWebTerminalClaim = (e: core.RequestEvent) => {
  requireBroker(e)
  const input = webInput(e)
  if (typeof input.token !== 'string' || !/^[A-Za-z0-9]{80}$/.test(input.token))
    throw new UnauthorizedError('Autorisation de terminal invalide.')
  let result: any
  $app.runInTransaction((tx) => {
    const rows = tx.findRecordsByFilter(COLLECTION, 'grantHash = {:hash}', '', 1, 0, {
      hash: $security.sha256(input.token),
    })
    const row = rows[0]
    if (!row || row.getString('status') !== 'issued' || !active(row))
      throw new UnauthorizedError('Cette autorisation a expiré ou a déjà été utilisée.')
    validateActor(row, tx)
    currentPublication(row.getString('appId'), row.getString('deploymentId'), tx)
    const proof = $security.randomString(80)
    row.set('grantHash', '')
    row.set('leaseHash', $security.sha256(proof))
    row.set('status', 'connecting')
    row.set('lastSeenAt', stamp())
    tx.save(row)
    result = {
      sessionId: row.id,
      appId: row.getString('appId'),
      actorId: row.getString('actorId'),
      deploymentId: row.getString('deploymentId'),
      containerName: row.getString('containerName'),
      shell: row.getString('shell'),
      proof,
      absoluteExpiresAt: row.getString('absoluteExpiresAt'),
    }
  })
  return webJson(e, result)
}
export const HandleWebTerminalValidate = (e: core.RequestEvent) => {
  requireBroker(e)
  const input = webInput(e)
  let result: any = { valid: false }
  $app.runInTransaction((tx) => {
    const row = lease(input, tx)
    if (!active(row)) {
      close(row, 'expired', tx)
      return
    }
    try {
      validateActor(row, tx)
    } catch {
      close(row, 'access_revoked', tx)
      return
    }
    try {
      currentPublication(row.getString('appId'), row.getString('deploymentId'), tx)
    } catch {
      close(row, 'container_changed', tx)
      return
    }
    if (typeof input.containerId !== 'string' || !/^[a-f0-9]{64}$/.test(input.containerId))
      throw new BadRequestError('Conteneur invalide.')
    if (row.getString('containerId') && row.getString('containerId') !== input.containerId) {
      close(row, 'container_changed', tx)
      return
    }
    row.set('containerId', input.containerId)
    row.set('lastSeenAt', stamp())
    if (!row.getString('openedAt')) {
      row.set('openedAt', stamp())
      row.set('status', 'open')
    }
    tx.save(row)
    result = { valid: true, absoluteExpiresAt: row.getString('absoluteExpiresAt') }
  })
  return webJson(e, result)
}
export const HandleWebTerminalClose = (e: core.RequestEvent) => {
  requireBroker(e)
  const input = webInput(e)
  $app.runInTransaction((tx) => {
    const row = lease(input, tx)
    close(row, input.reason, tx)
  })
  return webJson(e, { closed: true })
}
export const HandleWebTerminalHistory = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'terminal.open').app!
  const rows = $app.findRecordsByFilter(COLLECTION, 'appId={:id}', '-created', 30, 0, { id: app.id })
  return webJson(e, { items: rows.map(metadata) })
}
export const HandleWebTerminalRevoke = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'terminal.open').app!
  const row = $app.findRecordById(COLLECTION, sessionId(e.request!.pathValue('sessionId')))
  if (row.getString('appId') !== app.id) throw new ForbiddenError('Session invalide.')
  close(row, 'revoked')
  return webJson(e, { closed: true })
}
export const HandleWebTerminalMaintenance = () => {
  if (!webEnabled()) return
  try {
    $app.findCollectionByNameOrId(COLLECTION)
  } catch {
    return
  }
  const rows = $app.findRecordsByFilter(COLLECTION, 'status != "closed"', 'created', 200, 0)
  for (const row of rows) if (!active(row)) close(row, 'expired')
  const cutoff = new Date(Date.now() - 30 * 86400000).toISOString().replace('T', ' ')
  for (const row of $app.findRecordsByFilter(COLLECTION, 'status = "closed" && closedAt < {:cutoff}', '', 500, 0, {
    cutoff,
  }))
    $app.delete(row)
}
