import { isIPv4, isIPv6 } from 'node:net'

export const WEIGHT_DEN = 10
export const FILES_WEIGHT_NUM = 1
export const API_WEIGHT_NUM = WEIGHT_DEN

export const toProxyCidrString = (entry: string): string => {
  if (entry.includes('/')) return entry
  if (isIPv4(entry)) return `${entry}/32`
  if (isIPv6(entry)) return `${entry}/128`
  return entry
}

/** Clé d'hôte des limiteurs : `Exemple.com.` et `exemple.com` ne font qu'un seau. */
export const normalizedRateLimitHost = (hostname: string | undefined): string | undefined => {
  const host = hostname?.trim().toLowerCase().replace(/\.+$/, '')
  return host && host.length <= 253 ? host : undefined
}

/**
 * Requêtes en cours par clé. Une clé n'existe que tant qu'une requête est en vol : contrairement à un limiteur à
 * `duration: 0`, qui ne purge jamais ses clés, la mémoire reste bornée par le trafic simultané.
 */
export const createConcurrencyLimiter = (maxPoints: number) => {
  const inFlight = new Map<string, number>()
  return {
    tryAcquire(key: string, weight: number) {
      const used = inFlight.get(key) || 0
      if (used + weight > maxPoints) return false
      inFlight.set(key, used + weight)
      return true
    },
    release(key: string, weight: number) {
      const left = (inFlight.get(key) || 0) - weight
      if (left > 0) inFlight.set(key, left)
      else inFlight.delete(key)
    },
    size: () => inFlight.size,
  }
}

export const isPocketBaseFilesPath = (path: string): boolean => path === '/api/files' || path.startsWith('/api/files/')

export const isHealthProbePath = (path: string): boolean =>
  path === '/api/firewall/health' || path === '/_api/daemon/health' || path.startsWith('/_api/daemon/vacuum')

export const toMicroPointLimit = (cfg: { points: number; duration: number }) => ({
  points: cfg.points * WEIGHT_DEN,
  duration: cfg.duration,
})

export const consumeWeightForPath = (path: string): number =>
  isPocketBaseFilesPath(path) ? FILES_WEIGHT_NUM : API_WEIGHT_NUM
