import express from 'express'
import type { Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { LoggerService } from 'src/common'
import request from 'supertest'
import { describe, expect, it } from 'vitest'
import type { ClientIpResolver } from './clientIp'
import { createRateLimiterMiddleware } from './rate-limiter'

/** Requêtes retenues en vol : le limiteur de concurrence (15 par IP et par hôte, 50 pour un client de confiance). */
const withHeldRequests = async (
  ips: ClientIpResolver | undefined,
  run: (send: (headers?: Record<string, string>) => Promise<number>) => Promise<void>
) => {
  const app = express()
  app.use(createRateLimiterMiddleware(LoggerService(), ips))
  const held: express.Response[] = []
  app.use((_req, res) => {
    held.push(res)
  })
  const server: Server = await new Promise((resolve) => {
    const listening = app.listen(0, '127.0.0.1', () => resolve(listening))
  })
  const { port } = server.address() as AddressInfo
  const pending: Promise<unknown>[] = []
  const send = async (headers: Record<string, string> = {}) => {
    const before = held.length
    const response = fetch(`http://127.0.0.1:${port}/api/test`, { headers }).then((res) => res.status)
    pending.push(response)
    const status = await Promise.race([
      response,
      new Promise<number>((resolve) => {
        const poll = setInterval(() => {
          if (held.length > before) {
            clearInterval(poll)
            resolve(0)
          }
        }, 1)
      }),
    ])
    return status
  }
  try {
    await run(send)
  } finally {
    for (const res of held) res.end()
    await Promise.all(pending)
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
}

describe('createRateLimiterMiddleware', () => {
  it('bypasses health probe paths', async () => {
    const app = express()
    app.use(createRateLimiterMiddleware(LoggerService()))
    app.get('/api/firewall/health', (_req, res) => {
      res.status(200).send('ok')
    })

    const res = await request(app).get('/api/firewall/health').set('Host', 'app.example.com')
    expect(res.status).toBe(200)
    expect(res.text).toBe('ok')
  })

  it('allows requests with a valid Host header', async () => {
    const app = express()
    app.use(createRateLimiterMiddleware(LoggerService()))
    app.get('/api/test', (_req, res) => {
      res.status(200).send('ok')
    })

    const res = await request(app).get('/api/test').set('Host', 'example.com')
    expect(res.status).toBe(200)
  })

  it('keeps one bucket per real peer whatever the forged headers say', async () => {
    await withHeldRequests(undefined, async (send) => {
      for (let i = 0; i < 15; i++)
        expect(
          await send({
            'CF-Connecting-IP': `198.51.100.${i}`,
            'True-Client-IP': `198.51.100.${i}`,
            'X-Forwarded-For': `198.51.100.${i}`,
            'X-Real-IP': `198.51.100.${i}`,
          })
        ).toBe(0)
      expect(await send({ 'CF-Connecting-IP': '198.51.100.200' })).toBe(429)
    })
  })

  it('does not treat forged image-proxy headers as a trusted client', async () => {
    await withHeldRequests(undefined, async (send) => {
      const forged = { Via: '1.1 image-resizing-proxy', 'CDN-Loop': 'cloudflare' }
      for (let i = 0; i < 15; i++) expect(await send(forged)).toBe(0)
      expect(await send(forged)).toBe(429)
    })
  })

  it('accepts X-Pockethost-Client-Ip only from a listed proxy, and only when it is an IP', async () => {
    const proxy: ClientIpResolver = {
      clientIp: () => '192.0.2.10',
      isTrustedProxy: () => true,
      isUserProxyIp: () => true,
      forwardedProto: () => 'https',
    }
    await withHeldRequests(proxy, async (send) => {
      for (let i = 0; i < 20; i++) expect(await send({ 'X-Pockethost-Client-Ip': `198.51.100.${i}` })).toBe(0)
    })
    await withHeldRequests({ ...proxy, isUserProxyIp: () => false, isTrustedProxy: () => false }, async (send) => {
      for (let i = 0; i < 15; i++) expect(await send({ 'X-Pockethost-Client-Ip': `198.51.100.${i}` })).toBe(0)
      expect(await send({ 'X-Pockethost-Client-Ip': '198.51.100.200' })).toBe(429)
    })
  })

  it('shares a hostname bucket across case and trailing-dot variants', async () => {
    const app = express()
    app.use(createRateLimiterMiddleware(LoggerService()))
    const hosts: string[] = []
    app.use((req, res) => {
      hosts.push(req.hostname)
      res.send('ok')
    })
    for (const host of ['Example.COM.', 'example.com'])
      expect((await request(app).get('/api/test').set('Host', host)).status).toBe(200)
    expect((await request(app).get('/api/test').set('Host', '...')).status).toBe(400)
    expect(hosts).toHaveLength(2)
  })
})
