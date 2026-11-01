import { consoleSsoConfig } from './consoleSso'
const TABLE = 'ph_service_accounts'
export const initializeServiceAccounts = () => {
  try {
    $app.findCollectionByNameOrId(TABLE)
    return
  } catch {}
  $app.save(
    new Collection({
      name: TABLE,
      type: 'base',
      listRule: null,
      viewRule: null,
      createRule: null,
      updateRule: null,
      deleteRule: null,
      fields: [
        { name: 'authId', type: 'text', required: true, max: 15 },
        { name: 'label', type: 'text', required: true, max: 80 },
        { name: 'email', type: 'text', required: true, max: 254 },
        { name: 'lastAuthAt', type: 'date' },
        { name: 'revokedAt', type: 'date' },
        { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
      ],
      indexes: ['CREATE UNIQUE INDEX idx_ph_service_auth ON ph_service_accounts (authId)'],
    })
  )
}
export const trackServiceAuthentication = (record: core.Record) => {
  if (!record.getString('email').startsWith('ph-service-')) return
  try {
    const row = $app.findFirstRecordByFilter(TABLE, 'authId = {:id}', { id: record.id })
    if (Date.now() - Date.parse(row.getString('lastAuthAt') || '1970-01-01') < 60000) return
    row.set('lastAuthAt', new Date().toISOString())
    $app.save(row)
  } catch {
    /* Authentication must remain available if tracking fails. */
  }
}
export const executeServiceAccountCommand = (e: core.RequestEvent) => {
  const config = consoleSsoConfig()
  if (!config) throw new BadRequestError('La connexion dashboard doit être configurée.')
  const input = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  if (!/^[A-Za-z0-9]{64}$/.test(input.ticket || '')) throw new UnauthorizedError('Autorisation invalide.')
  const response = $http.send({
    url: `${config.dashboard}/api/instance/${config.instance}/operations/consume`,
    method: 'POST',
    timeout: 10,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticket: input.ticket }),
  })
  if (response.statusCode !== 200) throw new UnauthorizedError('Autorisation expirée ou révoquée.')
  const command = JSON.parse(toString(response.raw))
  if (command.instance !== config.instance) throw new UnauthorizedError('Instance invalide.')
  const result: Record<string, unknown> = {}
  if (command.action === 'list') {
    result.accounts = $app.findRecordsByFilter(TABLE, '', '-created', 101, 0).map((r) => ({
      id: r.id,
      label: r.getString('label'),
      email: r.getString('email'),
      lastAuthAt: r.getString('lastAuthAt'),
      revokedAt: r.getString('revokedAt'),
      created: r.getString('created'),
    }))
  } else {
    if (!['create', 'rotate', 'revoke'].includes(command.action)) throw new BadRequestError('Action invalide.')
    $app.runInTransaction((app) => {
      let row: core.Record, auth: core.Record
      if (command.action === 'create') {
        if (app.countRecords(TABLE) >= 100) throw new BadRequestError('Limite de 100 comptes atteinte.')
        const label = `${command.label || ''}`.trim()
        if (!label || label.length > 80) throw new BadRequestError('Nom attendu (80 caractères maximum).')
        auth = new Record(app.findCollectionByNameOrId('_superusers'))
        auth.set('email', `ph-service-${$security.randomString(20).toLowerCase()}@pockethost.internal`)
        row = new Record(app.findCollectionByNameOrId(TABLE))
        row.set('label', label)
        row.set('email', auth.getString('email'))
      } else {
        if (!/^[a-z0-9]{15}$/.test(command.account || '')) throw new BadRequestError('Compte invalide.')
        row = app.findRecordById(TABLE, command.account)
        if (row.getString('revokedAt')) throw new BadRequestError('Ce compte est déjà révoqué.')
        auth = app.findRecordById('_superusers', row.getString('authId'))
        if (!auth.getString('email').startsWith('ph-service-') || auth.getString('email') !== row.getString('email'))
          throw new BadRequestError('Compte non géré par PocketHost.')
      }
      if (command.action === 'revoke') {
        auth.setPassword($security.randomString(64))
        auth.refreshTokenKey()
        app.save(auth)
        row.set('revokedAt', new Date().toISOString())
      } else {
        const password = $security.randomString(48)
        auth.setPassword(password)
        auth.refreshTokenKey()
        app.save(auth)
        row.set('authId', auth.id)
        result.password = password
        result.email = auth.getString('email')
      }
      app.save(row)
      result.id = row.id
    })
  }
  e.response.header().set('Cache-Control', 'no-store')
  return e.json(200, result)
}

export const assertServiceAccountActive = (record: core.Record) => {
  if (!record.getString('email').startsWith('ph-service-')) return
  let row: core.Record
  try {
    row = $app.findFirstRecordByFilter(TABLE, 'authId = {:id}', { id: record.id })
  } catch {
    return
  }
  if (row.getString('revokedAt')) throw new UnauthorizedError('Compte technique révoqué.')
}
