import { operationJson, recordInstanceAction } from '../instance/api/instanceOperationsAccess'
import { requireOperatorAdmin } from './auth'

const local = (action: string, body: unknown) => {
  let result: any
  try {
    result = JSON.parse(
      toString(
        $os
          .cmd(
            '/usr/bin/timeout',
            '30',
            '/usr/bin/python3',
            '-I',
            '/opt/pockethost-web/platform_operator_socket.py',
            'request',
            action,
            JSON.stringify(body)
          )
          .output()
      )
    )
  } catch {
    throw new ApiError(503, 'Le contrôle local est indisponible. Les services clients restent en service.')
  }
  if (!result.ok)
    throw new ApiError(
      result.status === 409 ? 409 : result.status === 400 ? 400 : 503,
      result.message || 'Contrôle local indisponible.'
    )
  return result.value
}

export const HandleOperatorControls = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  e.response.header().set('Cache-Control', 'no-store')
  return operationJson(e, local('status', {}))
}

export const HandleOperatorMemory = (e: core.RequestEvent) => {
  const actor = requireOperatorAdmin(e)
  const body = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  if (typeof body.id !== 'string' || !/^[a-z0-9]{15}$/.test(body.id)) throw new BadRequestError('Base invalide.')
  const instance = $app.findRecordById('instances', body.id)
  const result = local('memory', body)
  recordInstanceAction(instance, actor, 'memory.updated', {
    mode: body.mode,
    maximumMiB: body.maximumMiB,
    restartRequired: false,
  })
  return operationJson(e, result)
}

export const HandleOperatorDeploymentMaintenance = (e: core.RequestEvent) => {
  const actor = requireOperatorAdmin(e)
  const body = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
  let result: any
  if (body.paused === true && Object.keys(body).sort().join(',') === 'paused,seconds')
    result = local('pause', {
      owner: `admin-${actor.id}-${$security.randomString(8).toLowerCase()}`,
      seconds: body.seconds,
    })
  else if (body.paused === false && Object.keys(body).sort().join(',') === 'owner,paused')
    result = local('resume', { owner: body.owner })
  else throw new BadRequestError('Pause ou reprise invalide.')
  $app.logger().info('Deployment admission maintenance updated', 'actor', actor.id, 'paused', body.paused)
  return operationJson(e, result)
}
