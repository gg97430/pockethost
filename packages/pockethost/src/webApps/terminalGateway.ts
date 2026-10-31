import type { NextFunction, Request, Response } from 'express'
import { createProxyMiddleware } from 'http-proxy-middleware'
import type { IncomingMessage } from 'node:http'
import type { Socket } from 'node:net'
import { peerIp } from '../cli/commands/FirewallCommand/ServeCommand/firewall/clientIp'

const WINDOW_MS = 60000
const GLOBAL_LIMIT = 120
const IP_LIMIT = 30
const MAX_IP_ENTRIES = 1000

export function createTerminalGateway(options: {
  enabled: boolean
  dashboardHost: string
  /** IP calculée par le pare-feu ; à défaut le pair TCP, jamais un en-tête. */
  clientIp?: (req: IncomingMessage) => string
}) {
  const path = '/api/web-terminal/connect',
    host = options.dashboardHost.toLowerCase(),
    clientIp = options.clientIp || peerIp
  const dashboard = new URL(`https://${host}`)
  if (
    dashboard.host !== host ||
    dashboard.pathname !== '/' ||
    dashboard.search ||
    dashboard.hash ||
    dashboard.username ||
    dashboard.password
  )
    throw new Error('Exact terminal dashboard hostname required')
  const matches = (req: IncomingMessage) => {
    const incomingHost = req.headers.host?.toLowerCase()
    return (
      options.enabled && (incomingHost === host || incomingHost === `${host}:443`) && req.url?.split('?')[0] === path
    )
  }
  const reject = (socket: Socket, status: number) => {
    const reason = status === 403 ? 'Forbidden' : status === 429 ? 'Too Many Requests' : 'Bad Request'
    socket.end(`HTTP/1.1 ${status} ${reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`)
  }
  const attempts = new Map<string, { at: number; count: number }>()
  let windowStart = Date.now(),
    globalCount = 0
  const allowed = (ip: string, now: number) => {
    if (now < windowStart || now - windowStart >= WINDOW_MS) {
      windowStart = now
      globalCount = 0
    }
    let entry = attempts.get(ip)
    if (entry && (now < entry.at || now - entry.at >= WINDOW_MS)) {
      attempts.delete(ip)
      entry = undefined
    }
    // La limite par IP passe d'abord : une IP déjà bloquée ne consomme plus le quota commun.
    if (entry && entry.count >= IP_LIMIT) return false
    if (globalCount >= GLOBAL_LIMIT) return false
    if (!entry) {
      if (attempts.size >= MAX_IP_ENTRIES) {
        for (const [key, value] of attempts) if (now < value.at || now - value.at >= WINDOW_MS) attempts.delete(key)
        if (attempts.size >= MAX_IP_ENTRIES) return false
      }
      entry = { at: now, count: 0 }
      attempts.set(ip, entry)
    }
    entry.count++
    globalCount++
    return true
  }
  const proxy = createProxyMiddleware<Request, Response>({
    target: 'http://127.0.0.1:3192',
    changeOrigin: false,
    ws: false,
    on: {
      proxyReqWs: (outgoing) => {
        for (const name of ['authorization', 'cookie', 'x-web-worker', 'x-pockethost-secret'])
          outgoing.removeHeader(name)
      },
      error: (_error, _req, output) => {
        if ('writeHead' in output && !output.headersSent) {
          output.writeHead(502)
          output.end()
        } else output.destroy()
      },
    },
  })
  return {
    middleware(req: Request, res: Response, next: NextFunction) {
      if (!matches(req)) return next()
      res
        .status(426)
        .set({ Upgrade: 'websocket', 'Cache-Control': 'no-store' })
        .end('Connexion WebSocket sécurisée requise.')
    },
    upgrade(req: IncomingMessage, socket: Socket, head: Buffer) {
      if (!matches(req)) return false
      if (req.url !== path || req.method !== 'GET') {
        reject(socket, 400)
        return true
      }
      if (!(req.socket as any).encrypted || req.headers.origin !== `https://${host}`) {
        reject(socket, 403)
        return true
      }
      if (!allowed(clientIp(req), Date.now())) {
        reject(socket, 429)
        return true
      }
      // The broker validates the one-use grant in Sec-WebSocket-Protocol. Never log it.
      proxy.upgrade(req as Request, socket, head)
      return true
    },
  }
}
