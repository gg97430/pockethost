import type { IncomingMessage } from 'node:http'
import type { Socket } from 'node:net'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createTerminalGateway } from './terminalGateway'

const mocked = vi.hoisted(() => ({ upgrade: vi.fn(), factory: vi.fn() }))
vi.mock('http-proxy-middleware', () => ({
  createProxyMiddleware: (options: unknown) => {
    mocked.factory(options)
    return { upgrade: mocked.upgrade }
  },
}))

const path = '/api/web-terminal/connect'
const gateway = (enabled = true) => createTerminalGateway({ enabled, dashboardHost: 'app.monappli.re' })
const request = (
  options: {
    host?: string
    origin?: string
    encrypted?: boolean
    url?: string
    method?: string
    ip?: string
  } = {}
) =>
  ({
    method: options.method || 'GET',
    url: options.url || path,
    headers: {
      host: options.host || 'app.monappli.re',
      origin: options.origin || 'https://app.monappli.re',
    },
    socket: { encrypted: options.encrypted !== false, remoteAddress: options.ip || '192.0.2.1' },
  }) as unknown as IncomingMessage
const socket = () => ({ end: vi.fn() }) as unknown as Socket
const upgrade = (router: ReturnType<typeof gateway>, req = request()) => {
  const connection = socket()
  const handled = router.upgrade(req, connection, Buffer.alloc(0))
  return { handled, reply: vi.mocked(connection.end) }
}
afterEach(() => {
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('terminal gateway boundaries', () => {
  it('routes only the exact dashboard hostname with no port or TLS port 443', () => {
    const router = gateway()
    for (const host of ['app.monappli.re', 'APP.MONAPPLI.RE:443']) {
      const result = upgrade(router, request({ host }))
      expect(result.handled).toBe(true)
      expect(result.reply).not.toHaveBeenCalled()
    }
    expect(mocked.upgrade).toHaveBeenCalledTimes(2)
    for (const host of [
      'app.monappli.re:444',
      'app.monappli.re:443:evil',
      'app.monappli.re.evil',
      'app.monappli.re.',
      'app.monappli.re:0443',
      'evil@app.monappli.re',
    ])
      expect(upgrade(router, request({ host })).handled).toBe(false)
  })
  it('requires TLS and the exact HTTPS dashboard Origin before proxying', () => {
    const router = gateway()
    for (const options of [
      { encrypted: false },
      { origin: 'https://evil.example' },
      { origin: 'http://app.monappli.re' },
      { origin: 'https://app.monappli.re:443' },
    ]) {
      const result = upgrade(router, request(options))
      expect(result.handled).toBe(true)
      expect(result.reply).toHaveBeenCalledWith(expect.stringContaining('403 Forbidden'))
    }
    expect(mocked.upgrade).not.toHaveBeenCalled()
  })
  it('rejects query tokens and non-GET upgrade methods', () => {
    const router = gateway()
    for (const options of [{ url: `${path}?token=private` }, { url: `${path}?` }, { method: 'POST' }]) {
      expect(upgrade(router, request(options)).reply).toHaveBeenCalledWith(expect.stringContaining('400 Bad Request'))
    }
    expect(mocked.upgrade).not.toHaveBeenCalled()
  })
  it('strips dashboard credentials and private worker headers before the fixed loopback proxy', () => {
    gateway()
    const options = mocked.factory.mock.calls[0]![0]
    expect(options.target).toBe('http://127.0.0.1:3192')
    expect(options.ws).toBe(false)
    const outgoing = { removeHeader: vi.fn() }
    options.on.proxyReqWs(outgoing)
    expect(outgoing.removeHeader.mock.calls.map(([name]) => name)).toEqual([
      'authorization',
      'cookie',
      'x-web-worker',
      'x-pockethost-secret',
    ])
    expect(outgoing.removeHeader).not.toHaveBeenCalledWith('sec-websocket-protocol')
    expect(outgoing.removeHeader).not.toHaveBeenCalledWith('origin')
  })
  it('leaves disabled features, ordinary PocketBase hosts and unrelated paths to existing routing', () => {
    expect(upgrade(gateway(false)).handled).toBe(false)
    const router = gateway()
    for (const options of [{ host: 'avenirassur.app.monappli.re' }, { url: '/api/realtime' }, { url: '/' }])
      expect(upgrade(router, request(options)).handled).toBe(false)
    const next = vi.fn(),
      res = { status: vi.fn(), set: vi.fn(), end: vi.fn() }
    router.middleware(request({ host: 'db.monappli.re' }) as any, res as any, next)
    expect(next).toHaveBeenCalledOnce()
    expect(res.status).not.toHaveBeenCalled()
    expect(mocked.upgrade).not.toHaveBeenCalled()
  })
  it('returns a no-store upgrade requirement for ordinary HTTP terminal requests', () => {
    const router = gateway(),
      next = vi.fn()
    const res: any = { status: vi.fn(() => res), set: vi.fn(() => res), end: vi.fn() }
    router.middleware(request() as any, res, next)
    expect(res.status).toHaveBeenCalledWith(426)
    expect(res.set).toHaveBeenCalledWith({ Upgrade: 'websocket', 'Cache-Control': 'no-store' })
    expect(next).not.toHaveBeenCalled()
  })
  it('caps each IP at 30 attempts per minute and resets the window', () => {
    vi.useFakeTimers()
    vi.setSystemTime(100000)
    const router = gateway()
    for (let i = 0; i < 30; i++) expect(upgrade(router).reply).not.toHaveBeenCalled()
    expect(upgrade(router).reply).toHaveBeenCalledWith(expect.stringContaining('429 Too Many Requests'))
    vi.advanceTimersByTime(60000)
    expect(upgrade(router).reply).not.toHaveBeenCalled()
  })
  it('does not let one blocked IP consume the shared quota', () => {
    vi.useFakeTimers()
    vi.setSystemTime(100000)
    const router = gateway()
    for (let i = 0; i < 500; i++) upgrade(router, request({ ip: '198.51.100.7' }))
    expect(upgrade(router, request({ ip: '198.51.100.8' })).reply).not.toHaveBeenCalled()
    expect(mocked.upgrade).toHaveBeenCalledTimes(31)
  })
  it('counts attempts on the IP computed by the firewall, not on the Cloudflare peer', () => {
    vi.useFakeTimers()
    vi.setSystemTime(100000)
    const clientIp = vi.fn((req: IncomingMessage) => `${req.headers['cf-connecting-ip']}`)
    const router = createTerminalGateway({ enabled: true, dashboardHost: 'app.monappli.re', clientIp })
    const fromClient = (ip: string) => {
      const req = request({ ip: '173.245.48.1' })
      req.headers['cf-connecting-ip'] = ip
      return req
    }
    for (let i = 0; i < 30; i++) expect(upgrade(router, fromClient('198.51.100.1')).reply).not.toHaveBeenCalled()
    expect(upgrade(router, fromClient('198.51.100.1')).reply).toHaveBeenCalledWith(
      expect.stringContaining('429 Too Many Requests')
    )
    expect(upgrade(router, fromClient('198.51.100.2')).reply).not.toHaveBeenCalled()
  })
  it('caps all IPs together at 120 attempts per minute and handles stale IP entries', () => {
    vi.useFakeTimers()
    vi.setSystemTime(100000)
    const router = gateway()
    for (let minute = 0; minute < 10; minute++) {
      for (let i = 0; i < 120; i++)
        expect(upgrade(router, request({ ip: `192.0.${minute}.${i}` })).reply).not.toHaveBeenCalled()
      expect(upgrade(router, request({ ip: `blocked-${minute}` })).reply).toHaveBeenCalledWith(
        expect.stringContaining('429 Too Many Requests')
      )
      vi.advanceTimersByTime(60000)
    }
    expect(mocked.upgrade).toHaveBeenCalledTimes(1200)
  })
})
