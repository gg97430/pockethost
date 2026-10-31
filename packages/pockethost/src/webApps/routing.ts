import type { NextFunction, Request, Response } from 'express'
import { createProxyMiddleware } from 'http-proxy-middleware'
import { lstatSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs'
import type { IncomingMessage } from 'node:http'
import type { Socket } from 'node:net'
import { join, resolve, sep } from 'node:path'
import { createSecureContext, type SecureContext } from 'node:tls'
import { peerIp } from '../cli/commands/FirewallCommand/ServeCommand/firewall/clientIp'
import { createProjectPolicyRegistry, createProjectTrafficLimiter } from './projectLimits'
import { setProxyHeaders } from './proxyHeaders'

export type WebRoute = {
  host: string
  appId: string
  target?: string
  active: boolean
  certificate?: boolean
}
export type WebRoutingOptions = {
  enabled: boolean
  root: string
  apexDomain: string
  protectedHosts: string[]
  platformAliases?: string[]
  onError?: (message: string) => void
  now?: () => number
  /** IP calculée par le pare-feu ; à défaut le pair TCP, jamais un en-tête. */
  clientIp?: (req: IncomingMessage) => string
}
const DOMAIN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/
export const normalizedWebHost = (value: string | undefined): string | undefined => {
  if (!value || /[\s/@\\?#]/.test(value)) return
  const host = value
    .replace(/:\d{1,5}$/, '')
    .replace(/\.$/, '')
    .toLowerCase()
  return DOMAIN.test(host) ? host : undefined
}
export const isLoopbackWebTarget = (value: unknown): value is string => {
  if (typeof value !== 'string') return false
  try {
    const url = new URL(value)
    return (
      url.protocol === 'http:' &&
      url.hostname === '127.0.0.1' &&
      Number(url.port) >= 1024 &&
      Number(url.port) <= 65535 &&
      url.pathname === '/' &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    )
  } catch {
    return false
  }
}

/** Independent web routes; malformed/missing registry never changes PocketBase routing. */
export function createWebAppRouting(options: WebRoutingOptions) {
  const root = resolve(options.root)
  const apex = options.apexDomain.toLowerCase()
  const reserved = new Set([
    'app',
    'app1',
    'app2',
    'app3',
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
  ])
  const protectedHosts = new Set([
    ...options.protectedHosts.map((host) => host.toLowerCase()),
    apex,
    ...[...reserved].map((label) => `${label}.${apex}`),
  ])
  const alias = (host: string) => {
    if (!host.endsWith(`.${apex}`) || protectedHosts.has(host)) return false
    const label = host.slice(0, -(apex.length + 1))
    return (
      !!label && !label.includes('.') && !label.startsWith('web-') && !reserved.has(label) && !/^app[0-9]*$/.test(label)
    )
  }
  const platformAliases = new Set(
    (options.platformAliases || [])
      .map((host) => host.trim().toLowerCase())
      .filter((host) => normalizedWebHost(host) === host && alias(host))
  )
  const now = options.now || Date.now
  const projectPolicy = createProjectPolicyRegistry(root, now)
  const projectTraffic = createProjectTrafficLimiter(now)
  const admitTraffic = (appId: string, req: IncomingMessage) => {
    const policy = projectPolicy(appId)
    return policy
      ? projectTraffic(policy.projectId, options.clientIp?.(req) || peerIp(req), policy.limits.traffic)
      : undefined
  }
  const clientIp = options.clientIp || peerIp
  let routes = new Map<string, WebRoute>()
  let lastRead = -Infinity
  let invalid = false
  const contexts = new Map<string, { signature: string; context: SecureContext }>()
  const proxy = createProxyMiddleware<Request, Response>({
    target: 'http://127.0.0.1:1',
    changeOrigin: false,
    xfwd: false,
    ws: false,
    router: (req) => lookup(req.headers.host)?.target || 'http://127.0.0.1:1',
    on: {
      error: (_error, _req, res) => {
        if ('writeHead' in res && !res.headersSent) {
          res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' })
          res.end('Application temporairement indisponible.')
        } else res.destroy()
      },
    },
  })
  const generated = (host: string) =>
    host.startsWith('web-') && host.endsWith(`.${apex}`) && host.split('.').length === apex.split('.').length + 1
  const refresh = () => {
    if (!options.enabled || now() - lastRead < 500) return
    lastRead = now()
    try {
      const file = join(root, 'routes.json')
      if (lstatSync(file).isSymbolicLink() || realpathSync(file) !== file || statSync(file).size > 2_000_000)
        throw Error()
      const value = JSON.parse(readFileSync(file, 'utf8'))
      if (value.version !== 1 || !Array.isArray(value.routes) || value.routes.length > 1000) throw Error()
      const next = new Map<string, WebRoute>()
      for (const row of value.routes) {
        const host = normalizedWebHost(row.host)
        if (
          !host ||
          host !== row.host ||
          protectedHosts.has(host) ||
          !/^[a-z0-9]{15}$/.test(row.appId) ||
          typeof row.active !== 'boolean' ||
          (row.target && !isLoopbackWebTarget(row.target)) ||
          (row.active && !row.target) ||
          next.has(host)
        )
          throw Error()
        if (host.endsWith(`.${apex}`) && !generated(host) && !platformAliases.has(host)) throw Error()
        if (row.certificate !== undefined && typeof row.certificate !== 'boolean') throw Error()
        next.set(host, { host, appId: row.appId, active: row.active, target: row.target, certificate: row.certificate })
      }
      routes = next
      invalid = false
    } catch {
      if (!invalid) options.onError?.('Registre des applications web indisponible ou invalide.')
      invalid = true
    }
  }
  function lookup(header: string | undefined) {
    if (!options.enabled) return
    const host = normalizedWebHost(header)
    if (!host || protectedHosts.has(host)) return
    refresh()
    const route = routes.get(host)
    if (route) return invalid ? { ...route, active: false } : route
  }
  const middleware = (req: Request, res: Response, next: NextFunction) => {
    const route = lookup(req.headers.host)
    if (!route) return next()
    if (!req.secure && !(req.socket as any).encrypted) {
      res.redirect(308, `https://${route.host}${req.originalUrl}`)
      return
    }
    if (!route.active || !route.target) {
      res.setHeader('Cache-Control', 'no-store')
      res.status(503).send('Application non publiée ou arrêtée.')
      return
    }
    const admission = admitTraffic(route.appId, req)
    if (admission && !admission.allowed) {
      res.setHeader('Retry-After', admission.retryAfter)
      res.setHeader('Cache-Control', 'no-store')
      res.status(429).send('Limite de débit de ce projet atteinte. Réessayez plus tard.')
      return
    }
    if (admission) {
      res.once('finish', admission.release)
      res.once('close', admission.release)
    }
    // Next asset names contain their build/hash. Retained static assets keep old
    // open pages usable after a release, without replaying any application API.
    if (req.method === 'GET' || req.method === 'HEAD') {
      try {
        if (req.path.startsWith('/_next/static/')) {
          const relative = decodeURIComponent(req.path.slice('/_next/static/'.length))
          if (
            relative &&
            relative.length <= 1500 &&
            !relative.includes('\\') &&
            !relative.includes('\0') &&
            !relative.split('/').some((part) => part === '..' || part === '.')
          ) {
            const base = join(root, 'assets', route.appId)
            const releases = readdirSync(base, { withFileTypes: true })
              .filter((row) => row.isDirectory() && /^[a-z0-9]{15}$/.test(row.name))
              .sort((a, b) => statSync(join(base, b.name)).mtimeMs - statSync(join(base, a.name)).mtimeMs)
              .slice(0, 3)
            for (const release of releases) {
              const dir = join(base, release.name),
                file = resolve(dir, relative)
              if (!file.startsWith(dir + sep)) continue
              try {
                if (realpathSync(file) !== file || !statSync(file).isFile() || statSync(file).size > 128 * 1024 * 1024)
                  continue
                res.setHeader('Cache-Control', 'public, max-age=31536000, immutable')
                res.sendFile(file, (error) => {
                  if (error && !res.headersSent) next()
                })
                return
              } catch {}
            }
          }
        }
      } catch {}
    }
    setProxyHeaders(req, route.host, 'https', clientIp(req))
    return proxy(req, res, next)
  }
  /** HTTP01 is served only for registered custom domains, never arbitrary hosts/files. */
  const challenge = (req: Request, res: Response, next: NextFunction) => {
    if (!options.enabled || !['GET', 'HEAD'].includes(req.method)) return next()
    const match = /^\/\.well-known\/acme-challenge\/([A-Za-z0-9_-]{1,200})$/.exec(req.path)
    const route = lookup(req.headers.host)
    if (!match || !route || generated(route.host) || invalid) return next()
    try {
      const dir = join(root, 'acme', '.well-known', 'acme-challenge')
      const file = join(dir, match[1]!)
      if (lstatSync(file).isSymbolicLink() || realpathSync(file) !== file || statSync(file).size > 4096) throw Error()
      res.setHeader('Content-Type', 'text/plain')
      res.setHeader('Cache-Control', 'no-store')
      res.send(readFileSync(file))
    } catch {
      res.sendStatus(404)
    }
  }
  const secureContext = (servername: string): SecureContext | undefined => {
    const route = lookup(servername)
    if (!route?.certificate || invalid) return
    try {
      const dir = join(root, 'tls', route.host)
      const certFile = join(dir, 'fullchain.pem'),
        keyFile = join(dir, 'privkey.pem')
      // File paths are fixed from a validated host, not supplied by the registry.
      if (
        [certFile, keyFile].some(
          (file) => lstatSync(file).isSymbolicLink() || !realpathSync(file).startsWith(root + sep)
        )
      )
        return
      const certStat = statSync(certFile),
        keyStat = statSync(keyFile)
      if (certStat.size > 100_000 || keyStat.size > 20_000) return
      const signature = `${certStat.mtimeMs}:${certStat.size}:${keyStat.mtimeMs}:${keyStat.size}`
      const cached = contexts.get(route.host)
      if (cached?.signature === signature) return cached.context
      const context = createSecureContext({ cert: readFileSync(certFile), key: readFileSync(keyFile) })
      contexts.set(route.host, { signature, context })
      return context
    } catch {
      return contexts.get(route.host)?.context
    }
  }
  const upgrade = (req: IncomingMessage, socket: Socket, head: Buffer) => {
    const route = lookup(req.headers.host)
    if (!route) return false
    if (route.active && route.target && (req.socket as any).encrypted) {
      const admission = admitTraffic(route.appId, req)
      if (admission && !admission.allowed) {
        socket.end(
          `HTTP/1.1 429 Too Many Requests\r\nRetry-After: ${admission.retryAfter}\r\nConnection: close\r\n\r\n`
        )
        return true
      }
      if (admission) socket.once('close', admission.release)
      setProxyHeaders(req, route.host, 'https', clientIp(req))
      proxy.upgrade(req as Request, socket, head)
    } else socket.destroy()
    return true
  }
  return { middleware, challenge, lookup, secureContext, upgrade }
}
