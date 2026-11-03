import { selectedInstanceImages } from '../../../../../common/instanceImages'
import { issueInstanceConsoleTicket } from '../instance/api/HandleInstanceConsoleAuth'
import { operationJson, recordInstanceAction } from '../instance/api/instanceOperationsAccess'
import { requireOperatorAdmin } from './auth'
import {
  connectionLimitsRevision,
  readConnectionLimits,
  validateConnectionLimits,
  writeConnectionLimits,
} from './connectionLimits'

export const HandleOperatorRequestLimits = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  const limits = readConnectionLimits()
  return operationJson(e, { limits, revision: connectionLimitsRevision(limits) })
}
export const HandleOperatorRequestLimitsPut = (e: core.RequestEvent) => {
  const actor = requireOperatorAdmin(e),
    body = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  if (
    Object.keys(body).sort().join(',') !== 'limits,revision' ||
    typeof body.revision !== 'string' ||
    !/^[a-f0-9]{64}$/.test(body.revision)
  )
    throw new BadRequestError('Configuration ou révision invalide.')
  const limits = validateConnectionLimits(body.limits)
  writeConnectionLimits(limits, body.revision)
  $app.logger().info('Private database connection limits updated', 'actor', actor.id, 'limits', JSON.stringify(limits))
  return operationJson(e, { limits, revision: connectionLimitsRevision(limits) })
}
export const HandleOperatorDatabaseRequestLimits = (e: core.RequestEvent) => {
  const actor = requireOperatorAdmin(e),
    id = e.request!.pathValue('id')
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Base invalide.')
  const instance = $app.findRecordById('instances', id)
  if (!instance.getBool('power') || instance.getString('suspension'))
    throw new BadRequestError('Démarrez cette base avant de modifier ses limites.')
  const action = e.request!.method === 'PUT' ? 'update' : 'read'
  const input = action === 'update' ? JSON.parse(JSON.stringify(e.requestInfo().body || {})) : null
  const inferred = `${$app.dataDir()}`.replace(/\/mothership\/pb_data\/?$/, '')
  const root = $os.getenv('DATA_ROOT') || (inferred !== `${$app.dataDir()}` ? inferred : '')
  if (!root) throw new InternalServerError('Dossier des bases indisponible.')
  const { ticket } = issueInstanceConsoleTicket(instance, actor, Date.now() + 30000)
  const path = `${$app.dataDir()}/.request-limits-${$security.randomString(24)}.json`
  let result: any
  try {
    $os.writeFile(
      path,
      JSON.stringify({
        dataRoot: root,
        instance: id,
        action,
        input,
        ticket,
        databaseImage: $os.getenv('DOCKER_INSTANCE_IMAGE_NAME') || 'benallfree/pockethost-instance',
        databaseImages: selectedInstanceImages([id], $os.getenv('PH_INSTANCE_IMAGE_OVERRIDES')),
      }),
      0o600 as any
    )
    result = JSON.parse(
      toString(
        $os
          .cmd(
            'python3',
            $os.getenv('INSTANCE_REQUEST_LIMITS_SCRIPT') || '/opt/pockethost-web/instance_request_limits.py',
            path
          )
          .output()
      )
    )
  } catch {
    throw new BadRequestError(
      'La base est inaccessible ou ses hooks doivent être mis à jour. Aucun redémarrage automatique.'
    )
  } finally {
    try {
      $os.remove(path)
    } catch {}
  }
  if (!result.ok)
    throw new ApiError(result.status === 409 ? 409 : 400, result.message || 'Réglage PocketBase indisponible.')
  if (action === 'update')
    recordInstanceAction(instance, actor, 'rate-limits.updated', {
      enabled: result.value.enabled,
      rules: result.value.rules,
    })
  return operationJson(e, result.value)
}
