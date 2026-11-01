import { describe, expect, it } from 'vitest'
import {
  WEB_NOTIFICATION_DEFAULT_EVENTS,
  WEB_NOTIFICATION_EVENTS,
  WEB_NOTIFICATION_LABELS,
  WEB_PLATFORM_DEFAULT_EVENTS,
  WEB_PLATFORM_EVENTS,
  webNotificationTitle,
} from './webNotifications'

describe('web notification labels', () => {
  it('labels every event with a title, a sentence and a tab', () => {
    for (const event of [...WEB_NOTIFICATION_EVENTS, ...WEB_PLATFORM_EVENTS, 'test', 'alerts_grouped'] as const) {
      const label = WEB_NOTIFICATION_LABELS[event]
      expect(label.title.trim()).not.toBe('')
      expect(label.sentence.trim()).not.toBe('')
      expect(typeof label.tab).toBe('string')
    }
  })
  it('keeps defaults inside the event lists and host capacity opt-in for applications', () => {
    for (const event of WEB_NOTIFICATION_DEFAULT_EVENTS) expect(WEB_NOTIFICATION_EVENTS).toContain(event)
    for (const event of WEB_PLATFORM_DEFAULT_EVENTS) expect(WEB_PLATFORM_EVENTS).toContain(event)
    expect(WEB_NOTIFICATION_DEFAULT_EVENTS).not.toContain('disk_low')
    expect(WEB_PLATFORM_DEFAULT_EVENTS).not.toContain('app_backup_failed')
    expect(webNotificationTitle('unknown_event')).toBe('unknown_event')
  })
})
