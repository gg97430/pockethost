import { describe, expect, it } from 'vitest'
import { defaultWebProjectLimits, normalizeWebProjectLimits } from './webProjectLimits'

describe('project limits', () => {
  it('keeps both controls off for existing projects and independent when enabled', () => {
    expect(normalizeWebProjectLimits()).toEqual(defaultWebProjectLimits())
    expect(normalizeWebProjectLimits({ traffic: { enabled: true } }).resources.enabled).toBe(false)
    expect(normalizeWebProjectLimits({ resources: { enabled: true } }).traffic.enabled).toBe(false)
  })
  it.each([
    null,
    [],
    { traffic: true },
    { traffic: { enabled: 'true' } },
    { resources: { memoryMiB: 0 } },
    { resources: { cpuMillicores: 500.5 } },
    { resources: { buildMinutes: 31 } },
    { traffic: { requestsPerMinute: Infinity } },
    { resources: { privileged: true } },
  ])('rejects invalid policy %j', (value) => {
    expect(() => normalizeWebProjectLimits(value)).toThrow()
  })
})
