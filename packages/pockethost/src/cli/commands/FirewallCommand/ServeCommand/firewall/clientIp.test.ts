import { readFileSync } from 'node:fs'
import type { IncomingMessage } from 'node:http'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { FIREWALL_CLIENT_IP_HEADER } from '../../../../../webApps/proxyHeaders'
import { createClientIpResolver, peerIp, validClientIp } from './clientIp'

const request = (remoteAddress: string | undefined, headers: Record<string, string> = {}, encrypted = false) =>
  ({ headers, socket: { remoteAddress, encrypted } }) as unknown as IncomingMessage

const CLOUDFLARE_PEER = '173.245.48.1'
const FORGED = {
  'cf-connecting-ip': '198.51.100.1',
  'true-client-ip': '198.51.100.2',
  'x-forwarded-for': '198.51.100.3',
  'x-real-ip': '198.51.100.4',
}

describe('client IP resolver', () => {
  it('ignores every header from a peer that is not a trusted proxy', () => {
    const { clientIp, isTrustedProxy } = createClientIpResolver()
    const req = request('203.0.113.9', FORGED)
    expect(isTrustedProxy(req)).toBe(false)
    expect(clientIp(req)).toBe('203.0.113.9')
  })

  it('takes a valid CF-Connecting-IP from a published Cloudflare range, IPv4 and IPv6', () => {
    const { clientIp } = createClientIpResolver()
    expect(clientIp(request(CLOUDFLARE_PEER, { 'cf-connecting-ip': '198.51.100.1' }))).toBe('198.51.100.1')
    expect(clientIp(request('2606:4700::1', { 'cf-connecting-ip': '2001:db8::5' }))).toBe('2001:db8::5')
    expect(clientIp(request(`::ffff:${CLOUDFLARE_PEER}`, { 'cf-connecting-ip': '198.51.100.1' }))).toBe('198.51.100.1')
  })

  it('never reads True-Client-IP, X-Forwarded-For or X-Real-Ip, even from Cloudflare', () => {
    const { clientIp } = createClientIpResolver()
    const { 'cf-connecting-ip': _, ...others } = FORGED
    expect(clientIp(request(CLOUDFLARE_PEER, others))).toBe(CLOUDFLARE_PEER)
  })

  it('falls back to the peer on an invalid CF-Connecting-IP', () => {
    const { clientIp } = createClientIpResolver()
    for (const value of [
      '',
      'unknown',
      '1.1.1.1, 2.2.2.2',
      '999.1.1.1',
      'fe80::1%eth0',
      '127.0.0.1',
      '127.9.9.9',
      '::1',
    ])
      expect(clientIp(request(CLOUDFLARE_PEER, { 'cf-connecting-ip': value }))).toBe(CLOUDFLARE_PEER)
  })

  it('treats PH_USER_PROXY_IPS as trusted peers, bare IP or CIDR, and reports unusable entries', () => {
    const onInvalid = vi.fn()
    const { clientIp, isUserProxyIp } = createClientIpResolver(
      ['192.0.2.10', ' 10.0.0.0/8 ', '2001:db8::/32', 'nope', '10.0.0.0/99', ''],
      onInvalid
    )
    expect(clientIp(request('192.0.2.10', { 'cf-connecting-ip': '198.51.100.1' }))).toBe('198.51.100.1')
    expect(clientIp(request('10.1.2.3', { 'cf-connecting-ip': '198.51.100.1' }))).toBe('198.51.100.1')
    expect(clientIp(request('2001:db8::9', { 'cf-connecting-ip': '198.51.100.1' }))).toBe('198.51.100.1')
    expect(clientIp(request('192.0.2.11', { 'cf-connecting-ip': '198.51.100.1' }))).toBe('192.0.2.11')
    expect(isUserProxyIp('192.0.2.10')).toBe(true)
    expect(isUserProxyIp(CLOUDFLARE_PEER)).toBe(false)
    expect(onInvalid.mock.calls.map(([entry]) => entry)).toEqual(['nope', '10.0.0.0/99'])
  })

  it('normalizes IPv4-mapped addresses and survives a closed socket', () => {
    expect(peerIp(request('::ffff:203.0.113.9'))).toBe('203.0.113.9')
    expect(peerIp(request(undefined))).toBe('unknown')
    expect(validClientIp('::ffff:203.0.113.9')).toBe('203.0.113.9')
    expect(createClientIpResolver().isTrustedProxy(request(undefined))).toBe(false)
    expect(createClientIpResolver().isTrustedProxy(request('fe80::1%eth0'))).toBe(false)
  })

  it('honours X-Forwarded-Proto only from a trusted proxy', () => {
    const { forwardedProto } = createClientIpResolver()
    expect(forwardedProto(request(CLOUDFLARE_PEER, { 'x-forwarded-proto': 'https' }))).toBe('https')
    expect(forwardedProto(request('203.0.113.9', { 'x-forwarded-proto': 'https' }))).toBe('http')
    expect(forwardedProto(request('203.0.113.9', {}, true))).toBe('https')
  })

  it('keeps the migration on the header the firewall writes', () => {
    const migration = readFileSync(
      resolve(__dirname, '../../../../../mothership-app/pb_migrations/1793271500_enable_web_rate_limits.js'),
      'utf8'
    )
    expect(/const HEADER = '([^']+)'/.exec(migration)?.[1]?.toLowerCase()).toBe(FIREWALL_CLIENT_IP_HEADER)
  })
})
