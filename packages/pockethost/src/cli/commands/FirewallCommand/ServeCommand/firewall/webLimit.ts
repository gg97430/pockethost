import type { NextFunction, Request, Response } from 'express'
import type { IncomingMessage } from 'node:http'
import { isIP, type Socket } from 'node:net'

const WINDOW_MS = 60_000
const MAX_KEYS = 100_000
export const WEB_IP_RATE_LIMIT = 1200
export const WEBHOOK_IP_RATE_LIMIT = 300
export const MAX_UPGRADES_PER_IP = 100
export const MAX_UPGRADES_TOTAL = 20_000

type ClientIp = (req: IncomingMessage) => string

/** Valeur de `WEB_APPS_IP_RATE_LIMIT` : 0 désactive ; une valeur illisible garde le plafond par défaut. */
export const rateLimitFromEnv = (value: string | undefined, fallback: number) => {
  const limit = value?.trim() ? Number(value) : NaN
  return Number.isInteger(limit) && limit >= 0 ? limit : fallback
}

/** Un abonné IPv6 dispose d'un /64 entier : compter chaque adresse reviendrait à ne rien limiter. */
export const rateLimitKey = (ip: string) => {
  if (isIP(ip) !== 6) return ip
  const [head = '', tail = ''] = ip.split('::')
  const left = head ? head.split(':') : []
  const right = tail ? tail.split(':') : []
  const groups = [...left, ...Array(Math.max(0, 8 - left.length - right.length)).fill('0'), ...right]
  return `${groups
    .slice(0, 4)
    .map((group) => parseInt(group, 16).toString(16))
    .join(':')}::/64`
}

/** Fichiers `/_next/static/` servis tels quels ; un chemin qui remonte (`..`) reste compté, le site le normaliserait. */
export const isNextStaticRequest = (method: string, path: string) => {
  if ((method !== 'GET' && method !== 'HEAD') || !path.startsWith('/_next/static/')) return false
  try {
    const decoded = decodeURIComponent(path)
    return !/[\\\0]/.test(decoded) && !decoded.split('/').some((part) => part === '.' || part === '..')
  } catch {
    return false
  }
}

/**
 * Fenêtre fixe par IP, carte bornée. Une fenêtre renouvelée est réinsérée : la carte reste triée par début de fenêtre,
 * la purge s'arrête donc à la première fenêtre vivante et reste en O(1) amorti, même sous attaque. Carte pleine : la
 * plus ancienne clé est oubliée (son client repart à zéro) plutôt que de refuser des clients légitimes.
 */
export const createIpWindowLimiter = (options: { limit: number; maxKeys?: number; now?: () => number }) => {
  const { limit, maxKeys = MAX_KEYS, now = Date.now } = options
  const windows = new Map<string, { start: number; count: number }>()
  const expired = (start: number, at: number) => at < start || at - start >= WINDOW_MS
  return {
    hit(ip: string) {
      const at = now()
      const key = rateLimitKey(ip)
      let entry = windows.get(key)
      if (entry && expired(entry.start, at)) {
        windows.delete(key)
        entry = undefined
      }
      if (!entry) {
        for (const [oldest, row] of windows) {
          if (!expired(row.start, at)) break
          windows.delete(oldest)
        }
        if (windows.size >= maxKeys) windows.delete(windows.keys().next().value!)
        entry = { start: at, count: 0 }
        windows.set(key, entry)
      }
      if (entry.count >= limit)
        return { allowed: false, retryAfter: Math.max(1, Math.ceil((entry.start + WINDOW_MS - at) / 1000)) }
      entry.count++
      return { allowed: true, retryAfter: 0 }
    },
    size: () => windows.size,
  }
}

/** Middleware de limite par IP. Un plafond à 0 le désactive. */
export const createIpRateLimit = (options: {
  limit: number
  clientIp: ClientIp
  skip?: (req: Request) => boolean
  limiter?: ReturnType<typeof createIpWindowLimiter>
}) => {
  const limiter = options.limiter || createIpWindowLimiter(options)
  return (req: Request, res: Response, next: NextFunction) => {
    if (options.limit <= 0 || options.skip?.(req)) return next()
    const { allowed, retryAfter } = limiter.hit(options.clientIp(req))
    if (allowed) return next()
    res.set({ 'Retry-After': String(retryAfter), 'Cache-Control': 'no-store' })
    res.status(429).type('text/plain').send('Trop de requêtes. Réessayez dans un instant.')
  }
}

/** WebSocket ouverts par IP et au total : compteur rendu à la fermeture du socket, clé retirée à zéro. */
export const createUpgradeLimit = (options: { clientIp: ClientIp; perIp?: number; total?: number }) => {
  const { clientIp, perIp = MAX_UPGRADES_PER_IP, total = MAX_UPGRADES_TOTAL } = options
  const open = new Map<string, number>()
  let count = 0
  return {
    admit(req: IncomingMessage, socket: Socket) {
      const key = rateLimitKey(clientIp(req))
      const current = open.get(key) || 0
      if (count >= total || current >= perIp) return false
      open.set(key, current + 1)
      count++
      let released = false
      const release = () => {
        if (released) return
        released = true
        count--
        const left = (open.get(key) || 1) - 1
        if (left > 0) open.set(key, left)
        else open.delete(key)
      }
      socket.once('close', release)
      if (socket.destroyed) release()
      return true
    },
    open: () => count,
    keys: () => open.size,
  }
}

export const rejectUpgrade = (socket: Socket) =>
  socket.end('HTTP/1.1 429 Too Many Requests\r\nRetry-After: 5\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')

/**
 * Limiteur des sites hébergés, indépendant de `PH_DISABLE_FIREWALL_RATE_LIMIT` : uniquement par IP client, jamais par
 * hôte (un site visité par beaucoup d'IP ne doit pas se bloquer lui-même).
 */
export const createWebLimiter = (options: { limit: number; clientIp: ClientIp; now?: () => number }) => {
  const { limit, clientIp, now } = options
  const windows = createIpWindowLimiter({ limit, now })
  const upgrades = createUpgradeLimit({ clientIp })
  return {
    middleware: createIpRateLimit({
      limit,
      clientIp,
      limiter: windows,
      skip: (req) => isNextStaticRequest(req.method, req.path),
    }),
    /** Une ouverture compte comme une requête : sans cela, ouvrir et refermer en boucle resterait gratuit. */
    upgrade(req: IncomingMessage, socket: Socket) {
      if (limit <= 0) return true
      if (windows.hit(clientIp(req)).allowed && upgrades.admit(req, socket)) return true
      rejectUpgrade(socket)
      return false
    },
  }
}
