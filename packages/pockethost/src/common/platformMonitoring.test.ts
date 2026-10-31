import { describe, expect, it } from 'vitest'
import {
  advanceMonitorSignals,
  monitoringReport,
  monitoringStale,
  parseMonitoringReport,
  type MonitorCheck,
} from './platformMonitoring'

const check = (state: MonitorCheck['state'], id = 'database:abc'): MonitorCheck => ({
  id,
  name: 'BDD',
  group: 'databases',
  state,
  detail: 'Contrôle local',
})
describe('operator monitoring', () => {
  it('requires two errors and sends only one incident until recovery', () => {
    const first = advanceMonitorSignals([check('error')])
    expect(first.opened).toEqual([])
    const second = advanceMonitorSignals([check('error')], first.signals)
    expect(second.opened).toHaveLength(1)
    expect(advanceMonitorSignals([check('error')], second.signals).opened).toEqual([])
    const healthy = advanceMonitorSignals([check('ok')], second.signals)
    expect(healthy.recovered).toEqual([])
    expect(advanceMonitorSignals([check('ok')], healthy.signals).recovered).toEqual(['BDD'])
  })
  it('does not alert on a single failure during a scheduled backup', () => {
    const first = advanceMonitorSignals([check('error')])
    const second = advanceMonitorSignals([check('ok')], first.signals)
    expect(second.opened).toEqual([])
    expect(second.recovered).toEqual([])
  })
  it('alerts on sustained warnings while paused resources are expected', () => {
    const first = advanceMonitorSignals([check('warning'), check('paused', 'database:stopped')])
    const second = advanceMonitorSignals([check('warning'), check('paused', 'database:stopped')], first.signals)
    expect(second.opened.map((row) => row.id)).toEqual(['database:abc'])
  })
  it('never reports recovery from a missing or unknown measurement', () => {
    const first = advanceMonitorSignals([check('error')])
    const active = advanceMonitorSignals([check('error')], first.signals)
    const missing = advanceMonitorSignals([], active.signals)
    expect(missing.recovered).toEqual([])
    expect(missing.signals['database:abc']?.active).toBe(true)
    expect(advanceMonitorSignals([check('unknown')], active.signals).recovered).toEqual([])
  })
  it('isolates counters between resources', () => {
    const first = advanceMonitorSignals([check('error', 'database:a'), check('ok', 'web:b')])
    expect(advanceMonitorSignals([check('ok', 'database:a'), check('error', 'web:b')], first.signals).opened).toEqual(
      []
    )
  })
  it('marks old and future snapshots stale', () => {
    const report = monitoringReport('host', 'app.example.test', [check('ok')], 1000000)
    expect(monitoringStale(report, 1000000 + 180001)).toBe(true)
    expect(monitoringStale(report, 1000000 - 30001)).toBe(true)
    expect(monitoringStale(report, 1000001)).toBe(false)
  })
  it('recomputes counts and status instead of trusting a persisted green summary', () => {
    const report = monitoringReport('host', 'app.example.test', [check('error')])
    report.state = 'ok'
    report.counts.error = 0
    expect(parseMonitoringReport(report).state).toBe('error')
    expect(parseMonitoringReport(report).counts.error).toBe(1)
  })
  it('drops unknown fields and unsafe links at the privileged file boundary', () => {
    const report = monitoringReport('host', 'app.example.test', [{ ...check('ok'), href: 'https://outside.test' }])
    Object.assign(report, { password: 'never-public' })
    Object.assign(report.checks[0]!, { token: 'never-public' })
    const safe = parseMonitoringReport(report)
    expect(JSON.stringify(safe)).not.toContain('never-public')
    expect(safe.checks[0]?.href).toBeUndefined()
  })
  it('rejects corrupt reports rather than presenting a healthy empty page', () => {
    expect(() => parseMonitoringReport({ version: 1, checks: [] })).toThrow()
    const report = monitoringReport('host', 'app.example.test', [check('ok')])
    ;(report.checks[0] as any).state = 'healthy'
    expect(() => parseMonitoringReport(report)).toThrow()
  })
})
