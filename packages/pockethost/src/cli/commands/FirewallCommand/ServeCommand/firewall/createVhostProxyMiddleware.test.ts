import express from 'express'
import { createServer } from 'node:http'
import { connect, type AddressInfo } from 'node:net'
import request from 'supertest'
import { expect, it, vi } from 'vitest'
import { FIREWALL_CLIENT_IP_HEADER } from '../../../../../webApps/proxyHeaders'
import { createClientIpResolver, type ClientIpResolver } from './clientIp'
import { createVhostProxyMiddleware } from './createVhostProxyMiddleware'

const behindCloudflare: ClientIpResolver = {
  ...createClientIpResolver(),
  clientIp: (req) => `${req.headers['cf-connecting-ip']}`,
  isTrustedProxy: () => true,
  forwardedProto: () => 'https',
}

it.each([undefined, '100-continue'])(
  'replaces client IP headers towards the controller (Expect: %s)',
  async (expectation) => {
    const upstream = createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify(req.headers))
    })
    await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve))
    try {
      const app = express()
      const { middleware } = createVhostProxyMiddleware(
        'app.example.com',
        `http://127.0.0.1:${(upstream.address() as any).port}`,
        false,
        { dbg: vi.fn() } as any
      )
      app.use(middleware)
      const pending = request(app)
        .get('/api/collections/users/auth-with-password')
        .set('Host', 'app.example.com')
        .set('X-Forwarded-For', '198.51.100.21')
        .set('X-Forwarded-Host', 'attacker.example.com')
        .set('X-Forwarded-Proto', 'https')
        .set('CF-Connecting-IP', '198.51.100.22')
      if (expectation) pending.set('Expect', expectation)
      const response = await pending
      expect(response.status).toBe(200)
      expect(response.body['x-forwarded-for']).toMatch(/^(::ffff:)?127\.0\.0\.1$/)
      expect(response.body['x-forwarded-host']).toBe('app.example.com')
      expect(response.body['x-forwarded-proto']).toBe('http')
      expect(response.body['cf-connecting-ip']).toBeUndefined()
    } finally {
      await new Promise<void>((resolve) => upstream.close(() => resolve()))
    }
  }
)

it.each([undefined, '100-continue'])(
  'overwrites the internal client IP header on every relay (Expect: %s)',
  async (expectation) => {
    const upstream = createServer((req, res) => {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify(req.headers))
    })
    await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve))
    try {
      const target = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`
      const logger = { dbg: vi.fn() } as any
      const direct = express().use(createVhostProxyMiddleware('app.example.com', target, false, logger).middleware)
      const relayed = express().use(
        createVhostProxyMiddleware('app.example.com', target, false, logger, behindCloudflare).middleware
      )
      const send = (app: express.Express) => {
        const pending = request(app)
          .post('/api/collections/users/auth-with-password')
          .set('Host', 'app.example.com')
          .set(FIREWALL_CLIENT_IP_HEADER, '198.51.100.99')
          .set('X-Pockethost-Client-Ip', '198.51.100.98')
          .set('CF-Connecting-IP', '203.0.113.5')
        if (expectation) pending.set('Expect', expectation)
        return pending.send({ identity: 'a', password: 'b' })
      }
      const peer = (await send(direct)).body
      expect(peer[FIREWALL_CLIENT_IP_HEADER]).toMatch(/^(::ffff:)?127\.0\.0\.1$/)
      expect(peer['x-forwarded-for']).toBe(peer[FIREWALL_CLIENT_IP_HEADER])
      const cloudflare = (await send(relayed)).body
      expect(cloudflare[FIREWALL_CLIENT_IP_HEADER]).toBe('203.0.113.5')
      expect(cloudflare['x-forwarded-for']).toBe('203.0.113.5')
      expect(cloudflare['x-forwarded-proto']).toBe('https')
      expect(cloudflare['x-pockethost-client-ip']).toBeUndefined()
    } finally {
      await new Promise<void>((resolve) => upstream.close(() => resolve()))
    }
  }
)

it('sets the same headers on a WebSocket upgrade', async () => {
  let seen: any
  const upstream = createServer()
  upstream.on('upgrade', (req, socket) => {
    seen = req.headers
    socket.end('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n')
  })
  await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve))
  const { upgrade } = createVhostProxyMiddleware(
    'app.example.com',
    `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`,
    true,
    { dbg: vi.fn() } as any,
    behindCloudflare
  )
  const server = createServer()
  server.on('upgrade', (req, socket, head) => upgrade!(req, socket as any, head))
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const client = connect((server.address() as AddressInfo).port, '127.0.0.1')
  try {
    const reply = new Promise<string>((resolve, reject) => {
      client.once('data', (data) => resolve(data.toString()))
      client.once('error', reject)
    })
    client.write(
      `GET /ws HTTP/1.1\r\nHost: app.example.com\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nCF-Connecting-IP: 203.0.113.5\r\n${FIREWALL_CLIENT_IP_HEADER}: 198.51.100.99\r\n\r\n`
    )
    expect(await reply).toContain('101 Switching Protocols')
    expect(seen[FIREWALL_CLIENT_IP_HEADER]).toBe('203.0.113.5')
    expect(seen['x-forwarded-for']).toBe('203.0.113.5')
  } finally {
    client.destroy()
    await Promise.all([
      new Promise<void>((resolve) => server.close(() => resolve())),
      new Promise<void>((resolve) => upstream.close(() => resolve())),
    ])
  }
})
