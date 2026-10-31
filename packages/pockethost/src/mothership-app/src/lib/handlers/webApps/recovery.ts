import { requirePlatformAdmin, webInput, webJson } from './api'

const DRIVER = '/opt/pockethost-web/web_platform_recovery_service.py'
const run = (operation: string, input: Record<string, unknown> = {}) => {
  const configured = $os.getenv('WEB_PLATFORM_RECOVERY_RUNNER') || DRIVER
  if (!configured.startsWith('/') || /[\r\n\0]/.test(configured))
    throw new BadRequestError('Le service de reprise n’est pas configuré.')
  try {
    const response = JSON.parse(
      toString($os.cmd('python3', configured, 'api', operation, JSON.stringify(input)).output())
    )
    if (
      !response ||
      ![200, 202, 400, 409, 503].includes(response.status) ||
      !response.response ||
      typeof response.response !== 'object'
    )
      throw new Error()
    return response as { status: number; response: any }
  } catch {
    if (operation === 'status')
      return {
        status: 200,
        response: { configured: false, worker: { online: false }, settings: { enabled: false }, unavailable: true },
      }
    throw new BadRequestError(
      'Le service de reprise ne peut pas confirmer la demande. Actualisez son état avant de réessayer.'
    )
  }
}
// Same fixed runner for the alert watchdog (recovery_failed backstop): no other entry point.
export const webPlatformRecoveryApi = run
const plain = (input: any, keys: string[]) => {
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).some((key) => !keys.includes(key))
  )
    throw new BadRequestError('Options de reprise invalides.')
}
const audit = (actorId: string, action: string, response: any, details: any = {}) => {
  const row = new Record($app.findCollectionByNameOrId('web_platform_recovery_audit'))
  row.set('actorId', actorId)
  row.set('action', action)
  row.set('jobId', typeof response.job?.id === 'string' ? response.job.id.slice(0, 128) : '')
  row.set('details', details)
  $app.save(row)
}
export const HandleWebPlatformRecoveryStatus = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const result = run('status')
  return webJson(e, result.response, result.status)
}
export const HandleWebPlatformRecoveryRuns = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const raw = e.request!.url!.query().get('page') || '1'
  if (!/^\d{1,6}$/.test(raw) || Number(raw) < 1 || Number(raw) > 100000)
    throw new BadRequestError('Page de reprise invalide.')
  const result = run('runs', { page: Number(raw) })
  return webJson(e, result.response, result.status)
}
export const HandleWebPlatformRecoverySettings = (e: core.RequestEvent) => {
  const actor = requirePlatformAdmin(e),
    input = webInput(e)
  plain(input, ['enabled', 'hourUTC', 'minuteUTC', 'retention', 'runToday'])
  // runToday: arming after today's time also runs today instead of waiting for tomorrow.
  if (input.runToday !== undefined && typeof input.runToday !== 'boolean')
    throw new BadRequestError('Option « aujourd’hui » invalide.')
  if (
    typeof input.enabled !== 'boolean' ||
    !Number.isInteger(input.hourUTC) ||
    input.hourUTC < 0 ||
    input.hourUTC > 23 ||
    !Number.isInteger(input.minuteUTC) ||
    input.minuteUTC < 0 ||
    input.minuteUTC > 59 ||
    !Number.isInteger(input.retention) ||
    input.retention < 1 ||
    input.retention > 30
  )
    throw new BadRequestError('Horaire UTC et rétention de 1 à 30 captures requis.')
  const result = run('settings', { ...input, actorId: actor.id })
  if (result.status < 300) audit(actor.id, 'settings', result.response, input)
  return webJson(e, result.response, result.status)
}
export const HandleWebPlatformRecoveryCapture = (e: core.RequestEvent) => {
  const actor = requirePlatformAdmin(e),
    input = webInput(e)
  plain(input, [])
  const result = run('capture', { actorId: actor.id })
  if (result.status < 300) audit(actor.id, 'capture', result.response)
  return webJson(e, result.response, result.status)
}
export const HandleWebPlatformRecoveryDrill = (e: core.RequestEvent) => {
  const actor = requirePlatformAdmin(e),
    input = webInput(e)
  plain(input, [])
  const backupId = e.request!.pathValue('backupId')
  if (!/^[a-f0-9]{32}$/.test(backupId || '')) throw new BadRequestError('Capture de reprise invalide.')
  const result = run('drill', { backupId, actorId: actor.id })
  if (result.status < 300) audit(actor.id, 'drill', result.response, { backupId })
  return webJson(e, result.response, result.status)
}
