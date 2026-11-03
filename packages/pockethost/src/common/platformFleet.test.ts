import { describe, expect, it } from 'vitest'
import { parsePlatformFleet, type PlatformFleet } from './platformFleet'

const now = Date.parse('2026-11-03T12:00:00Z')
const fixture = (): PlatformFleet => ({
  version: 1,
  checkedAt: new Date(now).toISOString(),
  self: 'app4',
  servers: [
    {
      key: 'app3',
      hostname: 'app3.monappli.re',
      access: 'denied',
      checkedAt: new Date(now).toISOString(),
    },
    {
      key: 'app4',
      hostname: 'app4.monappli.re',
      access: 'granted',
      checkedAt: new Date(now).toISOString(),
      counts: {
        databases: 1,
        applications: 0,
        databasesRunning: 1,
        applicationsRunning: 0,
        deploymentsRunning: 0,
        deploymentsQueued: 1,
        deploymentsFailed24h: 0,
      },
      inventory: [
        {
          id: 'a'.repeat(15),
          kind: 'database',
          name: 'Base de recette',
          status: 'running',
          cpuPercent: 2,
          memoryBytes: 100,
          diskBytes: null,
        },
      ],
      truncated: false,
      metrics: {
        collectedAt: new Date(now).toISOString(),
        stale: false,
        cpuPercent: 12,
        memoryTotalBytes: 1000,
        memoryUsedBytes: 500,
        diskTotalBytes: 2000,
        diskUsedBytes: 100,
      },
      monitoring: {
        checkedAt: new Date(now).toISOString(),
        stale: false,
        state: 'ok',
        counts: { ok: 1, warning: 0, error: 0, unknown: 0, paused: 0 },
      },
    },
    {
      key: 'app5',
      hostname: 'app5.monappli.re',
      access: 'unavailable',
      checkedAt: new Date(now).toISOString(),
    },
  ],
})

describe('read-only fleet boundary', () => {
  it('keeps unavailable/unauthorized hosts separate from a healthy local host', () => {
    const value = parsePlatformFleet(fixture(), now)
    expect(value.servers.map((row) => row.access)).toEqual(['denied', 'granted', 'unavailable'])
    expect(value.servers[1]?.counts?.databases).toBe(1)
    expect(value.servers[1]?.metrics?.stale).toBe(false)
  })
  it('recomputes freshness and monitoring state rather than trusting a green summary', () => {
    const value = fixture()
    value.servers[1]!.monitoring!.counts.error = 1
    value.servers[1]!.metrics!.collectedAt = '2000-01-01T00:00:00Z'
    const result = parsePlatformFleet(value, now)
    expect(result.servers[1]?.metrics?.stale).toBe(true)
    expect(result.servers[1]?.monitoring?.state).toBe('error')
    value.servers[1]!.monitoring!.checkedAt = '2999-01-01T00:00:00Z'
    expect(parsePlatformFleet(value, now).servers[1]?.monitoring?.stale).toBe(true)
  })
  it('reconstructs safe DTOs and never accepts peer-provided URLs or secrets', () => {
    const value = fixture()
    Object.assign(value, { privateKey: 'sensitive' })
    Object.assign(value.servers[1]!, { email: 'sensitive', environment: 'sensitive', href: 'https://attacker.invalid' })
    Object.assign(value.servers[1]!.inventory![0]!, { password: 'sensitive', href: 'javascript:alert(1)' })
    Object.assign(value.servers[0]!, { inventory: [{ password: 'sensitive' }], counts: { secret: 'sensitive' } })
    const clean = parsePlatformFleet(value, now)
    expect(JSON.stringify(clean)).not.toContain('sensitive')
    expect(JSON.stringify(clean)).not.toContain('attacker')
    expect(JSON.stringify(clean)).not.toContain('javascript:')
    expect(clean.servers[0]?.inventory).toBeUndefined()
  })
  it('isolates corrupt known peers and refuses foreign/duplicate fleet identities', () => {
    const foreign = fixture()
    foreign.servers[2]!.hostname = 'attacker.invalid'
    expect(parsePlatformFleet(foreign, now).servers[2]?.access).toBe('unavailable')
    const duplicate = fixture()
    duplicate.servers[2] = duplicate.servers[1]!
    expect(() => parsePlatformFleet(duplicate, now)).toThrow()
    const invalid = fixture()
    invalid.servers[1]!.metrics!.cpuPercent = 101
    const safe = parsePlatformFleet(invalid, now)
    expect(safe.servers[1]).toEqual({
      key: 'app4',
      hostname: 'app4.monappli.re',
      access: 'unavailable',
      checkedAt: new Date(now).toISOString(),
    })
    expect(safe.servers[0]?.access).toBe('denied')
    expect(safe.servers[2]?.access).toBe('unavailable')
    invalid.servers[1]!.metrics!.cpuPercent = NaN
    expect(parsePlatformFleet(invalid, now).servers[1]?.access).toBe('unavailable')
    ;(invalid.servers[1] as any).key = 'app1'
    expect(() => parsePlatformFleet(invalid, now)).toThrow()
  })
  it('refuses malformed resource identities and duplicate rows', () => {
    const value = fixture()
    value.servers[1]!.inventory![0]!.id = '../../secret'
    expect(parsePlatformFleet(value, now).servers[1]?.access).toBe('unavailable')
    const duplicate = fixture()
    duplicate.servers[1]!.inventory!.push(duplicate.servers[1]!.inventory![0]!)
    expect(parsePlatformFleet(duplicate, now).servers[1]?.access).toBe('unavailable')
  })
  it('retains two healthy peers when the third response is corrupt', () => {
    const value = fixture()
    value.servers[0] = { ...structuredClone(value.servers[1]!), key: 'app3', hostname: 'app3.monappli.re' }
    value.servers[2] = { ...structuredClone(value.servers[1]!), key: 'app5', hostname: 'app5.monappli.re' }
    value.servers[1]!.counts!.databases = -1
    const clean = parsePlatformFleet(value, now)
    expect(clean.servers.map((row) => row.access)).toEqual(['granted', 'unavailable', 'granted'])
    expect(clean.servers[0]?.inventory).toHaveLength(1)
    expect(clean.servers[2]?.metrics?.stale).toBe(false)
  })
})
