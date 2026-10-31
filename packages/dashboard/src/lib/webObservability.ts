import {
  WEB_NOTIFICATION_EVENTS,
  WEB_NOTIFICATION_LABELS,
  WEB_PLATFORM_EVENTS,
  WEB_WEBHOOK_FORMATS,
  webNotificationTitle,
  type WebWebhookFormat,
} from '../../../pockethost/src/common/webNotifications'
export type RuntimeLogRecord = {
  id: string
  timestamp: string
  stream: 'stdout' | 'stderr'
  level: string
  message: string
}
export type RuntimeLogPage = {
  items: RuntimeLogRecord[]
  generation: string
  deploymentId: string
  checkedAt: string
  state: string
  olderCursor: string
  newerCursor: string
  hasOlder: boolean
  hasNewer: boolean
  reset: boolean
  truncated: boolean
  retention: { hours: number; maxBytes: number }
}
export const notificationEvents: Record<string, string> = Object.fromEntries(
  WEB_NOTIFICATION_EVENTS.map((event) => [event, WEB_NOTIFICATION_LABELS[event].title])
)
export const platformNotificationEvents: Record<string, string> = Object.fromEntries(
  WEB_PLATFORM_EVENTS.map((event) => [event, WEB_NOTIFICATION_LABELS[event].title])
)
export const notificationEventTitle = (event: string) =>
  event === 'test' ? 'Test simple' : webNotificationTitle(event)
export const webhookFormats: Record<WebWebhookFormat, string> = {
  generic: 'JSON signé (générique)',
  slack: 'Slack (webhook entrant)',
  discord: 'Discord',
}
export const WEBHOOK_FORMATS = WEB_WEBHOOK_FORMATS
export type NotificationSettings = {
  paused?: boolean
  pausedLegacy?: boolean
  revision: string
  webhook: {
    enabled: boolean
    configured: boolean
    endpointHost: string
    hasSecret: boolean
    format: WebWebhookFormat
  }
  email: {
    enabled: boolean
    configured: boolean
    recipients: string[]
    projectAdmins: boolean
    projectAdminCount?: number
  }
  events: string[]
  cooldownSeconds: number
  smtpAvailable: boolean
  inProject?: boolean
  platformOperated?: boolean
  healthMonitoring?: 'enabled' | 'disabled' | 'not_applicable'
  /** Secret HMAC généré : renvoyé une seule fois par l'enregistrement. */
  generatedSecret?: string
  // Canal plateforme uniquement.
  copyAppEvents?: boolean
  availableEvents?: string[]
  worker?: { lastClaimAt: string; down: boolean }
  disk?: { low: boolean; changedAt: string }
  recovery?: { checkedAt: string }
}
export type NotificationDelivery = {
  id: string
  channel: string
  event: string
  status: string
  attempts: number
  nextAttemptAt: string
  created: string
  sentAt: string
  httpStatus: number
  lastError: string
  trigger: string
}
export type NotificationTestResult = { result: NotificationDelivery; done: boolean }
