import { operationJson } from '../instance/api/instanceOperationsAccess'
import { requireOperatorAdmin } from './auth'
const NAME = 'web_ip_request_limit'
const valid = (value: unknown): value is number =>
  Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= 1000000
export const readWebRequestLimit = (app = $app) => {
  const rows = app.findRecordsByFilter('settings', 'name = {:name}', '', 2, 0, { name: NAME })
  if (!rows.length) {
    const raw = $os.getenv('WEB_APPS_IP_RATE_LIMIT'),
      value = raw?.trim() ? Number(raw) : 1200
    return valid(value) ? value : 1200
  }
  const value = rows.length === 1 ? JSON.parse(rows[0]!.getString('value')) : undefined
  if (!valid(value)) throw new InternalServerError('Limite web invalide.')
  return value
}
const response = (value: number) => ({ requestsPerMinute: value, revision: $security.sha256(`${value}`) })
export const HandleOperatorWebRequestLimit = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  if (e.request!.method !== 'PUT') return operationJson(e, response(readWebRequestLimit()))
  const body = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  if (
    Object.keys(body).sort().join(',') !== 'requestsPerMinute,revision' ||
    !valid(body.requestsPerMinute) ||
    typeof body.revision !== 'string'
  )
    throw new BadRequestError('Saisissez un entier de 0 à 1 000 000. Zéro désactive le limiteur web.')
  $app.runInTransaction((app) => {
    if (response(readWebRequestLimit(app)).revision !== body.revision)
      throw new ApiError(409, 'Ce réglage a changé. Rechargez avant d’enregistrer.')
    const row =
      app.findRecordsByFilter('settings', 'name = {:name}', '', 1, 0, { name: NAME })[0] ||
      new Record(app.findCollectionByNameOrId('settings'))
    row.set('name', NAME)
    row.set('value', JSON.stringify(body.requestsPerMinute))
    app.save(row)
  })
  $app.logger().info('Web IP request limit updated', 'actor', e.auth!.id, 'requestsPerMinute', body.requestsPerMinute)
  return operationJson(e, response(body.requestsPerMinute))
}
