export const MONITORING_DEFAULTS = {
  healthPath: '/api/health',
  healthFailureCount: 3,
  healthRecoveryCount: 2,
  cpuThresholdPercent: 85,
  cpuSustainMinutes: 5,
  memoryThresholdPercent: 85,
  memorySustainMinutes: 5,
  resourceRecoveryCount: 3,
  resourceRecoveryMargin: 5,
  startupGraceMs: 2 * 60 * 1000,
  requestTimeoutSeconds: 5,
} as const

export type MonitoringTransition = 'open' | 'resolve' | null

export type HealthSignalState = {
  failureStreak: number
  successStreak: number
  incidentOpen: boolean
}

export type ThresholdSignalState = {
  highStreak: number
  normalStreak: number
  incidentOpen: boolean
}

export type MonitoringNotificationPayload = {
  title: string
  message: string
  instanceId: string
  instanceName: string
  type: 'health' | 'cpu' | 'memory' | 'backup' | 'test'
  phase: 'opened' | 'resolved' | 'test'
  occurredAt: string
}

const notificationPayloadText = (value: unknown, field: string) => {
  const text = `${typeof value === 'string' ? value : ''}`.trim()
  if (!text) throw new Error(`Payload de notification invalide : champ ${field} absent.`)
  return text
}

export const normalizeMonitoringNotificationPayload = (value: unknown): MonitoringNotificationPayload => {
  let parsed = value
  try {
    if (typeof value === 'string') parsed = JSON.parse(value)
    else if (value && typeof value === 'object') parsed = JSON.parse(JSON.stringify(value))
  } catch {
    throw new Error('Payload de notification invalide.')
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Payload de notification invalide.')
  }

  const payload = parsed as Record<string, unknown>
  const type = notificationPayloadText(payload.type, 'type')
  const phase = notificationPayloadText(payload.phase, 'phase')
  if (!['health', 'cpu', 'memory', 'backup', 'test'].includes(type)) {
    throw new Error('Payload de notification invalide : type inconnu.')
  }
  if (!['opened', 'resolved', 'test'].includes(phase)) {
    throw new Error('Payload de notification invalide : phase inconnue.')
  }

  return {
    title: notificationPayloadText(payload.title, 'title'),
    message: notificationPayloadText(payload.message, 'message'),
    instanceId: notificationPayloadText(payload.instanceId, 'instanceId'),
    instanceName: notificationPayloadText(payload.instanceName, 'instanceName'),
    type: type as MonitoringNotificationPayload['type'],
    phase: phase as MonitoringNotificationPayload['phase'],
    occurredAt: notificationPayloadText(payload.occurredAt, 'occurredAt'),
  }
}

export const normalizeHealthPath = (value: unknown) => {
  const path = `${typeof value === 'string' ? value : ''}`.trim()
  if (!path || path.length > 200) throw new Error('Le chemin de santé doit contenir entre 1 et 200 caractères.')
  if (!path.startsWith('/') || path.startsWith('//') || /[\u0000-\u001f\u007f]/.test(path)) {
    throw new Error('Le chemin de santé doit être un chemin relatif commençant par /.')
  }
  if (/^[^?#]*\\/.test(path) || /^\/\w+:\/\//i.test(path)) {
    throw new Error('Le chemin de santé est invalide.')
  }
  return path
}

const normalizeWebhook = (value: unknown) => `${typeof value === 'string' ? value : ''}`.trim()

export const normalizeDiscordWebhook = (value: unknown) => {
  const url = normalizeWebhook(value)
  if (!url) return ''
  if (
    url.length > 2000 ||
    !/^https:\/\/(?:discord\.com|discordapp\.com)\/api\/webhooks\/[0-9]+\/[A-Za-z0-9._-]+$/.test(url)
  ) {
    throw new Error("L'URL Discord doit être une URL officielle https://discord.com/api/webhooks/…")
  }
  return url
}

export const normalizeSlackWebhook = (value: unknown) => {
  const url = normalizeWebhook(value)
  if (!url) return ''
  if (
    url.length > 2000 ||
    !/^https:\/\/hooks\.slack\.com\/services\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/.test(url)
  ) {
    throw new Error("L'URL Slack doit être une URL officielle https://hooks.slack.com/services/…")
  }
  return url
}

export const evaluateHealthSignal = (
  state: HealthSignalState,
  healthy: boolean,
  failuresRequired: number,
  recoveriesRequired = MONITORING_DEFAULTS.healthRecoveryCount
) => {
  const next = { ...state }
  let transition: MonitoringTransition = null

  if (healthy) {
    next.failureStreak = 0
    next.successStreak += 1
    if (state.incidentOpen && next.successStreak >= recoveriesRequired) {
      next.incidentOpen = false
      transition = 'resolve'
    }
  } else {
    next.successStreak = 0
    next.failureStreak += 1
    if (!state.incidentOpen && next.failureStreak >= failuresRequired) {
      next.incidentOpen = true
      transition = 'open'
    }
  }

  return { state: next, transition }
}

export const evaluateThresholdSignal = (
  state: ThresholdSignalState,
  value: number,
  threshold: number,
  highSamplesRequired: number,
  normalSamplesRequired = MONITORING_DEFAULTS.resourceRecoveryCount,
  recoveryMargin = MONITORING_DEFAULTS.resourceRecoveryMargin
) => {
  const next = { ...state }
  let transition: MonitoringTransition = null

  if (value >= threshold) {
    next.highStreak += 1
    next.normalStreak = 0
    if (!state.incidentOpen && next.highStreak >= highSamplesRequired) {
      next.incidentOpen = true
      transition = 'open'
    }
  } else if (value <= Math.max(0, threshold - recoveryMargin)) {
    next.highStreak = 0
    next.normalStreak += 1
    if (state.incidentOpen && next.normalStreak >= normalSamplesRequired) {
      next.incidentOpen = false
      transition = 'resolve'
    }
  } else {
    next.highStreak = 0
    next.normalStreak = 0
  }

  return { state: next, transition }
}

export const classifyHealthError = (error: unknown) => {
  const message = `${error}`.toLowerCase()
  if (message.includes('timeout') || message.includes('deadline exceeded')) return 'timeout'
  if (message.includes('no such host') || message.includes('dns') || message.includes('resolve')) return 'dns'
  if (message.includes('tls') || message.includes('certificate') || message.includes('x509')) return 'tls'
  if (message.includes('connect') || message.includes('network') || message.includes('refused')) return 'network'
  return 'unknown'
}

export const monitoringRetryDelayMs = (attempts: number) => {
  const delayMinutes = [0, 1, 5, 15, 60]
  return delayMinutes[Math.min(Math.max(0, attempts), delayMinutes.length - 1)]! * 60 * 1000
}

export const MONITORING_MAX_DELIVERY_ATTEMPTS = 5

export const formatMonitoringPercent = (value: number) => `${Math.round(value * 10) / 10}`.replace('.', ',')

export const monitoringHistoryRanges = {
  '24h': { durationMs: 24 * 60 * 60 * 1000, bucketSeconds: 60 },
  '7d': { durationMs: 7 * 24 * 60 * 60 * 1000, bucketSeconds: 10 * 60 },
  '30d': { durationMs: 30 * 24 * 60 * 60 * 1000, bucketSeconds: 60 * 60 },
} as const

export type MonitoringHistoryRange = keyof typeof monitoringHistoryRanges
