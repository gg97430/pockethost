import express from 'express'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { createServer as createHttpsServer } from 'node:https'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { connect as tlsConnect } from 'node:tls'
import request from 'supertest'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { PLATFORM_CONTROL_HOSTS } from '../common/platformDashboardHosts'
import { defaultWebProjectLimits } from '../common/webProjectLimits'
import { createWebAppRouting, isLoopbackWebTarget, normalizedWebHost } from './routing'

let root: string
beforeEach(() => {
  root = mkdtempSync(join(realpathSync(tmpdir()), 'web-routing-'))
})
afterEach(() => rmSync(root, { recursive: true, force: true }))
const appId = 'a'.repeat(15)
const manifest = (routes: any[]) => writeFileSync(join(root, 'routes.json'), JSON.stringify({ version: 1, routes }))
const router = (enabled = true) =>
  createWebAppRouting({
    enabled,
    root,
    apexDomain: 'monappli.re',
    protectedHosts: ['app.monappli.re', 'monappli.re'],
    now: () => Date.now() + 1000,
  })
const secureApp = (routing: ReturnType<typeof router>) => {
  const app = express()
  app.use((req, _res, next) => {
    ;(req.socket as any).encrypted = true
    next()
  })
  app.use(routing.challenge)
  app.use(routing.middleware)
  app.use((_req, res) => {
    res.status(418).send('legacy-pocketbase')
  })
  return app
}
describe('web routes preserve PocketBase traffic', () => {
  it('never routes any imported or canonical controller origin to a tenant website', async () => {
    for (const host of PLATFORM_CONTROL_HOSTS) {
      manifest([{ host, appId, target: 'http://127.0.0.1:31000', active: true }])
      const routing = createWebAppRouting({
        enabled: true,
        root,
        apexDomain: 'app4.monappli.re',
        protectedHosts: PLATFORM_CONTROL_HOSTS,
      })
      expect(routing.lookup(host)).toBeUndefined()
      expect((await request(secureApp(routing)).get('/applications').set('Host', host)).status).toBe(418)
    }
  })

  it('enforces a live project policy across sites, cannot spoof its IP, and disables without redeploying', async () => {
    const target = createServer((_req, res) => res.end('site'))
    await new Promise<void>((resolve) => target.listen(0, '127.0.0.1', resolve))
    try {
      const a = 'a'.repeat(15),
        b = 'b'.repeat(15),
        c = 'c'.repeat(15),
        p = 'p'.repeat(15),
        q = 'q'.repeat(15)
      const policy = defaultWebProjectLimits()
      policy.traffic = { enabled: true, requestsPerMinute: 1, concurrentRequests: 10 }
      const registry = {
        version: 1,
        projects: { [p]: policy, [q]: defaultWebProjectLimits() },
        apps: { [a]: p, [b]: p, [c]: q },
      }
      const hosts = ['web-a.monappli.re', 'web-b.monappli.re', 'web-c.monappli.re']
      manifest(
        [a, b, c].map((id, index) => ({
          host: hosts[index],
          appId: id,
          active: true,
          target: `http://127.0.0.1:${(target.address() as any).port}`,
        }))
      )
      writeFileSync(join(root, 'project-limits.json'), JSON.stringify(registry))
      let clock = 1000
      const routing = createWebAppRouting({
        enabled: true,
        root,
        apexDomain: 'monappli.re',
        protectedHosts: [],
        now: () => clock,
      })
      const app = secureApp(routing)
      expect((await request(app).get('/').set('Host', hosts[0]!).set('X-Forwarded-For', '1.2.3.4')).status).toBe(200)
      const refused = await request(app)
        .get('/')
        .set('Host', hosts[1]!)
        .set('X-Forwarded-For', '5.6.7.8')
        .set('CF-Connecting-IP', '5.6.7.8')
      expect(refused.status).toBe(429)
      expect(refused.headers['retry-after']).toBe('60')
      expect((await request(app).get('/').set('Host', hosts[2]!)).status).toBe(200)
      registry.projects[p]!.traffic.enabled = false
      writeFileSync(join(root, 'project-limits.json'), JSON.stringify(registry))
      clock += 1000
      expect((await request(app).get('/').set('Host', hosts[1]!)).status).toBe(200)
    } finally {
      await new Promise<void>((resolve) => target.close(() => resolve()))
    }
  })
  it('normalizes hosts and rejects non-loopback/internal proxy targets', () => {
    expect(normalizedWebHost('EXAMPLE.COM:443')).toBe('example.com')
    for (const host of ['example.com/path', 'user@example.com', '127.0.0.1', 'a.com\r\nX-Evil:yes'])
      expect(normalizedWebHost(host)).toBeUndefined()
    expect(isLoopbackWebTarget('http://127.0.0.1:31000')).toBe(true)
    for (const value of [
      'http://localhost:31000',
      'http://169.254.169.254:8080',
      'http://127.0.0.1:8091/path',
      'https://127.0.0.1:31000',
      'http://u:p@127.0.0.1:31000',
    ])
      expect(isLoopbackWebTarget(value)).toBe(false)
  })
  it('disabled feature and missing registry never intercept existing database hosts', async () => {
    manifest([{ host: 'web-site-a.monappli.re', appId, target: 'http://127.0.0.1:31000', active: true }])
    for (const host of ['web-site-a.monappli.re', 'app.monappli.re', 'db.monappli.re'])
      expect(
        (
          await request(secureApp(router(false)))
            .get('/')
            .set('Host', host)
        ).status
      ).toBe(418)
    rmSync(join(root, 'routes.json'))
    expect(
      (await request(secureApp(router())).get('/').set('Host', 'web-existing-pocketbase.monappli.re')).status
    ).toBe(418)
  })
  it('rejects registry routes claiming protected or ordinary PocketBase namespaces', async () => {
    for (const host of ['app.monappli.re', 'db.monappli.re', 'monappli.re']) {
      manifest([{ host, appId, target: 'http://127.0.0.1:31000', active: true }])
      expect((await request(secureApp(router())).get('/').set('Host', host)).status).toBe(418)
    }
  })
  it('allows only explicitly listed safe apex aliases through pending ACME and active proxy without invalidating generated sites', async () => {
    let clock = 1000
    const target = createServer((_req, res) => res.end('alias-response'))
    await new Promise<void>((resolve) => target.listen(0, '127.0.0.1', resolve))
    try {
      const port = (target.address() as any).port
      const generatedRoute = {
        host: 'web-existing.monappli.re',
        appId,
        active: true,
        target: `http://127.0.0.1:${port}`,
      }
      manifest([generatedRoute, { host: 'testgg.monappli.re', appId, active: false }])
      const routing = createWebAppRouting({
        enabled: true,
        root,
        apexDomain: 'monappli.re',
        protectedHosts: ['dashboard.monappli.re'],
        platformAliases: ['testgg.monappli.re'],
        now: () => clock,
      })
      expect(routing.lookup(generatedRoute.host)?.active).toBe(true)
      expect(routing.lookup('testgg.monappli.re')?.active).toBe(false)
      const dir = join(root, 'acme/.well-known/acme-challenge')
      mkdirSync(dir, { recursive: true })
      writeFileSync(join(dir, 'alias-token'), 'alias-proof')
      const plain = express()
      plain.use(routing.challenge)
      plain.use(routing.middleware)
      plain.use((_req, res) => res.status(418).send('legacy-pocketbase'))
      const challenge = await request(plain)
        .get('/.well-known/acme-challenge/alias-token')
        .set('Host', 'testgg.monappli.re')
      expect(challenge.status).toBe(200)
      expect(challenge.text).toBe('alias-proof')
      expect((await request(secureApp(routing)).get('/').set('Host', 'web-existing.monappli.re')).text).toBe(
        'alias-response'
      )
      manifest([
        generatedRoute,
        { host: 'testgg.monappli.re', appId, active: true, target: `http://127.0.0.1:${port}`, certificate: true },
      ])
      clock += 1000
      expect(routing.lookup('testgg.monappli.re')?.certificate).toBe(true)
      expect((await request(secureApp(routing)).get('/').set('Host', 'testgg.monappli.re')).text).toBe('alias-response')
      expect((await request(secureApp(routing)).get('/').set('Host', 'db.monappli.re')).status).toBe(418)
    } finally {
      await new Promise<void>((resolve, reject) => target.close((error) => (error ? reject(error) : resolve())))
    }
  })
  it('never accepts protected or reserved apex aliases even when explicitly listed', async () => {
    const forbidden = [
      'app',
      'app1',
      'app2',
      'app3',
      'app42',
      'www',
      'api',
      'admin',
      'ftp',
      'sftp',
      'smtp',
      'mail',
      'mothership',
      'edge',
      'daemon',
      'acme',
      'static',
      'assets',
      'files',
      'dashboard',
      'nested.name',
    ]
    for (const label of forbidden) {
      const host = label + '.monappli.re'
      manifest([{ host, appId, active: false }])
      const routing = createWebAppRouting({
        enabled: true,
        root,
        apexDomain: 'monappli.re',
        protectedHosts: ['dashboard.monappli.re'],
        platformAliases: [host],
      })
      expect(routing.lookup(host)).toBeUndefined()
      expect((await request(secureApp(routing)).get('/').set('Host', host)).status).toBe(418)
    }
    manifest([{ host: 'unlisted.monappli.re', appId, active: false }])
    expect(
      createWebAppRouting({
        enabled: true,
        root,
        apexDomain: 'monappli.re',
        protectedHosts: [],
        platformAliases: ['testgg.monappli.re'],
      }).lookup('unlisted.monappli.re')
    ).toBeUndefined()
    const dir = join(root, 'acme/.well-known/acme-challenge')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, 'generated-token'), 'must-not-serve')
    manifest([{ host: 'web-safe.monappli.re', appId, active: false }])
    const routing = createWebAppRouting({
      enabled: true,
      root,
      apexDomain: 'monappli.re',
      protectedHosts: [],
      platformAliases: ['web-safe.monappli.re'],
    })
    const app = express()
    app.use(routing.challenge)
    app.use((_req, res) => res.status(418).end())
    expect(
      (await request(app).get('/.well-known/acme-challenge/generated-token').set('Host', 'web-safe.monappli.re')).status
    ).toBe(418)
  })
  it('fails closed for known web hosts after a corrupt registry without taking over PocketBase', async () => {
    let clock = 1000
    manifest([{ host: 'www.example.com', appId, target: 'http://127.0.0.1:31000', active: true }])
    const routing = createWebAppRouting({
      enabled: true,
      root,
      apexDomain: 'monappli.re',
      protectedHosts: ['app.monappli.re'],
      now: () => clock,
    })
    expect(routing.lookup('www.example.com')?.active).toBe(true)
    writeFileSync(join(root, 'routes.json'), '{broken-json')
    clock += 1000
    expect(routing.lookup('www.example.com')?.active).toBe(false)
    expect((await request(secureApp(routing)).get('/').set('Host', 'db.monappli.re')).status).toBe(418)
  })
  it('registered offline web host returns503 and HTTP redirects only to its validated host', async () => {
    manifest([{ host: 'www.example.com', appId, active: false }])
    const routing = router()
    expect((await request(secureApp(routing)).get('/').set('Host', 'www.example.com')).status).toBe(503)
    const plain = express()
    plain.use(routing.middleware)
    const r = await request(plain).get('/hello?x=1').set('Host', 'www.example.com')
    expect(r.status).toBe(308)
    expect(r.headers.location).toBe('https://www.example.com/hello?x=1')
  })
  it.each([undefined, '100-continue'])(
    'sanitizes forwarding headers before proxying (Expect: %s)',
    async (expectation) => {
      let seen: any
      const target = createServer((req, res) => {
        seen = req.headers
        req.resume()
        req.on('end', () => res.end('nextjs-response'))
      })
      await new Promise<void>((resolve) => target.listen(0, '127.0.0.1', resolve))
      try {
        const port = (target.address() as any).port
        manifest([{ host: 'web-site-a.monappli.re', appId, target: `http://127.0.0.1:${port}`, active: true }])
        const app = secureApp(router())
        const pending = request(app)
          .post('/api/hello')
          .send({ ping: true })
          .set('Host', 'web-site-a.monappli.re')
          .set('X-Forwarded-For', 'private-spoofed')
          .set('X-Forwarded-Host', 'other.example')
          .set('X-Pockethost-Secret', 'private-platform-secret')
          .set('Forwarded', 'for=spoofed;host=other.example')
          .set('X-Real-IP', 'spoofed')
        if (expectation) pending.set('Expect', expectation)
        const r = await pending
        expect(r.text).toBe('nextjs-response')
        expect(seen['x-forwarded-host']).toBe('web-site-a.monappli.re')
        expect(seen['x-forwarded-proto']).toBe('https')
        expect(seen['x-forwarded-for']).not.toBe('private-spoofed')
        expect(seen['x-pockethost-secret']).toBeUndefined()
        expect(seen.forwarded).toBeUndefined()
        expect(seen['x-real-ip']).toBeUndefined()
        expect((await request(app).get('/').set('Host', 'db.monappli.re')).text).toBe('legacy-pocketbase')
      } finally {
        await new Promise<void>((resolve, reject) => target.close((e) => (e ? reject(e) : resolve())))
      }
    }
  )
  const FORGED = {
    'CF-Connecting-IP': '198.51.100.1',
    'True-Client-IP': '198.51.100.2',
    'X-Real-IP': '198.51.100.3',
    Forwarded: 'for=198.51.100.4',
    Via: '1.1 spoofed',
    'X-Pockethost-Client-Ip': '198.51.100.5',
    'X-Pockethost-Firewall-Client-Ip': '198.51.100.6',
    'X-Forwarded-Port': '8443',
  }
  const expectCleanHeaders = (seen: any) => {
    expect(seen.host).toBe('web-site-a.monappli.re')
    expect(seen['x-forwarded-for']).toBe('203.0.113.77')
    expect(seen['x-forwarded-host']).toBe('web-site-a.monappli.re')
    expect(seen['x-forwarded-proto']).toBe('https')
    for (const name of Object.keys(FORGED)) expect(seen[name.toLowerCase()]).toBeUndefined()
  }
  it.each([undefined, '100-continue'])(
    'rewrites HTTP headers from the computed client IP, whatever the client sent (Expect: %s)',
    async (expectation) => {
      let seen: any
      const target = createServer((req, res) => {
        seen = req.headers
        req.resume()
        req.on('end', () => res.end('nextjs-response'))
      })
      await new Promise<void>((resolve) => target.listen(0, '127.0.0.1', resolve))
      try {
        manifest([
          {
            host: 'web-site-a.monappli.re',
            appId,
            target: `http://127.0.0.1:${(target.address() as any).port}`,
            active: true,
          },
        ])
        const routing = createWebAppRouting({
          enabled: true,
          root,
          apexDomain: 'monappli.re',
          protectedHosts: ['app.monappli.re'],
          now: () => Date.now() + 1000,
          clientIp: () => '203.0.113.77',
        })
        const pending = request(secureApp(routing))
          .post('/api/hello')
          .set('Host', 'WEB-SITE-A.monappli.re:443')
          .set('X-Forwarded-Host', 'other.example')
          .set('X-Forwarded-For', 'spoofed')
          .set('X-Forwarded-Proto', 'http')
          .set(FORGED)
        if (expectation) pending.set('Expect', expectation)
        const r = await pending.send({ hello: 'world' })
        expect(r.text).toBe('nextjs-response')
        expectCleanHeaders(seen)
      } finally {
        await new Promise<void>((resolve, reject) => target.close((e) => (e ? reject(e) : resolve())))
      }
    }
  )
  it('rewrites WebSocket headers from the computed client IP', async () => {
    const target = createServer()
    let seen: any
    target.on('upgrade', (req, socket) => {
      seen = req.headers
      socket.end('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n')
    })
    await new Promise<void>((resolve) => target.listen(0, '127.0.0.1', resolve))
    manifest([
      {
        host: 'web-site-a.monappli.re',
        appId,
        target: `http://127.0.0.1:${(target.address() as any).port}`,
        active: true,
      },
    ])
    const routing = createWebAppRouting({
      enabled: true,
      root,
      apexDomain: 'monappli.re',
      protectedHosts: [],
      now: () => Date.now() + 1000,
      clientIp: () => '203.0.113.77',
    })
    const server = createServer()
    server.on('upgrade', (req, socket, head) => {
      ;(req.socket as any).encrypted = true
      routing.upgrade(req, socket as any, head)
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const { connect } = await import('node:net')
    const client = connect((server.address() as any).port, '127.0.0.1')
    try {
      const response = new Promise<string>((resolve, reject) => {
        client.once('data', (data) => resolve(data.toString()))
        client.once('error', reject)
      })
      const forged = Object.entries(FORGED)
        .map(([name, value]) => `${name}: ${value}\r\n`)
        .join('')
      client.write(
        `GET /socket HTTP/1.1\r\nHost: web-site-a.monappli.re\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n${forged}\r\n`
      )
      expect(await response).toContain('101 Switching Protocols')
      expectCleanHeaders(seen)
    } finally {
      client.destroy()
      await Promise.all([
        new Promise<void>((resolve) => server.close(() => resolve())),
        new Promise<void>((resolve) => target.close(() => resolve())),
      ])
    }
  })
  it('sanitizes WebSocket upgrade headers before the upstream handshake', async () => {
    const target = createServer()
    let seen: any
    target.on('upgrade', (req, socket) => {
      seen = req.headers
      socket.end('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n')
    })
    await new Promise<void>((resolve) => target.listen(0, '127.0.0.1', resolve))
    const routing = router()
    manifest([
      {
        host: 'web-site-a.monappli.re',
        appId,
        target: `http://127.0.0.1:${(target.address() as any).port}`,
        active: true,
      },
    ])
    const server = createServer()
    server.on('upgrade', (req, socket, head) => {
      ;(req.socket as any).encrypted = true
      routing.upgrade(req, socket as any, head)
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const { connect } = await import('node:net')
    const client = connect((server.address() as any).port, '127.0.0.1')
    try {
      const response = new Promise<string>((resolve, reject) => {
        client.once('data', (data) => resolve(data.toString()))
        client.once('error', reject)
      })
      client.write(
        'GET /socket HTTP/1.1\r\nHost: web-site-a.monappli.re\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nX-Forwarded-For: spoofed\r\nX-Forwarded-Host: other.example\r\nX-Forwarded-Proto: http\r\nX-Pockethost-Secret: private-platform-secret\r\n\r\n'
      )
      expect(await response).toContain('101 Switching Protocols')
      expect(seen['x-forwarded-for']).toBe('127.0.0.1')
      expect(seen['x-forwarded-host']).toBe('web-site-a.monappli.re')
      expect(seen['x-forwarded-proto']).toBe('https')
      expect(seen['x-pockethost-secret']).toBeUndefined()
    } finally {
      client.destroy()
      await Promise.all([
        new Promise<void>((resolve) => server.close(() => resolve())),
        new Promise<void>((resolve) => target.close(() => resolve())),
      ])
    }
  })
  it('serves retained Next assets only for the matching active application and rejects traversal', async () => {
    manifest([{ host: 'www.example.com', appId, target: 'http://127.0.0.1:31000', active: true }])
    const dir = join(root, 'assets', appId, 'b'.repeat(15), 'chunks')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, 'old-hash.js'), 'old-next-asset')
    writeFileSync(join(root, 'private.txt'), 'do-not-serve')
    symlinkSync(join(root, 'private.txt'), join(dir, 'bad.js'))
    const app = secureApp(router())
    const r = await request(app).get('/_next/static/chunks/old-hash.js').set('Host', 'www.example.com')
    expect(r.text).toBe('old-next-asset')
    expect(r.headers['cache-control']).toContain('immutable')
    expect((await request(app).get('/_next/static/chunks/old-hash.js').set('Host', 'db.monappli.re')).status).toBe(418)
    expect((await request(app).get('/_next/static/chunks/bad.js').set('Host', 'www.example.com')).status).toBe(502)
  })
  it('serves ACME tokens only on registered custom domains, rejects symlinks and invalid tokens', async () => {
    manifest([{ host: 'www.example.com', appId, active: false }])
    const dir = join(root, 'acme/.well-known/acme-challenge')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, 'valid-token'), 'proof')
    const app = secureApp(router())
    expect(
      (await request(app).get('/.well-known/acme-challenge/valid-token').set('Host', 'www.example.com')).text
    ).toBe('proof')
    expect(
      (await request(app).get('/.well-known/acme-challenge/valid-token').set('Host', 'db.monappli.re')).status
    ).toBe(418)
    symlinkSync(join(dir, 'valid-token'), join(dir, 'linked-token'))
    expect(
      (await request(app).get('/.well-known/acme-challenge/linked-token').set('Host', 'www.example.com')).status
    ).toBe(404)
  })
  it('real TLS selects a custom certificate and preserves the existing wildcard for PocketBase', async () => {
    manifest([{ host: 'www.example.com', appId, active: false, certificate: true }])
    for (const [folder, cn] of [
      ['default', '*.monappli.re'],
      ['tls/www.example.com', 'www.example.com'],
    ]) {
      const dir = join(root, folder!)
      mkdirSync(dir, { recursive: true })
      execFileSync(
        'openssl',
        [
          'req',
          '-x509',
          '-newkey',
          'rsa:2048',
          '-nodes',
          '-subj',
          `/CN=${cn}`,
          '-keyout',
          join(dir, 'privkey.pem'),
          '-out',
          join(dir, 'fullchain.pem'),
          '-days',
          '1',
        ],
        { stdio: 'ignore' }
      )
    }
    const routing = router()
    const server = createHttpsServer(
      {
        key: readFileSync(join(root, 'default/privkey.pem')),
        cert: readFileSync(join(root, 'default/fullchain.pem')),
        SNICallback: (host, callback) => callback(null, routing.secureContext(host)),
      },
      (_req, res) => res.end('ok')
    )
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    try {
      const peer = (servername: string) =>
        new Promise<string>((resolve, reject) => {
          const socket = tlsConnect(
            { host: '127.0.0.1', port: (server.address() as any).port, servername, rejectUnauthorized: false },
            () => {
              const name = socket.getPeerCertificate().subject.CN
              socket.destroy()
              resolve(name as string)
            }
          )
          socket.on('error', reject)
        })
      expect(await peer('www.example.com')).toBe('www.example.com')
      expect(await peer('db.monappli.re')).toBe('*.monappli.re')
      expect(await peer('app.monappli.re')).toBe('*.monappli.re')
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  })
  it('only loads custom TLS pairs from fixed paths and leaves wildcard fallback unchanged', () => {
    manifest([{ host: 'www.example.com', appId, active: true, certificate: true, target: 'http://127.0.0.1:31000' }])
    const dir = join(root, 'tls/www.example.com')
    mkdirSync(dir, { recursive: true })
    execFileSync(
      'openssl',
      [
        'req',
        '-x509',
        '-newkey',
        'rsa:2048',
        '-nodes',
        '-subj',
        '/CN=www.example.com',
        '-keyout',
        join(dir, 'privkey.pem'),
        '-out',
        join(dir, 'fullchain.pem'),
        '-days',
        '1',
      ],
      { stdio: 'ignore' }
    )
    const routing = router(),
      first = routing.secureContext('www.example.com')
    expect(first).toBeDefined()
    expect(routing.secureContext('www.example.com')).toBe(first)
    expect(routing.secureContext('app.monappli.re')).toBeUndefined()
    expect(routing.secureContext('db.monappli.re')).toBeUndefined()
    writeFileSync(join(dir, 'fullchain.pem'), 'incomplete-atomic-certificate-update')
    expect(routing.secureContext('www.example.com')).toBe(first)
  })
})
