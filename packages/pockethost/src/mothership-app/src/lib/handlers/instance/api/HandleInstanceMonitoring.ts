import { mailRecipientSkipReason } from '$util/mailRecipient'
import {
  CollectInstanceResourceMetrics,
  readDockerMetricsSnapshot,
  serializeInstanceRuntimeMetrics,
  type DockerMetricsSnapshot,
} from './HandleInstanceOverview'
import {
  MONITORING_DEFAULTS,
  MONITORING_MAX_DELIVERY_ATTEMPTS,
  classifyHealthError,
  evaluateHealthSignal,
  evaluateThresholdSignal,
  formatMonitoringPercent,
  monitoringHistoryRanges,
  monitoringRetryDelayMs,
  normalizeDiscordWebhook,
  normalizeHealthPath,
  normalizeMonitoringNotificationPayload,
  normalizeSlackWebhook,
  type MonitoringHistoryRange,
  type MonitoringNotificationPayload,
} from './instanceMonitoring'

const POLICY_COLLECTION = 'instance_monitoring_policies'
const CHECK_COLLECTION = 'instance_health_checks'
const INCIDENT_COLLECTION = 'instance_monitoring_incidents'
const DELIVERY_COLLECTION = 'instance_monitoring_deliveries'
const HEALTH_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
const INCIDENT_PAGE_SIZE = 25

type MonitoringIncidentType = 'health' | 'cpu' | 'memory' | 'backup'
type MonitoringChannel = 'email' | 'discord' | 'slack'
type MonitoringPhase = 'opened' | 'resolved' | 'test'

const nowIso = () => new Date().toISOString()
const formatPocketBaseDate = (timestamp: number) => new Date(timestamp).toISOString().replace('T', ' ')

const assertSafeInstanceId = (id: string) => {
  if (!id.match(/^[a-z0-9]+$/)) throw new BadRequestError("Identifiant d'instance invalide.")
}

const pathValue = (e: core.RequestEvent, name: string) => {
  if (!e.request) throw new BadRequestError('Requête invalide.')
  return e.request.pathValue(name)
}

const requireAuthRecord = (authRecord?: core.Record) => {
  if (!authRecord) throw new BadRequestError('Session utilisateur attendue.')
  return authRecord
}

const findInstance = (id: string) => {
  assertSafeInstanceId(id)
  const instance = $app.findRecordById('instances', id)
  if (!instance) throw new BadRequestError(`Instance ${id} introuvable.`)
  return instance
}

const assertInstanceAccess = (instance: core.Record, authRecord: core.Record) => {
  if (instance.getString('uid') !== authRecord.id && !authRecord.getBool('superAdmin')) {
    throw new BadRequestError('Non autorisé.')
  }
}

const findPolicy = (instanceId: string) => {
  try {
    return $app.findFirstRecordByFilter(POLICY_COLLECTION, 'instance = {:instance}', { instance: instanceId })
  } catch {
    return null
  }
}

const setPolicyDefaults = (policy: core.Record, instance: core.Record) => {
  policy.set('user', instance.getString('uid'))
  policy.set('instance', instance.id)
  policy.set('enabled', false)
  policy.set('healthEnabled', true)
  policy.set('healthPath', MONITORING_DEFAULTS.healthPath)
  policy.set('healthFailureCount', MONITORING_DEFAULTS.healthFailureCount)
  policy.set('cpuEnabled', true)
  policy.set('cpuThresholdPercent', MONITORING_DEFAULTS.cpuThresholdPercent)
  policy.set('cpuSustainMinutes', MONITORING_DEFAULTS.cpuSustainMinutes)
  policy.set('memoryEnabled', true)
  policy.set('memoryThresholdPercent', MONITORING_DEFAULTS.memoryThresholdPercent)
  policy.set('memorySustainMinutes', MONITORING_DEFAULTS.memorySustainMinutes)
  policy.set('backupAlertsEnabled', true)
  policy.set('emailEnabled', true)
  policy.set('discordEnabled', false)
  policy.set('discordWebhook', '')
  policy.set('slackEnabled', false)
  policy.set('slackWebhook', '')
  policy.set('lastHealthStatus', 'unknown')
  policy.set('lastCheckAt', '')
  policy.set('graceUntil', '')
  policy.set('lastMetricsAt', '')
  policy.set('healthFailureStreak', 0)
  policy.set('healthSuccessStreak', 0)
  policy.set('cpuHighStreak', 0)
  policy.set('cpuNormalStreak', 0)
  policy.set('memoryHighStreak', 0)
  policy.set('memoryNormalStreak', 0)
  policy.set('lastCpuCapacityPercent', 0)
  policy.set('lastMemoryPercent', 0)
  policy.set('lastLatencyMs', 0)
  policy.set('lastStatusCode', 0)
  policy.set('lastError', '')
  return policy
}

const createPolicy = (instance: core.Record) => {
  const policy = new Record($app.findCollectionByNameOrId(POLICY_COLLECTION))
  setPolicyDefaults(policy, instance)
  $app.save(policy)
  return policy
}

const monitoringApexDomain = () => `${$os.getenv('APEX_DOMAIN') || 'pockethost.lvh.me'}`.trim()
const monitoringProtocol = () => `${$os.getenv('HTTP_PROTOCOL') || 'https'}`.replace(/:$/, '')
const monitoringTargetUrl = (instance: core.Record, healthPath: string) =>
  `${monitoringProtocol()}://${instance.getString('subdomain')}.${monitoringApexDomain()}${healthPath}`

const defaultPolicyPayload = (instance: core.Record) => ({
  id: '',
  user: instance.getString('uid'),
  instance: instance.id,
  enabled: false,
  healthEnabled: true,
  healthPath: MONITORING_DEFAULTS.healthPath,
  healthFailureCount: MONITORING_DEFAULTS.healthFailureCount,
  cpuEnabled: true,
  cpuThresholdPercent: MONITORING_DEFAULTS.cpuThresholdPercent,
  cpuSustainMinutes: MONITORING_DEFAULTS.cpuSustainMinutes,
  memoryEnabled: true,
  memoryThresholdPercent: MONITORING_DEFAULTS.memoryThresholdPercent,
  memorySustainMinutes: MONITORING_DEFAULTS.memorySustainMinutes,
  backupAlertsEnabled: true,
  emailEnabled: true,
  discordEnabled: false,
  slackEnabled: false,
  hasDiscordWebhook: false,
  hasSlackWebhook: false,
  lastHealthStatus: 'unknown' as const,
  lastCheckAt: '',
  graceUntil: '',
  lastMetricsAt: '',
  healthFailureStreak: 0,
  healthSuccessStreak: 0,
  cpuHighStreak: 0,
  cpuNormalStreak: 0,
  memoryHighStreak: 0,
  memoryNormalStreak: 0,
  lastCpuCapacityPercent: null,
  lastMemoryPercent: null,
  lastLatencyMs: null,
  lastStatusCode: null,
  lastError: '',
  created: '',
  updated: '',
})

const nullableObservedMetric = (policy: core.Record, field: string, observedAtField: string) => {
  if (!policy.getString(observedAtField)) return null
  const value = Number(policy.get(field))
  return Number.isFinite(value) ? value : null
}

const serializePolicy = (instance: core.Record, policy: core.Record | null) => {
  if (!policy) return defaultPolicyPayload(instance)
  return {
    id: policy.id,
    user: policy.getString('user'),
    instance: policy.getString('instance'),
    enabled: policy.getBool('enabled'),
    healthEnabled: policy.getBool('healthEnabled'),
    healthPath: policy.getString('healthPath') || MONITORING_DEFAULTS.healthPath,
    healthFailureCount: policy.getInt('healthFailureCount') || MONITORING_DEFAULTS.healthFailureCount,
    cpuEnabled: policy.getBool('cpuEnabled'),
    cpuThresholdPercent: policy.getFloat('cpuThresholdPercent') || MONITORING_DEFAULTS.cpuThresholdPercent,
    cpuSustainMinutes: policy.getInt('cpuSustainMinutes') || MONITORING_DEFAULTS.cpuSustainMinutes,
    memoryEnabled: policy.getBool('memoryEnabled'),
    memoryThresholdPercent: policy.getFloat('memoryThresholdPercent') || MONITORING_DEFAULTS.memoryThresholdPercent,
    memorySustainMinutes: policy.getInt('memorySustainMinutes') || MONITORING_DEFAULTS.memorySustainMinutes,
    backupAlertsEnabled: policy.getBool('backupAlertsEnabled'),
    emailEnabled: policy.getBool('emailEnabled'),
    discordEnabled: policy.getBool('discordEnabled'),
    slackEnabled: policy.getBool('slackEnabled'),
    hasDiscordWebhook: !!policy.getString('discordWebhook'),
    hasSlackWebhook: !!policy.getString('slackWebhook'),
    lastHealthStatus: policy.getString('lastHealthStatus') || 'unknown',
    lastCheckAt: policy.getString('lastCheckAt'),
    graceUntil: policy.getString('graceUntil'),
    lastMetricsAt: policy.getString('lastMetricsAt'),
    healthFailureStreak: policy.getInt('healthFailureStreak'),
    healthSuccessStreak: policy.getInt('healthSuccessStreak'),
    cpuHighStreak: policy.getInt('cpuHighStreak'),
    cpuNormalStreak: policy.getInt('cpuNormalStreak'),
    memoryHighStreak: policy.getInt('memoryHighStreak'),
    memoryNormalStreak: policy.getInt('memoryNormalStreak'),
    lastCpuCapacityPercent: nullableObservedMetric(policy, 'lastCpuCapacityPercent', 'lastMetricsAt'),
    lastMemoryPercent: nullableObservedMetric(policy, 'lastMemoryPercent', 'lastMetricsAt'),
    lastLatencyMs: nullableObservedMetric(policy, 'lastLatencyMs', 'lastCheckAt'),
    lastStatusCode:
      policy.getString('lastCheckAt') && policy.getInt('lastStatusCode') > 0 ? policy.getInt('lastStatusCode') : null,
    lastError: policy.getString('lastError'),
    created: policy.getString('created'),
    updated: policy.getString('updated'),
  }
}

const monitoringResponse = (instance: core.Record, policy: core.Record | null) => {
  const serialized = serializePolicy(instance, policy)
  let emailAddress = ''
  try {
    emailAddress = $app.findRecordById('users', instance.getString('uid')).email()
  } catch {}
  return {
    policy: serialized,
    capabilities: {
      emailAddress,
      targetUrl: monitoringTargetUrl(instance, serialized.healthPath),
      requestIntervalSeconds: 60,
      requestTimeoutSeconds: MONITORING_DEFAULTS.requestTimeoutSeconds,
      retentionDays: 30,
    },
  }
}

const clampNumber = (value: unknown, fallback: number, min: number, max: number, integer = false) => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  const clamped = Math.max(min, Math.min(max, parsed))
  return integer ? Math.round(clamped) : clamped
}

const findOpenIncident = (instanceId: string, type: MonitoringIncidentType, sourceId = '') => {
  const sourceFilter = sourceId ? ' && sourceId = {:sourceId}' : ''
  const records = $app.findRecordsByFilter(
    INCIDENT_COLLECTION,
    `instance = {:instance} && type = {:type} && status = "open"${sourceFilter}`,
    '-openedAt',
    1,
    0,
    { instance: instanceId, type, sourceId }
  )
  return records[0] || null
}

const incidentLabel = (type: MonitoringIncidentType) => {
  if (type === 'health') return 'Instance indisponible'
  if (type === 'cpu') return 'CPU élevé'
  if (type === 'memory') return 'Mémoire élevée'
  return 'Sauvegarde en échec'
}

const queueDelivery = (
  instance: core.Record,
  incident: core.Record | null,
  phase: MonitoringPhase,
  channel: MonitoringChannel,
  payload: MonitoringNotificationPayload
) => {
  const delivery = new Record($app.findCollectionByNameOrId(DELIVERY_COLLECTION))
  delivery.set('user', instance.getString('uid'))
  delivery.set('instance', instance.id)
  delivery.set('incident', incident?.id || '')
  delivery.set('phase', phase)
  delivery.set('channel', channel)
  delivery.set('status', 'pending')
  delivery.set('attempts', 0)
  delivery.set('nextAttemptAt', nowIso())
  delivery.set('lastAttemptAt', '')
  delivery.set('deliveredAt', '')
  delivery.set('lastError', '')
  delivery.set('payload', payload)
  $app.save(delivery)
  return delivery
}

const queueIncidentNotifications = (
  policy: core.Record,
  instance: core.Record,
  incident: core.Record,
  phase: 'opened' | 'resolved'
) => {
  const type = incident.getString('type') as MonitoringIncidentType
  const payload: MonitoringNotificationPayload = {
    title: `${phase === 'opened' ? 'ALERTE' : 'RÉTABLI'} · ${incidentLabel(type)} · ${instance.getString('subdomain')}`,
    message: incident.getString('message'),
    instanceId: instance.id,
    instanceName: instance.getString('subdomain'),
    type,
    phase,
    occurredAt: phase === 'opened' ? incident.getString('openedAt') : incident.getString('resolvedAt'),
  }
  if (policy.getBool('emailEnabled')) queueDelivery(instance, incident, phase, 'email', payload)
  if (policy.getBool('discordEnabled') && policy.getString('discordWebhook')) {
    queueDelivery(instance, incident, phase, 'discord', payload)
  }
  if (policy.getBool('slackEnabled') && policy.getString('slackWebhook')) {
    queueDelivery(instance, incident, phase, 'slack', payload)
  }
}

const openIncident = (
  policy: core.Record,
  instance: core.Record,
  type: MonitoringIncidentType,
  message: string,
  value = 0,
  threshold = 0,
  details: Record<string, unknown> = {},
  sourceId = ''
) => {
  const existing = findOpenIncident(instance.id, type, sourceId)
  if (existing) return existing
  const incident = new Record($app.findCollectionByNameOrId(INCIDENT_COLLECTION))
  incident.set('user', instance.getString('uid'))
  incident.set('instance', instance.id)
  incident.set('type', type)
  incident.set('status', 'open')
  incident.set('openedAt', nowIso())
  incident.set('resolvedAt', '')
  incident.set('value', value)
  incident.set('threshold', threshold)
  incident.set('message', message)
  incident.set('details', details)
  incident.set('sourceId', sourceId)
  $app.save(incident)
  queueIncidentNotifications(policy, instance, incident, 'opened')
  return incident
}

const resolveIncident = (
  policy: core.Record,
  instance: core.Record,
  incident: core.Record | null,
  message: string,
  notify = true,
  value?: number
) => {
  if (!incident || incident.getString('status') !== 'open') return null
  incident.set('status', 'resolved')
  incident.set('resolvedAt', nowIso())
  incident.set('message', message)
  if (value !== undefined) incident.set('value', value)
  $app.save(incident)
  if (notify) queueIncidentNotifications(policy, instance, incident, 'resolved')
  return incident
}

const resetRuntimeStreaks = (policy: core.Record) => {
  policy.set('healthFailureStreak', 0)
  policy.set('healthSuccessStreak', 0)
  policy.set('cpuHighStreak', 0)
  policy.set('cpuNormalStreak', 0)
  policy.set('memoryHighStreak', 0)
  policy.set('memoryNormalStreak', 0)
}

const pauseRuntimeMonitoring = (policy: core.Record, instance: core.Record, includeBackups = false) => {
  const types: MonitoringIncidentType[] = includeBackups
    ? ['health', 'cpu', 'memory', 'backup']
    : ['health', 'cpu', 'memory']
  for (const type of types) {
    resolveIncident(policy, instance, findOpenIncident(instance.id, type), 'Surveillance mise en pause.', false)
  }
  policy.set('lastHealthStatus', 'paused')
  policy.set('graceUntil', '')
  resetRuntimeStreaks(policy)
  $app.save(policy)
}

type HealthProbe = {
  healthy: boolean
  latencyMs: number
  statusCode: number
  errorKind: 'none' | 'timeout' | 'dns' | 'tls' | 'http' | 'network' | 'unknown'
  error: string
}

const runHealthProbe = (instance: core.Record, path: string): HealthProbe => {
  const startedAt = Date.now()
  try {
    const response = $http.send({
      url: monitoringTargetUrl(instance, path),
      method: 'GET',
      timeout: MONITORING_DEFAULTS.requestTimeoutSeconds,
      headers: { Accept: 'application/json', 'User-Agent': 'PocketHost-Monitor/1.0' },
    })
    const healthy = response.statusCode >= 200 && response.statusCode < 400
    return {
      healthy,
      latencyMs: Math.max(0, Date.now() - startedAt),
      statusCode: response.statusCode,
      errorKind: healthy ? 'none' : 'http',
      error: healthy ? '' : `Réponse HTTP ${response.statusCode}`,
    }
  } catch (error) {
    return {
      healthy: false,
      latencyMs: Math.max(0, Date.now() - startedAt),
      statusCode: 0,
      errorKind: classifyHealthError(error),
      error: `${error}`.slice(0, 300),
    }
  }
}

const saveHealthCheck = (instance: core.Record, probe: HealthProbe, checkedAt: string) => {
  const record = new Record($app.findCollectionByNameOrId(CHECK_COLLECTION))
  record.set('user', instance.getString('uid'))
  record.set('instance', instance.id)
  record.set('checkedAt', checkedAt)
  record.set('result', probe.healthy ? 'healthy' : 'unhealthy')
  record.set('latencyMs', probe.latencyMs)
  record.set('statusCode', probe.statusCode)
  record.set('errorKind', probe.errorKind)
  record.set('error', probe.error)
  $app.save(record)
}

const evaluateHealth = (policy: core.Record, instance: core.Record) => {
  if (!policy.getBool('healthEnabled')) return
  const checkedAt = nowIso()
  const probe = runHealthProbe(instance, normalizeHealthPath(policy.getString('healthPath')))
  saveHealthCheck(instance, probe, checkedAt)
  policy.set('lastCheckAt', checkedAt)
  policy.set('lastHealthStatus', probe.healthy ? 'healthy' : 'unhealthy')
  policy.set('lastLatencyMs', probe.latencyMs)
  policy.set('lastStatusCode', probe.statusCode)
  policy.set('lastError', probe.error)

  const incident = findOpenIncident(instance.id, 'health')
  const inGrace = Date.parse(policy.getString('graceUntil')) > Date.now()
  if (inGrace) {
    policy.set('healthFailureStreak', 0)
    policy.set('healthSuccessStreak', 0)
    return
  }

  const evaluation = evaluateHealthSignal(
    {
      failureStreak: policy.getInt('healthFailureStreak'),
      successStreak: policy.getInt('healthSuccessStreak'),
      incidentOpen: !!incident,
    },
    probe.healthy,
    policy.getInt('healthFailureCount') || MONITORING_DEFAULTS.healthFailureCount
  )
  policy.set('healthFailureStreak', evaluation.state.failureStreak)
  policy.set('healthSuccessStreak', evaluation.state.successStreak)
  if (evaluation.transition === 'open') {
    openIncident(
      policy,
      instance,
      'health',
      `${instance.getString('subdomain')} ne répond plus correctement (${probe.error || `HTTP ${probe.statusCode}`}).`,
      probe.statusCode,
      0,
      { latencyMs: probe.latencyMs, errorKind: probe.errorKind, healthPath: policy.getString('healthPath') }
    )
  } else if (evaluation.transition === 'resolve') {
    resolveIncident(
      policy,
      instance,
      incident,
      `${instance.getString('subdomain')} répond de nouveau normalement en ${probe.latencyMs} ms.`,
      true,
      probe.statusCode
    )
  }
}

const evaluateResource = (policy: core.Record, instance: core.Record, type: 'cpu' | 'memory', value: number | null) => {
  const prefix = type === 'cpu' ? 'cpu' : 'memory'
  if (!policy.getBool(`${prefix}Enabled`) || value === null || !Number.isFinite(value)) return
  const threshold = policy.getFloat(`${prefix}ThresholdPercent`) || MONITORING_DEFAULTS[`${prefix}ThresholdPercent`]
  const sustain = policy.getInt(`${prefix}SustainMinutes`) || MONITORING_DEFAULTS[`${prefix}SustainMinutes`]
  const highField = type === 'cpu' ? 'cpuHighStreak' : 'memoryHighStreak'
  const normalField = type === 'cpu' ? 'cpuNormalStreak' : 'memoryNormalStreak'
  const lastField = type === 'cpu' ? 'lastCpuCapacityPercent' : 'lastMemoryPercent'
  const incident = findOpenIncident(instance.id, type)
  const evaluation = evaluateThresholdSignal(
    {
      highStreak: policy.getInt(highField),
      normalStreak: policy.getInt(normalField),
      incidentOpen: !!incident,
    },
    value,
    threshold,
    sustain
  )
  policy.set(highField, evaluation.state.highStreak)
  policy.set(normalField, evaluation.state.normalStreak)
  policy.set(lastField, value)
  const formatted = formatMonitoringPercent(value)
  if (evaluation.transition === 'open') {
    openIncident(
      policy,
      instance,
      type,
      `${type === 'cpu' ? 'Le CPU' : 'La mémoire'} de ${instance.getString('subdomain')} atteint ${formatted} % depuis ${sustain} minutes.`,
      value,
      threshold,
      { sustainMinutes: sustain }
    )
  } else if (evaluation.transition === 'resolve') {
    resolveIncident(
      policy,
      instance,
      incident,
      `${type === 'cpu' ? 'Le CPU' : 'La mémoire'} de ${instance.getString('subdomain')} est revenu à ${formatted} %.`,
      true,
      value
    )
  }
}

const collectMonitoringPolicy = (policy: core.Record, snapshot: DockerMetricsSnapshot) => {
  let instance: core.Record
  try {
    instance = findInstance(policy.getString('instance'))
  } catch {
    return false
  }
  if (!instance.getBool('power')) {
    pauseRuntimeMonitoring(policy, instance)
    return true
  }
  if (policy.getString('lastHealthStatus') === 'paused' || !policy.getString('graceUntil')) {
    policy.set('graceUntil', new Date(Date.now() + MONITORING_DEFAULTS.startupGraceMs).toISOString())
    resetRuntimeStreaks(policy)
  }
  evaluateHealth(policy, instance)
  const metric = serializeInstanceRuntimeMetrics(instance, snapshot)
  if (metric.cpuCapacityPercent !== null) policy.set('lastCpuCapacityPercent', metric.cpuCapacityPercent)
  if (metric.memoryPercent !== null) policy.set('lastMemoryPercent', metric.memoryPercent)
  if (metric.cpuCapacityPercent !== null || metric.memoryPercent !== null) policy.set('lastMetricsAt', nowIso())
  evaluateResource(policy, instance, 'cpu', metric.cpuCapacityPercent)
  evaluateResource(policy, instance, 'memory', metric.memoryPercent)
  $app.save(policy)
  return true
}

export const CollectInstanceMonitoring = (snapshot: DockerMetricsSnapshot) => {
  const policies = $app.findRecordsByFilter(POLICY_COLLECTION, 'enabled = true', '', 500, 0)
  let checked = 0
  let failed = 0
  for (const policy of policies) {
    try {
      if (collectMonitoringPolicy(policy, snapshot)) checked += 1
    } catch (error) {
      failed += 1
      console.warn(`Surveillance impossible pour l'instance ${policy.getString('instance')}: ${error}`)
    }
  }
  return { checked, failed }
}

const escapeHtml = (value: unknown) =>
  `${value ?? ''}`.replace(
    /[&<>"']/g,
    (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[character]!
  )

const deliveryPayload = (delivery: core.Record) => {
  try {
    return normalizeMonitoringNotificationPayload(delivery.getString('payload'))
  } catch (serializedError) {
    try {
      return normalizeMonitoringNotificationPayload(delivery.get('payload'))
    } catch {
      throw serializedError
    }
  }
}

const sendEmailDelivery = (delivery: core.Record, payload: MonitoringNotificationPayload) => {
  const user = $app.findRecordById('users', delivery.getString('user'))
  const skipReason = mailRecipientSkipReason(user as any)
  if (skipReason) throw new Error(`Email non envoyé : compte ${skipReason}.`)
  const settings = $app.settings()
  const senderAddress = `${settings.meta?.senderAddress || ''}`.trim()
  const senderName = `${settings.meta?.senderName || 'Gestion PocketBase'}`.trim()
  const recipientAddress = `${user.email() || ''}`.trim()
  if (!settings.smtp?.enabled) {
    throw new Error("Email non envoyé : SMTP est désactivé dans l'administration du serveur.")
  }
  if (!`${settings.smtp.host || ''}`.trim()) {
    throw new Error("Email non envoyé : l'hôte SMTP n'est pas configuré.")
  }
  if (!senderAddress) throw new Error("Email non envoyé : l'adresse expéditeur SMTP n'est pas configurée.")
  if (!recipientAddress) throw new Error("Email non envoyé : l'utilisateur n'a pas d'adresse email.")
  const html = `<h2>${escapeHtml(payload.title)}</h2><p>${escapeHtml(payload.message)}</p><p><strong>Instance :</strong> ${escapeHtml(payload.instanceName)}</p><p><small>${escapeHtml(payload.occurredAt)}</small></p>`
  const message = new MailerMessage({
    from: { address: senderAddress, name: senderName },
    to: [{ address: recipientAddress }],
    subject: `[PocketHost] ${payload.title}`,
    html,
  })
  $app.newMailClient().send(message)
}

const sendWebhookDelivery = (delivery: core.Record, payload: MonitoringNotificationPayload) => {
  const policy = findPolicy(delivery.getString('instance'))
  if (!policy) throw new Error('Configuration de surveillance introuvable.')
  const channel = delivery.getString('channel')
  const content = `**${payload.title}**\n${payload.message}\nInstance : \`${payload.instanceName}\`\n${payload.occurredAt}`
  const slackText = content.replace(/\*\*/g, '*').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const url =
    channel === 'discord'
      ? normalizeDiscordWebhook(policy.getString('discordWebhook'))
      : normalizeSlackWebhook(policy.getString('slackWebhook'))
  if (!url) throw new Error(`Webhook ${channel} non configuré.`)
  const response = $http.send({
    url,
    method: 'POST',
    timeout: 5,
    headers: { 'Content-Type': 'application/json', 'User-Agent': 'PocketHost-Monitor/1.0' },
    body: JSON.stringify(channel === 'discord' ? { content, allowed_mentions: { parse: [] } } : { text: slackText }),
  })
  if (response.statusCode < 200 || response.statusCode >= 300) {
    throw new Error(`Webhook ${channel} : HTTP ${response.statusCode}`)
  }
}

const processDelivery = (delivery: core.Record) => {
  if (delivery.getString('status') !== 'pending') return delivery
  const attempts = delivery.getInt('attempts') + 1
  delivery.set('attempts', attempts)
  delivery.set('lastAttemptAt', nowIso())
  try {
    const payload = deliveryPayload(delivery)
    if (delivery.getString('channel') === 'email') sendEmailDelivery(delivery, payload)
    else sendWebhookDelivery(delivery, payload)
    delivery.set('status', 'sent')
    delivery.set('deliveredAt', nowIso())
    delivery.set('lastError', '')
  } catch (error) {
    delivery.set('lastError', `${error}`.slice(0, 1000))
    if (attempts >= MONITORING_MAX_DELIVERY_ATTEMPTS) {
      delivery.set('status', 'abandoned')
      delivery.set('nextAttemptAt', '')
    } else {
      delivery.set('nextAttemptAt', new Date(Date.now() + monitoringRetryDelayMs(attempts)).toISOString())
    }
  }
  $app.save(delivery)
  return delivery
}

export const ProcessInstanceMonitoringDeliveries = () => {
  const deliveries = $app.findRecordsByFilter(
    DELIVERY_COLLECTION,
    'status = "pending" && nextAttemptAt <= {:now}',
    'nextAttemptAt',
    100,
    0,
    { now: formatPocketBaseDate(Date.now()) }
  )
  for (const delivery of deliveries) processDelivery(delivery)
  return { processed: deliveries.length }
}

export const CollectInstanceMetricsAndMonitoring = () => {
  const snapshot = readDockerMetricsSnapshot()
  const metrics = CollectInstanceResourceMetrics(snapshot)
  const monitoring = CollectInstanceMonitoring(snapshot)
  const deliveries = ProcessInstanceMonitoringDeliveries()
  return { metrics, monitoring, deliveries }
}

export const PurgeExpiredInstanceHealthChecks = () => {
  const cutoff = formatPocketBaseDate(Date.now() - HEALTH_RETENTION_MS)
  let deleted = 0
  for (;;) {
    const records = $app.findRecordsByFilter(CHECK_COLLECTION, 'checkedAt < {:cutoff}', 'checkedAt', 1000, 0, {
      cutoff,
    })
    if (records.length === 0) break
    for (const record of records) {
      $app.delete(record)
      deleted += 1
    }
  }
  return { deleted }
}

const readMonitoringHistoryRange = (e: core.RequestEvent): MonitoringHistoryRange => {
  const requested = `${e.request.url.query().get('range') || '24h'}`
  return requested in monitoringHistoryRanges ? (requested as MonitoringHistoryRange) : '24h'
}

const queryMonitoringHistory = (instanceId: string, range: MonitoringHistoryRange) => {
  const config = monitoringHistoryRanges[range]
  const rows = arrayOf(
    new DynamicModel({
      periodStartUnix: 0,
      totalChecks: 0,
      successfulChecks: 0,
      averageLatencyMs: 0,
      maxLatencyMs: 0,
    })
  ) as Array<{
    periodStartUnix: number
    totalChecks: number
    successfulChecks: number
    averageLatencyMs: number
    maxLatencyMs: number
  }>
  $app
    .db()
    .newQuery(
      `SELECT
        CAST(strftime('%s', checkedAt) / {:bucketSeconds} AS INTEGER) * {:bucketSeconds} AS periodStartUnix,
        COUNT(*) AS totalChecks,
        SUM(CASE WHEN result = 'healthy' THEN 1 ELSE 0 END) AS successfulChecks,
        AVG(latencyMs) AS averageLatencyMs,
        MAX(latencyMs) AS maxLatencyMs
      FROM instance_health_checks
      WHERE instance = {:instance} AND checkedAt >= {:cutoff}
      GROUP BY periodStartUnix
      ORDER BY periodStartUnix`
    )
    .bind({
      bucketSeconds: config.bucketSeconds,
      instance: instanceId,
      cutoff: formatPocketBaseDate(Date.now() - config.durationMs),
    })
    .all(rows as any)

  return rows.map((row) => {
    const totalChecks = Number(row.totalChecks || 0)
    const successfulChecks = Number(row.successfulChecks || 0)
    return {
      collectedAt: new Date(Number(row.periodStartUnix) * 1000).toISOString(),
      totalChecks,
      successfulChecks,
      availabilityPercent: totalChecks > 0 ? (successfulChecks / totalChecks) * 100 : null,
      averageLatencyMs: Math.round(Number(row.averageLatencyMs || 0)),
      maxLatencyMs: Math.round(Number(row.maxLatencyMs || 0)),
    }
  })
}

const serializeIncident = (incident: core.Record) => ({
  id: incident.id,
  type: incident.getString('type'),
  status: incident.getString('status'),
  openedAt: incident.getString('openedAt'),
  resolvedAt: incident.getString('resolvedAt'),
  value: Number(incident.get('value') || 0),
  threshold: Number(incident.get('threshold') || 0),
  message: incident.getString('message'),
  details: incident.get('details') || {},
  sourceId: incident.getString('sourceId'),
  created: incident.getString('created'),
  updated: incident.getString('updated'),
})

export const HandleInstanceMonitoringGet = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)
  return e.json(200, monitoringResponse(instance, findPolicy(instance.id)))
}

export const HandleInstanceMonitoringUpdate = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)
  let data = new DynamicModel({
    enabled: false,
    healthEnabled: true,
    healthPath: MONITORING_DEFAULTS.healthPath,
    healthFailureCount: MONITORING_DEFAULTS.healthFailureCount,
    cpuEnabled: true,
    cpuThresholdPercent: MONITORING_DEFAULTS.cpuThresholdPercent,
    cpuSustainMinutes: MONITORING_DEFAULTS.cpuSustainMinutes,
    memoryEnabled: true,
    memoryThresholdPercent: MONITORING_DEFAULTS.memoryThresholdPercent,
    memorySustainMinutes: MONITORING_DEFAULTS.memorySustainMinutes,
    backupAlertsEnabled: true,
    emailEnabled: true,
    discordEnabled: false,
    discordWebhook: '',
    clearDiscordWebhook: false,
    slackEnabled: false,
    slackWebhook: '',
    clearSlackWebhook: false,
  }) as Record<string, any>
  e.bindBody(data)
  data = JSON.parse(JSON.stringify(data))

  const policy = findPolicy(instance.id) || createPolicy(instance)
  const wasEnabled = policy.getBool('enabled')
  const wasHealthEnabled = policy.getBool('healthEnabled')
  const healthPath = normalizeHealthPath(data.healthPath)
  const submittedDiscord = `${data.discordWebhook || ''}`.trim()
  const submittedSlack = `${data.slackWebhook || ''}`.trim()
  if (data.clearDiscordWebhook) policy.set('discordWebhook', '')
  else if (submittedDiscord) policy.set('discordWebhook', normalizeDiscordWebhook(submittedDiscord))
  if (data.clearSlackWebhook) policy.set('slackWebhook', '')
  else if (submittedSlack) policy.set('slackWebhook', normalizeSlackWebhook(submittedSlack))

  if (data.discordEnabled && !policy.getString('discordWebhook'))
    throw new BadRequestError('Ajoutez une URL Discord valide.')
  if (data.slackEnabled && !policy.getString('slackWebhook')) throw new BadRequestError('Ajoutez une URL Slack valide.')

  policy.set('user', instance.getString('uid'))
  policy.set('enabled', !!data.enabled)
  policy.set('healthEnabled', !!data.healthEnabled)
  policy.set('healthPath', healthPath)
  policy.set(
    'healthFailureCount',
    clampNumber(data.healthFailureCount, MONITORING_DEFAULTS.healthFailureCount, 1, 10, true)
  )
  policy.set('cpuEnabled', !!data.cpuEnabled)
  policy.set(
    'cpuThresholdPercent',
    clampNumber(data.cpuThresholdPercent, MONITORING_DEFAULTS.cpuThresholdPercent, 1, 100)
  )
  policy.set(
    'cpuSustainMinutes',
    clampNumber(data.cpuSustainMinutes, MONITORING_DEFAULTS.cpuSustainMinutes, 1, 60, true)
  )
  policy.set('memoryEnabled', !!data.memoryEnabled)
  policy.set(
    'memoryThresholdPercent',
    clampNumber(data.memoryThresholdPercent, MONITORING_DEFAULTS.memoryThresholdPercent, 1, 100)
  )
  policy.set(
    'memorySustainMinutes',
    clampNumber(data.memorySustainMinutes, MONITORING_DEFAULTS.memorySustainMinutes, 1, 60, true)
  )
  policy.set('backupAlertsEnabled', !!data.backupAlertsEnabled)
  policy.set('emailEnabled', !!data.emailEnabled)
  policy.set('discordEnabled', !!data.discordEnabled)
  policy.set('slackEnabled', !!data.slackEnabled)
  if (!wasEnabled && data.enabled) {
    policy.set('lastHealthStatus', 'unknown')
    policy.set('graceUntil', new Date(Date.now() + MONITORING_DEFAULTS.startupGraceMs).toISOString())
    resetRuntimeStreaks(policy)
  } else if (data.enabled && data.healthEnabled && !wasHealthEnabled) {
    policy.set('lastHealthStatus', 'unknown')
    policy.set('graceUntil', new Date(Date.now() + MONITORING_DEFAULTS.startupGraceMs).toISOString())
    policy.set('healthFailureStreak', 0)
    policy.set('healthSuccessStreak', 0)
  }
  if (wasEnabled && !data.enabled) {
    pauseRuntimeMonitoring(policy, instance, true)
  } else {
    if (data.enabled && !data.healthEnabled) {
      resolveIncident(policy, instance, findOpenIncident(instance.id, 'health'), 'Sonde HTTP désactivée.', false)
      policy.set('lastHealthStatus', instance.getBool('power') ? 'unknown' : 'paused')
      policy.set('lastError', '')
      policy.set('healthFailureStreak', 0)
      policy.set('healthSuccessStreak', 0)
    }
    if (data.enabled && !data.cpuEnabled) {
      resolveIncident(policy, instance, findOpenIncident(instance.id, 'cpu'), 'Alerte CPU désactivée.', false)
      policy.set('cpuHighStreak', 0)
      policy.set('cpuNormalStreak', 0)
    }
    if (data.enabled && !data.memoryEnabled) {
      resolveIncident(policy, instance, findOpenIncident(instance.id, 'memory'), 'Alerte mémoire désactivée.', false)
      policy.set('memoryHighStreak', 0)
      policy.set('memoryNormalStreak', 0)
    }
    if (data.enabled && !data.backupAlertsEnabled) {
      const backupIncidents = $app.findRecordsByFilter(
        INCIDENT_COLLECTION,
        'instance = {:instance} && type = "backup" && status = "open"',
        'openedAt',
        100,
        0,
        { instance: instance.id }
      )
      for (const incident of backupIncidents) {
        resolveIncident(policy, instance, incident, 'Alerte de sauvegarde désactivée.', false)
      }
    }
    $app.save(policy)
  }
  return e.json(200, monitoringResponse(instance, policy))
}

export const HandleInstanceMonitoringHistory = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)
  const range = readMonitoringHistoryRange(e)
  const points = queryMonitoringHistory(instance.id, range)
  const totalChecks = points.reduce((sum, point) => sum + point.totalChecks, 0)
  const successfulChecks = points.reduce((sum, point) => sum + point.successfulChecks, 0)
  const latencyWeightedSum = points.reduce((sum, point) => sum + point.averageLatencyMs * point.totalChecks, 0)
  return e.json(200, {
    range,
    bucketSeconds: monitoringHistoryRanges[range].bucketSeconds,
    summary: {
      totalChecks,
      successfulChecks,
      availabilityPercent: totalChecks > 0 ? (successfulChecks / totalChecks) * 100 : null,
      averageLatencyMs: totalChecks > 0 ? Math.round(latencyWeightedSum / totalChecks) : null,
    },
    points,
    collectedAt: nowIso(),
  })
}

export const HandleInstanceMonitoringIncidents = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)
  const rawCursor = `${e.request.url.query().get('cursor') || ''}`
  const cursor = rawCursor && Number.isFinite(Date.parse(rawCursor)) ? formatPocketBaseDate(Date.parse(rawCursor)) : ''
  const cursorFilter = cursor ? ' && created < {:cursor}' : ''
  const records = $app.findRecordsByFilter(
    INCIDENT_COLLECTION,
    `instance = {:instance}${cursorFilter}`,
    '-created',
    INCIDENT_PAGE_SIZE + 1,
    0,
    { instance: instance.id, cursor }
  )
  const hasMore = records.length > INCIDENT_PAGE_SIZE
  const incidents = records.slice(0, INCIDENT_PAGE_SIZE).map(serializeIncident)
  return e.json(200, {
    incidents,
    nextCursor: hasMore ? incidents.at(-1)?.created || '' : '',
  })
}

export const HandleInstanceMonitoringTest = (e: core.RequestEvent) => {
  const authRecord = requireAuthRecord(e.auth)
  const instance = findInstance(pathValue(e, 'id'))
  assertInstanceAccess(instance, authRecord)
  const policy = findPolicy(instance.id)
  if (!policy) throw new BadRequestError('Enregistrez la configuration avant le test.')
  const payload: MonitoringNotificationPayload = {
    title: `TEST · Surveillance · ${instance.getString('subdomain')}`,
    message: 'Les notifications de surveillance PocketHost sont correctement configurées.',
    instanceId: instance.id,
    instanceName: instance.getString('subdomain'),
    type: 'test',
    phase: 'test',
    occurredAt: nowIso(),
  }
  const deliveries: core.Record[] = []
  if (policy.getBool('emailEnabled')) deliveries.push(queueDelivery(instance, null, 'test', 'email', payload))
  if (policy.getBool('discordEnabled') && policy.getString('discordWebhook')) {
    deliveries.push(queueDelivery(instance, null, 'test', 'discord', payload))
  }
  if (policy.getBool('slackEnabled') && policy.getString('slackWebhook')) {
    deliveries.push(queueDelivery(instance, null, 'test', 'slack', payload))
  }
  if (deliveries.length === 0) throw new BadRequestError('Activez au moins un canal de notification.')
  const results = deliveries.map(processDelivery).map((delivery) => ({
    channel: delivery.getString('channel'),
    status: delivery.getString('status'),
    error: delivery.getString('lastError'),
  }))
  return e.json(200, { results })
}

export const HandleInstanceBackupMonitoringUpdate = (e: core.RecordEvent) => {
  const status = e.record.getString('status')
  const previous = e.record.original().getString('status')
  if (status === previous || (status !== 'failed' && status !== 'ready')) return
  const instance = findInstance(e.record.getString('instance'))
  const policy = findPolicy(instance.id)
  if (!policy?.getBool('enabled') || !policy.getBool('backupAlertsEnabled')) return
  if (status === 'failed') {
    const label = e.record.getString('name') || e.record.getString('filename') || e.record.id
    openIncident(
      policy,
      instance,
      'backup',
      `La sauvegarde ${label} a échoué : ${e.record.getString('error') || 'erreur inconnue'}.`,
      0,
      0,
      { backupId: e.record.id, kind: e.record.getString('kind') },
      e.record.id
    )
    return
  }
  const openBackups = $app.findRecordsByFilter(
    INCIDENT_COLLECTION,
    'instance = {:instance} && type = "backup" && status = "open"',
    'openedAt',
    100,
    0,
    { instance: instance.id }
  )
  for (const incident of openBackups) {
    resolveIncident(
      policy,
      instance,
      incident,
      `Une nouvelle sauvegarde de ${instance.getString('subdomain')} a réussi.`
    )
  }
}
