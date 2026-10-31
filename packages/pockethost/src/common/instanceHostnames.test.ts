import { describe, expect, it } from 'vitest'
import {
  instanceGeneratedHostname,
  instancePublicHostname,
  instanceRoutingHostnames,
  normalizeAssignedInstanceHostnames,
  normalizeInstanceHostname,
  normalizeInstanceRequestHost,
} from './instanceHostnames'

describe('database hostname migration', () => {
  const database = {
    id: 'instance1234567',
    subdomain: 'client',
    publicHostname: 'client.app2.monappli.re',
    legacyHostnames: ['client.app2.monappli.re', 'instance1234567.app2.monappli.re'],
    cname: 'database.client.test',
  }
  it('keeps legacy nodes identical when the new fields are absent', () => {
    expect(instancePublicHostname({ id: '123', subdomain: 'client' }, 'monappli.re')).toBe('client.monappli.re')
    expect(instancePublicHostname({ id: '123', subdomain: 'client', cname: 'custom.test' }, 'monappli.re')).toBe(
      'custom.test'
    )
  })
  it('retains prior runtime/canonical fallback behavior while migrated records share one public URL', () => {
    const old = { id: 'record123', subdomain: 'client', cname: 'custom.test' }
    expect(instancePublicHostname(old, 'monappli.re', 'generated')).toBe('client.monappli.re')
    expect(instancePublicHostname({ ...old, cname: '' }, 'monappli.re', 'canonical')).toBe('record123.monappli.re')
    expect(instancePublicHostname(database, 'app4.monappli.re', 'generated')).toBe(database.cname)
    expect(instancePublicHostname(database, 'app4.monappli.re', 'canonical')).toBe(database.cname)
  })
  it('normalizes request host spelling without turning suffixes into wildcard routes', () => {
    expect(normalizeInstanceRequestHost('CLIENT.App2.Monappli.Re.:443')).toBe('client.app2.monappli.re')
    for (const invalid of ['https://client.test', 'client.test/other', '*.client.test', 'user@client.test'])
      expect(normalizeInstanceRequestHost(invalid)).toBeUndefined()
  })
  it('keeps the old generated address and an independent custom-domain override', () => {
    expect(instanceGeneratedHostname(database, 'app4.monappli.re')).toBe('client.app2.monappli.re')
    expect(instancePublicHostname(database, 'app4.monappli.re')).toBe('database.client.test')
    expect(instancePublicHostname({ ...database, cname: '' }, 'app4.monappli.re')).toBe('client.app2.monappli.re')
    expect(instanceRoutingHostnames({ ...database, cname: '' }, 'app4.monappli.re')).not.toContain(
      'database.client.test'
    )
  })
  it('routes exact assigned names plus both new canonical addresses without blanket multi-apex aliases', () => {
    expect(instanceRoutingHostnames(database, 'app4.monappli.re')).toEqual([
      'database.client.test',
      'client.app2.monappli.re',
      'instance1234567.app2.monappli.re',
      'client.app4.monappli.re',
      'instance1234567.app4.monappli.re',
    ])
    expect(instanceRoutingHostnames(database, 'app4.monappli.re')).not.toContain('other.app2.monappli.re')
    expect(instanceRoutingHostnames(database, 'app4.monappli.re')).not.toContain('client.monappli.re')
  })
  it('normalizes and bounds assigned FQDNs, without accepting URL/path/port/IP/wildcard data', () => {
    expect(
      normalizeAssignedInstanceHostnames('CLIENT.App2.Monappli.Re.', [
        ' CLIENT.app2.monappli.re ',
        'client.app2.monappli.re',
      ])
    ).toEqual({ publicHostname: 'client.app2.monappli.re', legacyHostnames: ['client.app2.monappli.re'] })
    for (const invalid of [
      'https://host.test',
      '*.host.test',
      'host.test:443',
      'host.test/path',
      '127.0.0.1',
      'host',
      'user@host.test',
    ])
      expect(normalizeInstanceHostname(invalid)).toBeUndefined()
    expect(() => normalizeAssignedInstanceHostnames('', Array(17).fill('host.test'))).toThrow()
    expect(() => normalizeAssignedInstanceHostnames('', ['bad/path'])).toThrow()
  })
})
