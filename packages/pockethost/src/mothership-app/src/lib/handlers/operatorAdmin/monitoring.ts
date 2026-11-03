import { monitoringStale, parseMonitoringReport } from '$common/platformMonitoring'
import { webDirectLoopback } from '../webApps/api'
import { requireOperatorAdmin } from './auth'
import { readOperatorDiagnostics } from './diagnostics'
import { readOperatorSettings } from './operatorSettings'

const webhookKey = () => {
  const key = $os.getenv('PLATFORM_MONITORING_KEY') || $os.getenv('WEB_APPS_MASTER_KEY')
  if (key.length !== 32) throw new BadRequestError('Clé privée de supervision indisponible.')
  return key
}

const monitoringSettings = (store: any = $app) => {
  try {
    return store.findFirstRecordByFilter('platform_monitoring_settings', 'key="global"')
  } catch {
    return null
  }
}
const smtpReady = () => {
  const smtp = readOperatorSettings().smtp
  return !!(smtp.enabled && smtp.host && smtp.senderAddress)
}
const publicSettings = (row: core.Record | null) => ({
  emailEnabled: row?.getBool('emailEnabled') || false,
  emailRecipients: JSON.parse(row?.getString('emailRecipients') || 'null') || [],
  discordEnabled: row?.getBool('discordEnabled') || false,
  hasDiscordWebhook: !!row?.getString('discordWebhookEncrypted'),
  revision: row?.getString('revision') || '',
  smtpReady: smtpReady(),
})

/** A page refresh never wakes a database, launches Docker or sends alerts. */
export const HandleOperatorMonitoring = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  e.response.header().set('Cache-Control', 'no-store')
  const root = $os.getenv('DATA_ROOT') || $filepath.dir($filepath.dir($app.dataDir()))
  try {
    const content = toString($os.readFile(`${root}/monitoring/status.json`))
    if (content.length > 4 * 1024 * 1024) throw Error('Report too large')
    const report = parseMonitoringReport(JSON.parse(content))
    return e.json(200, {
      available: true,
      stale: monitoringStale(report),
      report,
      diagnostics: readOperatorDiagnostics(report),
      settings: publicSettings(monitoringSettings()),
    })
  } catch {
    return e.json(200, {
      available: false,
      stale: true,
      report: null,
      diagnostics: null,
      settings: publicSettings(monitoringSettings()),
    })
  }
}

export const HandleOperatorMonitoringUpdate = (e: core.RequestEvent) => {
  const actor = requireOperatorAdmin(e),
    input = e.requestInfo().body || {}
  e.response.header().set('Cache-Control', 'no-store')
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).some(
      (key) =>
        ![
          'revision',
          'emailEnabled',
          'emailRecipients',
          'discordEnabled',
          'discordWebhook',
          'clearDiscordWebhook',
        ].includes(key)
    )
  )
    throw new BadRequestError('Configuration invalide.')
  for (const key of ['emailEnabled', 'discordEnabled', 'clearDiscordWebhook'])
    if (input[key] !== undefined && typeof input[key] !== 'boolean')
      throw new BadRequestError('Activation explicite attendue.')
  if (typeof input.revision !== 'string') throw new BadRequestError('Révision attendue.')
  if (input.discordWebhook !== undefined && typeof input.discordWebhook !== 'string')
    throw new BadRequestError('Webhook invalide.')
  let saved: any
  $app.runInTransaction((tx) => {
    const current = monitoringSettings(tx)
    if (input.revision !== (current?.getString('revision') || ''))
      throw new BadRequestError('La configuration a changé. Actualisez avant de réessayer.')
    const recipients =
      input.emailRecipients === undefined ? publicSettings(current).emailRecipients : input.emailRecipients
    if (
      !Array.isArray(recipients) ||
      recipients.length > 20 ||
      recipients.some(
        (value) =>
          typeof value !== 'string' ||
          value.length > 254 ||
          !/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$/.test(value)
      )
    )
      throw new BadRequestError('Jusqu’à 20 adresses e-mail valides sont autorisées.')
    const emailEnabled = input.emailEnabled ?? current?.getBool('emailEnabled') ?? false,
      discordEnabled = input.discordEnabled ?? current?.getBool('discordEnabled') ?? false
    if (emailEnabled && (!recipients.length || !smtpReady()))
      throw new BadRequestError(
        'Renseignez un destinataire et configurez SMTP dans Administration avant d’activer les e-mails.'
      )
    const webhook = input.discordWebhook?.trim() || ''
    if (
      webhook &&
      !/^https:\/\/(?:discord\.com|discordapp\.com)\/api\/webhooks\/[0-9]{15,25}\/[A-Za-z0-9_-]{30,200}$/.test(webhook)
    )
      throw new BadRequestError('Utilisez une URL de webhook Discord officielle, sans paramètres.')
    const encrypted = input.clearDiscordWebhook
      ? ''
      : webhook
        ? $security.encrypt(webhook, webhookKey())
        : current?.getString('discordWebhookEncrypted') || ''
    if (discordEnabled && !encrypted) throw new BadRequestError('Renseignez un webhook avant d’activer Discord.')
    const row = current || new Record(tx.findCollectionByNameOrId('platform_monitoring_settings'))
    row.set('key', 'global')
    row.set('emailEnabled', emailEnabled)
    row.set('emailRecipients', [...new Set(recipients.map((value: string) => value.trim().toLowerCase()))])
    row.set('discordEnabled', discordEnabled)
    row.set('discordWebhookEncrypted', encrypted)
    row.set('revision', $security.randomString(32))
    row.set('updatedBy', actor.id)
    tx.save(row)
    saved = publicSettings(row)
  })
  return e.json(200, { settings: saved })
}

/** Collector-only delivery configuration: native superuser and unrelayed loopback socket required. */
export const HandleOperatorMonitoringDeliveryConfig = (e: core.RequestEvent) => {
  if (e.auth?.collection().name !== '_superusers' || !webDirectLoopback(e))
    throw new ForbiddenError('Accès local du collecteur requis.')
  e.response.header().set('Cache-Control', 'no-store')
  const row = monitoringSettings(),
    settings = publicSettings(row),
    encrypted = row?.getString('discordWebhookEncrypted') || ''
  return e.json(200, {
    revision: settings.revision,
    emailEnabled: settings.emailEnabled,
    emailRecipients: settings.emailRecipients,
    discordEnabled: settings.discordEnabled,
    discordWebhook: encrypted && settings.discordEnabled ? toString($security.decrypt(encrypted, webhookKey())) : '',
    ...(settings.emailEnabled ? { smtp: readOperatorSettings().smtp } : {}),
  })
}
