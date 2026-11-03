import express from 'express'
import { EventEmitter } from 'node:events'
import type { IncomingMessage } from 'node:http'
import type { Socket } from 'node:net'
import request from 'supertest'
import { describe, expect, it, vi } from 'vitest'
import {
  createIpRateLimit,
  createIpWindowLimiter,
  createUpgradeLimit,
  createWebLimiter,
  isNextStaticRequest,
  MAX_UPGRADES_PER_IP,
  rateLimitFromEnv,
  rateLimitKey,
} from './webLimit'

const byHeader = (req: IncomingMessage) => `${req.headers['x-test-ip'] || '203.0.113.1'}`
const site = (limit: number, now?: () => number) => {
  const app = express()
  app.use(createWebLimiter({ limit, clientIp: byHeader, now }).middleware)
  app.use((_req, res) => {
    res.send('ok')
  })
  return app
}
const socket = () => {
  const emitter = new EventEmitter() as unknown as Socket & { destroyed: boolean }
  emitter.destroyed = false
  emitter.end = vi.fn() as any
  return emitter
}
const upgradeRequest = (ip: string) => ({ headers: { 'x-test-ip': ip } }) as unknown as IncomingMessage

describe('web site rate limit', () => {
  it('counts per client IP, never per host, and answers 429 with Retry-After', async () => {
    const app = site(2)
    for (const host of ['a.example.com', 'b.example.com'])
      expect((await request(app).get('/').set('Host', host)).status).toBe(200)
    const blocked = await request(app).get('/').set('Host', 'c.example.com')
    expect(blocked.status).toBe(429)
    expect(Number(blocked.headers['retry-after'])).toBeGreaterThan(0)
    expect(blocked.headers['cache-control']).toBe('no-store')
    expect((await request(app).get('/').set('Host', 'c.example.com').set('X-Test-Ip', '203.0.113.2')).status).toBe(200)
  })

  it('opens a new window after one minute', async () => {
    let clock = 1000
    const app = site(1, () => clock)
    expect((await request(app).get('/')).status).toBe(200)
    expect((await request(app).get('/')).status).toBe(429)
    clock += 60_000
    expect((await request(app).get('/')).status).toBe(200)
  })

  it('does not count Next static files, but counts anything that climbs out of them', async () => {
    const app = site(1)
    for (let i = 0; i < 3; i++) expect((await request(app).get('/_next/static/chunks/a.js')).status).toBe(200)
    expect((await request(app).get('/')).status).toBe(200)
    expect((await request(app).get('/')).status).toBe(429)
    expect(isNextStaticRequest('HEAD', '/_next/static/a.js')).toBe(true)
    expect(isNextStaticRequest('POST', '/_next/static/a.js')).toBe(false)
    expect(isNextStaticRequest('GET', '/_next/static/../../login')).toBe(false)
    expect(isNextStaticRequest('GET', '/_next/static/%2e%2e/%2e%2e/login')).toBe(false)
    expect(isNextStaticRequest('GET', '/_next/static/%zz')).toBe(false)
    expect(isNextStaticRequest('GET', '/_next/staticfile')).toBe(false)
  })

  it('stays out of the way when disabled', async () => {
    const app = site(0)
    for (let i = 0; i < 5; i++) expect((await request(app).get('/')).status).toBe(200)
    const peer = socket()
    expect(createWebLimiter({ limit: 0, clientIp: byHeader }).upgrade(upgradeRequest('203.0.113.1'), peer)).toBe(true)
    expect(peer.end).not.toHaveBeenCalled()
  })

  it('applies admin policy changes without a server restart and labels its own 429 responses', async () => {
    let limit = 1
    const app = express(),
      limiter = createWebLimiter({ limit, clientIp: byHeader, readLimit: () => limit })
    app.use(limiter.middleware)
    app.use((_req, res) => res.send('ok'))
    expect((await request(app).get('/')).status).toBe(200)
    const refused = await request(app).get('/')
    expect(refused.status).toBe(429)
    expect(refused.headers['x-kolab-rate-limit-source']).toBe('hosting-ip')
    limit = 0
    for (let i = 0; i < 3; i++) expect((await request(app).get('/')).status).toBe(200)
    limit = 2
    expect((await request(app).get('/')).status).toBe(200)
    expect((await request(app).get('/')).status).toBe(200)
    expect((await request(app).get('/')).status).toBe(429)
  })

  it('reads its ceiling from the environment, 0 disabling it and garbage keeping the default', () => {
    expect(rateLimitFromEnv(undefined, 1200)).toBe(1200)
    expect(rateLimitFromEnv('', 1200)).toBe(1200)
    expect(rateLimitFromEnv('0', 1200)).toBe(0)
    expect(rateLimitFromEnv('600', 1200)).toBe(600)
    for (const bad of ['abc', '-1', '1.5']) expect(rateLimitFromEnv(bad, 1200)).toBe(1200)
  })

  it('applies a dedicated ceiling to webhook deliveries', async () => {
    const app = express()
    app.use(createIpRateLimit({ limit: 1, clientIp: byHeader }))
    app.use((_req, res) => {
      res.send('ok')
    })
    expect((await request(app).post('/hook')).status).toBe(200)
    const blocked = await request(app).post('/hook')
    expect(blocked.status).toBe(429)
    expect(blocked.headers['retry-after']).toBeDefined()
  })
})

describe('bounded IP window map', () => {
  it('purges expired windows and forgets the oldest key rather than refusing new clients', () => {
    let clock = 1000
    const limiter = createIpWindowLimiter({ limit: 1, maxKeys: 3, now: () => clock })
    for (const ip of ['192.0.2.1', '192.0.2.2', '192.0.2.3']) limiter.hit(ip)
    expect(limiter.size()).toBe(3)
    expect(limiter.hit('192.0.2.4').allowed).toBe(true)
    expect(limiter.size()).toBe(3)
    expect(limiter.hit('192.0.2.1').allowed).toBe(true)
    clock += 60_000
    limiter.hit('192.0.2.9')
    expect(limiter.size()).toBe(1)
  })

  it('keeps insertion order equal to window order, so one purge pass is enough', () => {
    let clock = 1000
    const limiter = createIpWindowLimiter({ limit: 5, now: () => clock })
    limiter.hit('192.0.2.1')
    clock += 30_000
    limiter.hit('192.0.2.2')
    clock += 30_000
    limiter.hit('192.0.2.1')
    clock += 30_001
    limiter.hit('192.0.2.3')
    expect(limiter.size()).toBe(2)
  })

  it('groups an IPv6 subscriber on its /64 so address rotation does not reset the count', () => {
    expect(rateLimitKey('2001:db8:1:2:3:4:5:6')).toBe('2001:db8:1:2::/64')
    expect(rateLimitKey('2001:db8:1:2::9')).toBe('2001:db8:1:2::/64')
    expect(rateLimitKey('2001:0DB8::1')).toBe('2001:db8:0:0::/64')
    expect(rateLimitKey('::1')).toBe('0:0:0:0::/64')
    expect(rateLimitKey('203.0.113.1')).toBe('203.0.113.1')
    expect(rateLimitKey('unknown')).toBe('unknown')
    const limiter = createIpWindowLimiter({ limit: 1 })
    expect(limiter.hit('2001:db8:1:2::1').allowed).toBe(true)
    expect(limiter.hit('2001:db8:1:2::2').allowed).toBe(false)
  })
})

describe('WebSocket upgrades', () => {
  it('caps simultaneous upgrades per IP and releases them when the socket closes', () => {
    const limit = createUpgradeLimit({ clientIp: byHeader })
    const open = Array.from({ length: MAX_UPGRADES_PER_IP }, socket)
    for (const peer of open) expect(limit.admit(upgradeRequest('203.0.113.1'), peer)).toBe(true)
    expect(limit.admit(upgradeRequest('203.0.113.1'), socket())).toBe(false)
    expect(limit.admit(upgradeRequest('203.0.113.2'), socket())).toBe(true)
    ;(open[0] as unknown as EventEmitter).emit('close')
    ;(open[0] as unknown as EventEmitter).emit('close')
    expect(limit.open()).toBe(MAX_UPGRADES_PER_IP)
    expect(limit.admit(upgradeRequest('203.0.113.1'), socket())).toBe(true)
    expect(limit.admit(upgradeRequest('203.0.113.1'), socket())).toBe(false)
  })

  it('caps the total and forgets an IP whose sockets are all closed', () => {
    const limit = createUpgradeLimit({ clientIp: byHeader, total: 2 })
    const [first, second] = [socket(), socket()]
    expect(limit.admit(upgradeRequest('203.0.113.1'), first)).toBe(true)
    expect(limit.admit(upgradeRequest('203.0.113.2'), second)).toBe(true)
    expect(limit.admit(upgradeRequest('203.0.113.3'), socket())).toBe(false)
    ;(first as unknown as EventEmitter).emit('close')
    ;(second as unknown as EventEmitter).emit('close')
    expect(limit.open()).toBe(0)
    expect(limit.keys()).toBe(0)
  })

  it('does not leak a slot for a socket that is already closed', () => {
    const limit = createUpgradeLimit({ clientIp: byHeader })
    const peer = socket()
    peer.destroyed = true
    limit.admit(upgradeRequest('203.0.113.1'), peer)
    expect(limit.open()).toBe(0)
  })

  it('refuses an upgrade with a 429 and shares the request window with HTTP traffic', async () => {
    const limiter = createWebLimiter({ limit: 2, clientIp: byHeader })
    const app = express()
    app.use(limiter.middleware)
    app.use((_req, res) => {
      res.send('ok')
    })
    expect((await request(app).get('/')).status).toBe(200)
    const first = socket()
    expect(limiter.upgrade(upgradeRequest('203.0.113.1'), first)).toBe(true)
    ;(first as unknown as EventEmitter).emit('close')
    const refused = socket()
    expect(limiter.upgrade(upgradeRequest('203.0.113.1'), refused)).toBe(false)
    expect(vi.mocked(refused.end).mock.calls[0]![0]).toContain('429 Too Many Requests')
    expect((await request(app).get('/')).status).toBe(429)
  })
})
