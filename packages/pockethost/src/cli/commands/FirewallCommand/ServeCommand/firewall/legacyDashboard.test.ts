import express from 'express'
import { createHmac } from 'node:crypto'
import { createServer, type IncomingMessage } from 'node:http'
import type { AddressInfo } from 'node:net'
import request from 'supertest'
import { describe, expect, it } from 'vitest'
import {
  PLATFORM_CONTROL_HOSTS,
  legacyControllerHosts,
  legacyDashboardHosts,
} from '../../../../../common/platformDashboardHosts'
import { FIREWALL_CLIENT_IP_HEADER } from '../../../../../webApps/proxyHeaders'
import { createClientIpResolver } from './clientIp'
import { createVhostProxyMiddleware } from './createVhostProxyMiddleware'
import {
  createLegacyDashboardMiddleware,
  createSignedWebhookProxyMiddleware,
  dashboardRequestHost,
} from './legacyDashboard'
import { refuseWorkerRoutes } from './workerRoutes'

const dashboardHost = 'app4.monappli.re'
const legacyPath = '/api/web-apps/github/webhook'
const bPath = `${legacyPath}/50a164e0ef1c85e`
const target = async (handler: Parameters<typeof createServer>[0]) => {
  const server = createServer(handler)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  }
}
const pass = (_req: unknown, _res: unknown, next: () => void) => next()
const hosts = new Set([dashboardHost, ...legacyDashboardHosts(dashboardHost), ...legacyControllerHosts(dashboardHost)])

describe('APP4 imported dashboard origins', () => {
  it('redirects pages and GitHub callback query strings to the one canonical dashboard', async () => {
    const app = express().use(
      createLegacyDashboardMiddleware({
        dashboardHost,
        controllerTarget: 'http://127.0.0.1:1',
        ips: createClientIpResolver(),
      })
    )
    for (const host of ['monappli.re', 'app.monappli.re', 'app2.monappli.re', 'APP2.MONAPPLI.RE:443']) {
      const url = '/applications/settings/github/install?state=opaque-state&installation_id=123&setup_action=install'
      const response = await request(app).get(url).set('Host', host)
      expect(response.status).toBe(302)
      expect(response.headers.location).toBe(`https://${dashboardHost}${url}`)
      expect(response.headers['cache-control']).toBe('no-store')
      expect(response.headers['referrer-policy']).toBe('no-referrer')
    }
  })

  it('never redirects database hosts, neighbouring authorities or other installations', async () => {
    const app = express()
      .use(
        createLegacyDashboardMiddleware({
          dashboardHost,
          controllerTarget: 'http://127.0.0.1:1',
          ips: createClientIpResolver(),
        })
      )
      .use((_req, res) => res.status(418).end())
    for (const host of [
      'bdd.monappli.re',
      'bdd.app2.monappli.re',
      'app2.monappli.re.evil.test',
      'app2.monappli.re:444',
      'app2.monappli.re.',
    ])
      expect((await request(app).get('/api/collections/data/records').set('Host', host)).status).toBe(418)
    expect(legacyDashboardHosts('app3.monappli.re')).toEqual([])
    expect(dashboardRequestHost('app2.monappli.re:443:evil')).toBe('')
    expect(PLATFORM_CONTROL_HOSTS).toEqual(expect.arrayContaining([...hosts]))
  })

  it('keeps only exact old mothership origins on the guarded controller proxy without page redirects', async () => {
    const upstream = await target((req, res) => {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ url: req.url, headers: req.headers }))
    })
    let frontend: ReturnType<typeof createServer> | undefined
    try {
      const app = express().use(refuseWorkerRoutes)
      for (const host of legacyControllerHosts(dashboardHost))
        app.use(createVhostProxyMiddleware(host, upstream.url, false, { dbg() {} } as any).middleware)
      app.use((_req, res) => res.status(418).end())
      frontend = createServer(app)
      await new Promise<void>((resolve) => frontend!.listen(0, '127.0.0.1', resolve))
      for (const host of legacyControllerHosts(dashboardHost)) {
        const response = await request(frontend!)
          .get('/_/')
          .set('Host', host)
          .set('X-Forwarded-For', '198.51.100.1')
          .set(FIREWALL_CLIENT_IP_HEADER, '198.51.100.2')
        expect(response.status).toBe(200)
        expect(response.body.url).toBe('/_/')
        expect(response.body.headers['x-forwarded-host']).toBe(host)
        expect(response.body.headers[FIREWALL_CLIENT_IP_HEADER]).toBe('127.0.0.1')
        expect((await request(frontend!).post('/api/web-apps/%5fworker/claim').set('Host', host)).status).toBe(404)
      }
      expect((await request(frontend!).get('/_/').set('Host', 'pockethost-central.monappli.re.evil.test')).status).toBe(
        418
      )
    } finally {
      await upstream.close()
    }
  })

  it.each([undefined, '100-continue'])(
    'passes legacy API without redirect and overwrites forged headers (Expect %s)',
    async (expectation) => {
      const upstream = await target((req, res) => {
        res.setHeader('Content-Type', 'application/json')
        res.end(JSON.stringify({ url: req.url, headers: req.headers }))
      })
      try {
        const ips = createClientIpResolver()
        const app = express()
          .use(refuseWorkerRoutes)
          .use(createLegacyDashboardMiddleware({ dashboardHost, controllerTarget: upstream.url, ips }))
        const pending = request(app)
          .get('/api/health?probe=1')
          .set('Host', 'app2.monappli.re')
          .set('X-Forwarded-For', '198.51.100.1')
          .set('X-Forwarded-Host', dashboardHost)
          .set('X-Forwarded-Proto', 'https')
          .set('CF-Connecting-IP', '198.51.100.2')
          .set(FIREWALL_CLIENT_IP_HEADER, '198.51.100.3')
        if (expectation) pending.set('Expect', expectation)
        const response = await pending
        expect(response.status).toBe(200)
        expect(response.body.url).toBe('/api/health?probe=1')
        expect(response.body.headers['x-forwarded-for']).toBe('127.0.0.1')
        expect(response.body.headers[FIREWALL_CLIENT_IP_HEADER]).toBe('127.0.0.1')
        expect(response.body.headers['x-forwarded-host']).toBe('app2.monappli.re')
        expect(response.body.headers['x-forwarded-proto']).toBe('http')
        expect(response.body.headers['cf-connecting-ip']).toBeUndefined()
        expect((await request(app).post('/api/web-apps/%5fworker/claim').set('Host', 'app2.monappli.re')).status).toBe(
          404
        )
      } finally {
        await upstream.close()
      }
    }
  )

  it.each([undefined, '100-continue'])(
    'selects GitHub A/B by exact Host, keeps raw bytes/HMAC and ignores forwarded Host (Expect %s)',
    async (expectation) => {
      const seen: { path: string; raw: string; headers: IncomingMessage['headers'] }[] = []
      const secrets: Record<string, string> = { [legacyPath]: 'fixture-secret-a', [bPath]: 'fixture-secret-b' }
      const upstream = await target(async (req, res) => {
        let raw = ''
        for await (const chunk of req) raw += chunk
        const path = (req.url || '').split('?')[0]!
        const secret = secrets[path]
        const valid =
          secret &&
          req.headers['x-hub-signature-256'] === `sha256=${createHmac('sha256', secret).update(raw).digest('hex')}`
        seen.push({ path: req.url || '', raw, headers: req.headers })
        res.writeHead(valid ? 202 : 401)
        res.end()
      })
      try {
        const app = express()
          .use(refuseWorkerRoutes)
          .use(
            createSignedWebhookProxyMiddleware({
              dashboardHost,
              dashboardHosts: hosts,
              controllerTarget: upstream.url,
              ips: createClientIpResolver(),
              limit: pass,
            })
          )
          .use((_req, res) => res.status(418).end())
        const body = '{ "installation": {"id": 42}, "text": "é raw\\nbytes" }\n'
        const send = (host: string, secret: string, pathname = legacyPath) => {
          const pending = request(app)
            .post(`${pathname}?probe=1`)
            .set('Host', host)
            .set('X-Forwarded-Host', 'app.monappli.re')
            .set('CF-Connecting-IP', '198.51.100.1')
            .set(FIREWALL_CLIENT_IP_HEADER, '198.51.100.2')
            .set('Content-Type', 'application/json')
            .set('X-Hub-Signature-256', `sha256=${createHmac('sha256', secret).update(body).digest('hex')}`)
            .send(body)
          if (expectation) pending.set('Expect', expectation)
          return pending
        }
        expect((await send('app.monappli.re', secrets[legacyPath]!)).status).toBe(202)
        expect((await send(dashboardHost, secrets[legacyPath]!)).status).toBe(202)
        expect((await send('APP2.MONAPPLI.RE:443', secrets[bPath]!)).status).toBe(202)
        expect(seen[2]?.path).toBe(`${bPath}?probe=1`)
        expect((await send('monappli.re', secrets[legacyPath]!)).status).toBe(202)
        expect((await send('pockethost-central.monappli.re', secrets[legacyPath]!)).status).toBe(202)
        expect((await send('pockethost-central.app2.monappli.re', secrets[bPath]!)).status).toBe(202)
        expect(seen.every((value) => value.raw === body)).toBe(true)
        expect(seen[2]?.headers[FIREWALL_CLIENT_IP_HEADER]).toBe('127.0.0.1')
        expect((await send('app2.monappli.re', secrets[legacyPath]!)).status).toBe(401)
        expect((await send('app.monappli.re', secrets[bPath]!)).status).toBe(401)
        expect((await send(dashboardHost, secrets[bPath]!, bPath)).status).toBe(202)
        const count = seen.length
        for (const host of ['bdd.app2.monappli.re', 'app2.monappli.re.evil.test', 'app2.monappli.re:444'])
          expect((await send(host, secrets[bPath]!)).status).toBe(418)
        expect(seen.length).toBe(count)
      } finally {
        await upstream.close()
      }
    }
  )
})
