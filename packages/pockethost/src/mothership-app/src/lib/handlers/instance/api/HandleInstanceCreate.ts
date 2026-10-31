import { mkLog } from '$util/Logger'
import { listVersions } from '$util/versions'
import { readOperatorSettings } from '../../operatorAdmin/operatorSettings'
import { assertNoWebDomainConflict } from '../../webApps/domains'
import { recordInstanceAction } from './instanceOperationsAccess'

export const HandleInstanceCreate = (e: core.RequestEvent) => {
  const log = mkLog(`POST:instance`)
  const authRecord = e.auth
  log(`authRecord`, JSON.stringify(authRecord))

  if (!authRecord) {
    throw new Error(`Session utilisateur attendue`)
  }

  log(`TOP OF POST`)
  let data = new DynamicModel({
    subdomain: '',
    version: listVersions()[0] || '',
  }) as { subdomain?: string; version?: string }

  log(`before bind`)

  e.bindBody(data)

  log(`after bind`)

  // This is necessary for destructuring to work correctly
  data = JSON.parse(JSON.stringify(data))

  const { subdomain, version } = data
  const settings = readOperatorSettings()

  if (!version)
    throw new BadRequestError('Le catalogue des versions PocketBase doit être synchronisé avant de créer une instance.')

  log(`vars`, JSON.stringify({ subdomain }))

  if (!subdomain) {
    throw new BadRequestError(`Le sous-domaine est obligatoire pour créer une instance.`)
  }

  if ($os.getenv('WEB_APPS_ENABLED') === 'true' && subdomain.trim().toLowerCase().startsWith('web-'))
    throw new BadRequestError('Le préfixe web- est réservé aux applications web.')
  assertNoWebDomainConflict(`${subdomain.trim().toLowerCase()}.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`)

  const collection = $app.findCollectionByNameOrId('instances')
  const record = new Record(collection)
  record.set('uid', authRecord.id)
  record.set('subdomain', subdomain)
  record.set('power', settings.defaultInstancePower)
  record.set('status', 'idle')
  record.set('version', version)
  record.set('dev', settings.defaultInstanceDevMode)
  record.set('syncAdmin', settings.defaultSyncAdmin)
  record.set('autoVacuum', settings.defaultAutoVacuum)

  $app.save(record)
  recordInstanceAction(record, authRecord, 'instance.created')

  return e.json(200, { instance: record })
}
