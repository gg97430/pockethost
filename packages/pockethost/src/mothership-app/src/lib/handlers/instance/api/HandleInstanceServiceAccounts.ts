import { canManageInstance } from '../../../security/instanceAccess'
import { operationJson, operationsAccess, recordInstanceAction } from './instanceOperationsAccess'
const STORE = 'ph_service_commands'
type Command = {
  instance: string
  owner: string
  user: string
  authKey: string
  expires: number
  action: string
  label: string
  account: string
}
const decode = (raw: unknown): Record<string, Command> => (raw ? JSON.parse(`${raw}`) : {})
export const HandleInstanceServiceAccounts = (e: core.RequestEvent) => {
  const { instance, user } = operationsAccess(e)
  if (!instance.getBool('power')) throw new BadRequestError('Démarrez l’instance pour gérer ses comptes techniques.')
  const input = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  const action = e.request!.method === 'GET' ? 'list' : `${input.action || ''}`
  if (!['list', 'create', 'rotate', 'revoke'].includes(action)) throw new BadRequestError('Action invalide.')
  if (action === 'create' && (!`${input.label || ''}`.trim() || `${input.label}`.length > 80))
    throw new BadRequestError('Nom attendu (80 caractères maximum).')
  if (['rotate', 'revoke'].includes(action) && !/^[a-z0-9]{15}$/.test(input.account || ''))
    throw new BadRequestError('Compte invalide.')
  const ticket = $security.randomString(64)
  $app.store().setFunc(STORE, (raw: unknown) => {
    const commands = decode(raw)
    for (const k of Object.keys(commands)) if (commands[k]!.expires < Date.now()) delete commands[k]
    if (Object.keys(commands).length >= 500) throw new TooManyRequestsError('Réessayez dans une minute.')
    commands[$security.sha256(ticket)] = {
      instance: instance.id,
      owner: instance.getString('uid'),
      user: user.id,
      authKey: $security.sha256(user.getString('tokenKey')),
      expires: Date.now() + 30000,
      action,
      label: `${input.label || ''}`.trim(),
      account: input.account || '',
    }
    return JSON.stringify(commands)
  })
  const apex = $os.getenv('APEX_DOMAIN')
  const subdomain = instance.getString('subdomain')
  if (!/^[a-z0-9.-]+$/i.test(apex) || !/^[a-z0-9-]+$/i.test(subdomain)) throw new BadRequestError('Adresse invalide.')
  const protocol = ($os.getenv('APP_URL') || '').startsWith('http://') ? 'http' : 'https'
  let response: any
  try {
    response = $http.send({
      url: `${protocol}://${subdomain}.${apex}/api/ph-service-accounts/execute`,
      method: 'POST',
      timeout: 20,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket }),
    })
  } catch {
    throw new BadRequestError('Instance inaccessible. Vérifiez sa disponibilité et la version des hooks PocketHost.')
  }
  if (response.statusCode !== 200)
    throw new BadRequestError(
      response.statusCode === 404
        ? 'Mettez à jour les hooks de cette instance avant de gérer les comptes.'
        : 'La gestion du compte a échoué. Rechargez la liste avant de réessayer.'
    )
  const result = JSON.parse(toString(response.raw))
  if (action !== 'list') recordInstanceAction(instance, user, `service-account.${action}`, { account: result.id })
  return operationJson(e, result)
}
export const HandleInstanceOperationConsume = (e: core.RequestEvent) => {
  const instanceId = e.request!.pathValue('id')
  const input = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  if (!/^[a-z0-9]{15}$/.test(instanceId) || !/^[A-Za-z0-9]{64}$/.test(input.ticket || ''))
    throw new UnauthorizedError('Autorisation invalide.')
  let command: Command | undefined
  $app.store().setFunc(STORE, (raw: unknown) => {
    const commands = decode(raw),
      key = $security.sha256(input.ticket)
    if (commands[key]?.instance === instanceId) {
      command = commands[key]
      delete commands[key]
    }
    return JSON.stringify(commands)
  })
  if (!command || command.expires < Date.now()) throw new UnauthorizedError('Autorisation expirée ou déjà utilisée.')
  const instance = $app.findRecordById('instances', instanceId),
    user = $app.findRecordById('users', command.user)
  if (
    instance.getString('uid') !== command.owner ||
    !instance.getBool('power') ||
    !canManageInstance(instance, user) ||
    user.getString('suspension') ||
    instance.getString('suspension') ||
    !$security.equal($security.sha256(user.getString('tokenKey')), command.authKey)
  )
    throw new UnauthorizedError('Autorisation révoquée.')
  return operationJson(e, {
    instance: instanceId,
    action: command.action,
    label: command.label,
    account: command.account,
  })
}
