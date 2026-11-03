import { instancePublicHostname } from '../../../../../../common/instanceHostnames'
import { canManageInstance } from '../../../security/instanceAccess'
import { migratedNativeAdmin } from '../../../security/migratedNativeAdmin'
import { instanceHostnameView } from '../hostnames'
const TICKETS = 'ph_console_auth_tickets'
type ConsoleTicket = { instance: string; owner: string; user: string; authKey: string; expiresAt: number }
type Tickets = Record<string, ConsoleTicket>
const decode = (raw: unknown): Tickets => (raw ? JSON.parse(`${raw}`) : {})
const prune = (tickets: Tickets) => {
  for (const key of Object.keys(tickets)) if (tickets[key]!.expiresAt <= Date.now()) delete tickets[key]
  return tickets
}
const instanceFor = (e: core.RequestEvent) => {
  const id = e.request!.pathValue('id')
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Instance introuvable.')
  return $app.findRecordById('instances', id)
}
const consoleUrl = (instance: core.Record) => {
  const host = instancePublicHostname(instanceHostnameView(instance), $os.getenv('APEX_DOMAIN') || 'pockethost.lvh.me')
  if (!/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::\d+)?$/i.test(host))
    throw new BadRequestError('Adresse de console invalide.')
  const protocol = ($os.getenv('APP_URL') || '').startsWith('http://') ? 'http' : 'https'
  return `${protocol}://${host}/_/ph-auth`
}
const assertAccess = (instance: core.Record, user: core.Record) => {
  if (user.collection().name !== 'users' || !canManageInstance(instance, user)) {
    throw new ForbiddenError('Vous ne pouvez pas administrer cette instance.')
  }
  if (user.getString('suspension') || instance.getString('suspension')) throw new ForbiddenError('Accès suspendu.')
}
const result = (e: core.RequestEvent, value: unknown) => {
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(200, value)
}

/** Public address only: a logged-out dashboard bridge can return to the native sign-in form. */
export const HandleInstanceConsoleInfo = (e: core.RequestEvent) => result(e, { url: consoleUrl(instanceFor(e)) })

export const HandleInstanceConsoleTicket = (e: core.RequestEvent) => {
  const instance = instanceFor(e)
  if (!e.auth) throw new UnauthorizedError('Connexion au dashboard requise.')
  assertAccess(instance, e.auth)
  if (!instance.getBool('power')) throw new BadRequestError('Démarrez cette instance avant d’ouvrir sa console.')
  const version = instance.getString('version').match(/^(\d+)\.(\d+)/)
  if (!version || (Number(version[1]) === 0 && Number(version[2]) < 23)) {
    throw new BadRequestError('La connexion depuis le dashboard nécessite PocketBase 0.23 ou ultérieur.')
  }
  const headers = JSON.parse(JSON.stringify(e.requestInfo().headers || {}))
  const payload = JSON.parse(
    JSON.stringify($security.parseUnverifiedJWT((headers.authorization || '').replace(/^Bearer\s+/i, '')))
  )
  const expiresAt = Math.min(Date.now() + 60_000, Number(payload.exp) * 1000)
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) throw new UnauthorizedError('Session expirée.')
  const user = e.auth
  return result(e, issueInstanceConsoleTicket(instance, user, expiresAt))
}

/** Used by server administration after its authenticated dashboard permission check. */
export const issueInstanceConsoleTicket = (instance: core.Record, user: core.Record, expiresAt: number) => {
  assertAccess(instance, user)
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now() || expiresAt > Date.now() + 60000)
    throw new UnauthorizedError('Session expirée.')
  const ticket = $security.randomString(64)
  $app.store().setFunc(TICKETS, (raw: unknown) => {
    const tickets = prune(decode(raw))
    // One pending ticket per account and instance, without storing a dashboard bearer token.
    for (const key of Object.keys(tickets))
      if (tickets[key]!.user === user.id && tickets[key]!.instance === instance.id) delete tickets[key]
    if (Object.keys(tickets).length >= 1000) throw new TooManyRequestsError('Réessayez dans une minute.')
    tickets[$security.sha256(ticket)] = {
      instance: instance.id,
      owner: instance.getString('uid'),
      user: user.id,
      authKey: $security.sha256(user.getString('tokenKey')),
      expiresAt,
    }
    return JSON.stringify(tickets)
  })
  return { url: consoleUrl(instance), ticket, expiresAt: new Date(expiresAt).toISOString() }
}

/** The instance exchanges a one-use capability; its own id comes from platform configuration. */
export const HandleInstanceConsoleConsume = (e: core.RequestEvent) => {
  const instance = instanceFor(e)
  const input = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  if (!/^[A-Za-z0-9]{64}$/.test(input.ticket || '')) throw new UnauthorizedError('Autorisation de console invalide.')
  let consumed: ConsoleTicket | undefined
  $app.store().setFunc(TICKETS, (raw: unknown) => {
    const tickets = prune(decode(raw))
    const key = $security.sha256(input.ticket)
    const ticket = tickets[key]
    if (ticket?.instance === instance.id) {
      consumed = ticket
      delete tickets[key]
    }
    return JSON.stringify(tickets)
  })
  if (!consumed)
    throw new UnauthorizedError('Autorisation expirée ou déjà utilisée. Rouvrez la console depuis le dashboard.')
  const user = $app.findRecordById('users', consumed.user)
  assertAccess(instance, user)
  if (
    instance.getString('uid') !== consumed.owner ||
    !instance.getBool('power') ||
    !$security.equal(consumed.authKey, $security.sha256(user.getString('tokenKey')))
  ) {
    throw new UnauthorizedError('Cette autorisation de console a été révoquée.')
  }
  const owner = $app.findRecordById('users', consumed.owner)
  const native = migratedNativeAdmin(instance, owner)
  if (native) return result(e, { instance: instance.id, user: native.id, email: native.email })
  return result(e, { instance: instance.id, user: owner.id, email: owner.getString('email') })
}

export const PruneInstanceConsoleTickets = () =>
  $app.store().setFunc(TICKETS, (raw: unknown) => JSON.stringify(prune(decode(raw))))
