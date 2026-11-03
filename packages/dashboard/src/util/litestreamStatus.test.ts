import { describe, expect, it } from 'vitest'
import { litestreamStatus } from './litestreamStatus'

const report = () => ({
  enabled: true,
  positionState: 'caught-up',
  service: { state: 'online' },
  local: { status: 'ok' },
  metrics: { disk_full: 0 },
  remote: { ok: true, partial: false },
})
describe('Litestream summary', () => {
  it('confirms sync only with healthy readings and complete remote confirmation', () => {
    expect(litestreamStatus(report()).label).toBe('Synchro OK')
    expect(litestreamStatus({ ...report(), remote: { ok: true, partial: true } }).label).toBe('Synchro active')
    expect(litestreamStatus({ ...report(), positionState: 'pending' }).label).toBe('Synchronisation en cours')
    expect(litestreamStatus({ ...report(), positionState: 'unknown' }).label).toBe('À vérifier')
  })
  it('does not turn a partial inventory into a failure or a confirmed backup', () => {
    const result = litestreamStatus({ ...report(), positionState: 'unknown', remote: { ok: true, partial: true } })
    expect(result.label).toBe('Synchro active')
    expect(result.tone).toBe('info')
    expect(result.message).toContain('ne confirme pas')
  })
  it('prioritizes real failures over matching positions', () => {
    for (const change of [
      { service: { state: 'stopped' } },
      { local: { status: 'unavailable' } },
      { metrics: { disk_full: 1 } },
      { remote: { ok: false, partial: false } },
    ]) {
      expect(litestreamStatus({ ...report(), ...change }).tone).toBe('warning')
    }
  })
  it('does not show a failure when replication is deliberately disabled', () => {
    expect(litestreamStatus({ ...report(), enabled: false }).tone).toBe('neutral')
  })
})
