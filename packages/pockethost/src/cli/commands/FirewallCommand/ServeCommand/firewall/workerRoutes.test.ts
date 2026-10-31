import express from 'express'
import { createServer, type IncomingHttpHeaders } from 'node:http'
import type { AddressInfo } from 'node:net'
import { LoggerService } from 'src/common'
import request from 'supertest'
import { describe, expect, it } from 'vitest'
import { createVhostProxyMiddleware } from './createVhostProxyMiddleware'
import { isWorkerOnlyPath, refuseWorkerRoutes } from './workerRoutes'

describe('isWorkerOnlyPath', () => {
  it('refuses every worker and local-service route, whatever the spelling', () => {
    for (const path of [
      '/api/web-apps/_worker/claim',
      '/api/web-apps/_notifications/claim?x=1',
      '/api/web-apps/_platform/recovery-events',
      '/api/web-apps/_terminal/validate',
      '/api/web-apps/%5Fnotifications/claim',
      '/api/web-apps/%255Fnotifications/claim',
      '//api//web-apps//_notifications/claim',
      '/api/x/../web-apps/_notifications/claim',
      '/API/Web-Apps/_worker/claim',
      '/api/web-apps/_',
      '\\api\\web-apps\\_worker\\claim',
    ])
      expect(isWorkerOnlyPath(path)).toBe(true)
  })

  it('keeps public routes, webhooks and malformed escapes elsewhere reachable', () => {
    for (const path of [
      '/api/web-apps',
      '/api/web-apps/abcdefghijklmno/notifications',
      '/api/web-apps/github/webhook',
      '/api/web-git-sources/webhook/abc',
      '/api/web-platform-notifications',
      '/api/health',
      '/api/web-apps-_worker/claim',
      '/%E0%A4%A',
      '',
      undefined,
    ])
      expect(isWorkerOnlyPath(path as string)).toBe(false)
  })
})

describe('mothership proxy path', () => {
  it('never relays worker routes and marks every relayed request with X-Forwarded-For', async () => {
    const seen: { url?: string; headers: IncomingHttpHeaders }[] = []
    const upstream = createServer((req, res) => {
      seen.push({ url: req.url, headers: req.headers })
      res.writeHead(200, { 'content-type': 'application/json' }).end('{}')
    })
    await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve))
    try {
      const port = (upstream.address() as AddressInfo).port,
        app = express()
      app.use(refuseWorkerRoutes)
      app.use(
        createVhostProxyMiddleware('mothership.example.com', `http://127.0.0.1:${port}`, false, LoggerService())
          .middleware
      )
      for (const path of [
        '/api/web-apps/_notifications/claim',
        '/api/web-apps/%5Fplatform/recovery-events',
        '//api/web-apps/_worker/claim',
      ]) {
        const res = await request(app)
          .post(path)
          .set('Host', 'mothership.example.com')
          .set('X-Web-Worker', 'x'.repeat(40))
        expect(res.status).toBe(404)
      }
      expect(seen).toEqual([])
      const ok = await request(app).get('/api/health').set('Host', 'mothership.example.com')
      expect(ok.status).toBe(200)
      expect(seen).toHaveLength(1)
      expect(seen[0]!.headers['x-forwarded-for']).toBeTruthy()
    } finally {
      await new Promise((resolve) => upstream.close(resolve))
    }
  })
})
