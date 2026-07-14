import { describe, expect, it } from 'vitest'
import {
  evaluateHealthSignal,
  evaluateThresholdSignal,
  formatMonitoringPercent,
  monitoringRetryDelayMs,
  normalizeDiscordWebhook,
  normalizeHealthPath,
  normalizeMonitoringNotificationPayload,
  normalizeSlackWebhook,
  shouldRetryMonitoringDelivery,
} from './instanceMonitoring'

describe('instance monitoring state machine', () => {
  it('opens health incidents after the configured failure streak and resolves after two successes', () => {
    let state = { failureStreak: 0, successStreak: 0, incidentOpen: false }
    expect((state = evaluateHealthSignal(state, false, 3).state).incidentOpen).toBe(false)
    expect((state = evaluateHealthSignal(state, false, 3).state).incidentOpen).toBe(false)
    const opened = evaluateHealthSignal(state, false, 3)
    expect(opened.transition).toBe('open')
    state = opened.state

    expect((state = evaluateHealthSignal(state, true, 3).state).incidentOpen).toBe(true)
    const resolved = evaluateHealthSignal(state, true, 3)
    expect(resolved.transition).toBe('resolve')
    expect(resolved.state.incidentOpen).toBe(false)
  })

  it('uses hysteresis before resolving resource incidents', () => {
    let state = { highStreak: 0, normalStreak: 0, incidentOpen: false }
    for (let i = 0; i < 4; i += 1) state = evaluateThresholdSignal(state, 90, 85, 5).state
    const opened = evaluateThresholdSignal(state, 90, 85, 5)
    expect(opened.transition).toBe('open')
    state = opened.state

    expect(evaluateThresholdSignal(state, 82, 85, 5).state.normalStreak).toBe(0)
    state = evaluateThresholdSignal(state, 79, 85, 5).state
    state = evaluateThresholdSignal(state, 79, 85, 5).state
    const resolved = evaluateThresholdSignal(state, 79, 85, 5)
    expect(resolved.transition).toBe('resolve')
  })
})

describe('instance monitoring input validation', () => {
  it('accepts relative health paths and rejects absolute targets', () => {
    expect(normalizeHealthPath(' /api/health ')).toBe('/api/health')
    expect(() => normalizeHealthPath('https://internal.example')).toThrow()
    expect(() => normalizeHealthPath('//internal.example')).toThrow()
  })

  it('only accepts official Discord and Slack webhook hosts', () => {
    expect(normalizeDiscordWebhook('https://discord.com/api/webhooks/123/token_ABC')).toContain('discord.com')
    expect(normalizeSlackWebhook('https://hooks.slack.com/services/T01/B02/token_ABC')).toContain('hooks.slack.com')
    expect(() => normalizeDiscordWebhook('https://example.com/api/webhooks/123/token')).toThrow()
    expect(() => normalizeSlackWebhook('https://localhost/services/T/B/token')).toThrow()
  })

  it('uses bounded retry delays', () => {
    expect([0, 1, 2, 3, 4, 9].map(monitoringRetryDelayMs)).toEqual([0, 60_000, 300_000, 900_000, 3_600_000, 3_600_000])
  })

  it('formats percentages without Intl, which is unavailable in PocketBase hooks', () => {
    expect(formatMonitoringPercent(92.34)).toBe('92,3')
    expect(formatMonitoringPercent(100)).toBe('100')
  })

  it('does not retry manual test deliveries', () => {
    expect(shouldRetryMonitoringDelivery('test', 1)).toBe(false)
    expect(shouldRetryMonitoringDelivery('opened', 1)).toBe(true)
    expect(shouldRetryMonitoringDelivery('opened', 5)).toBe(false)
  })

  it('normalizes serialized notification payloads before delivery', () => {
    const payload = {
      title: 'TEST · Surveillance · avenirassur',
      message: 'Les notifications sont correctement configurées.',
      instanceId: 'a8k0ofg6ojwiiba',
      instanceName: 'avenirassur',
      type: 'test',
      phase: 'test',
      occurredAt: '2026-07-14T10:28:57.558Z',
    }

    expect(normalizeMonitoringNotificationPayload(JSON.stringify(payload))).toEqual(payload)
    expect(normalizeMonitoringNotificationPayload(payload)).toEqual(payload)
  })

  it('rejects incomplete notification payloads with an explicit error', () => {
    expect(() => normalizeMonitoringNotificationPayload(JSON.stringify({ type: 'test', phase: 'test' }))).toThrow(
      'champ title absent'
    )
  })
})
