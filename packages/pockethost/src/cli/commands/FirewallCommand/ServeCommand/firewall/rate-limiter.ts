import express from 'express'
import { RateLimiterMemory } from 'rate-limiter-flexible'
import { Logger } from 'src/common'
import { createClientIpResolver, validClientIp, type ClientIpResolver } from './clientIp'
import {
  API_WEIGHT_NUM,
  createConcurrencyLimiter,
  FILES_WEIGHT_NUM,
  isHealthProbePath,
  isPocketBaseFilesPath,
  normalizedRateLimitHost,
  toMicroPointLimit,
  WEIGHT_DEN,
} from './rateLimiterPure'

const headerContains = (header: string | string[] | undefined, token: string): boolean => {
  if (!header) return false
  const tokenLc = token.toLowerCase()
  if (Array.isArray(header)) return header.some((value) => value.toLowerCase().includes(tokenLc))
  return header.toLowerCase().includes(tokenLc)
}

const isCfImageService = (req: express.Request): boolean => {
  const viaMatches = headerContains(req.headers['via'], 'image-resizing-proxy')
  if (!viaMatches) return false

  const cdnLoopMatches = headerContains(req.headers['cdn-loop'], 'cloudflare')
  if (!cdnLoopMatches) return false

  return true
}

/** For rate-limit logs: raw Host / X-Forwarded-Host vs req.hostname (Express parses Host). */
const hostForensics = (req: express.Request) => {
  const rawHost = req.get('host')
  const forwardedHost = req.get('x-forwarded-host')
  const fromXfh = forwardedHost?.split(',')[0]?.trim().split(':')[0]?.toLowerCase()
  const xForwardedHostConflicts = fromXfh && req.hostname && fromXfh !== req.hostname.toLowerCase() ? true : undefined

  return {
    hostname: req.hostname,
    hostHeader: rawHost ?? null,
    xForwardedHost: forwardedHost ?? null,
    xForwardedHostConflicts,
    method: req.method,
    url: req.originalUrl?.slice(0, 256) ?? '',
  }
}

const LIMITS = {
  untrustedIp: { points: 1000, duration: 60 * 60 },
  untrustedHostname: { points: 10000, duration: 60 * 60 },
  trustedIp: { points: 5000, duration: 60 * 60 },
  trustedHostname: { points: 20000, duration: 60 * 60 },
  untrustedIpConcurrent: { points: 15 },
  trustedIpConcurrent: { points: 50 },
  untrustedHostnameConcurrent: { points: 250 },
  trustedHostnameConcurrent: { points: 250 },
} as const

/** Larger denominator → cheaper file routes vs API (FILES_WEIGHT_NUM / WEIGHT_DEN). */

/** Scale bucket sizes so weighted consume keeps semantic API limits unchanged. */
export const createRateLimiterMiddleware = (logger: Logger, ips: ClientIpResolver = createClientIpResolver()) => {
  const rateLimiterLogger = logger.create(`RateLimiter`)
  const { dbg } = rateLimiterLogger
  dbg(`Creating`)

  /** Proxy listé dans `PH_USER_PROXY_IPS` : seule source acceptée pour l'en-tête public `X-Pockethost-Client-Ip`. */
  const getClientIp = (req: express.Request, connectingIp: string): string =>
    (ips.isUserProxyIp(connectingIp) && validClientIp(req.headers['x-pockethost-client-ip'])) || connectingIp

  const untrustedIpRateLimiter = new RateLimiterMemory(toMicroPointLimit(LIMITS.untrustedIp))
  const untrustedHostnameRateLimiter = new RateLimiterMemory(toMicroPointLimit(LIMITS.untrustedHostname))
  const trustedIpRateLimiter = new RateLimiterMemory(toMicroPointLimit(LIMITS.trustedIp))
  const trustedHostnameRateLimiter = new RateLimiterMemory(toMicroPointLimit(LIMITS.trustedHostname))

  // Concurrent request limiters: keys only exist while a request is in flight.
  const concurrencyLimiter = (cfg: { points: number }) => createConcurrencyLimiter(cfg.points * WEIGHT_DEN)
  const untrustedIpConcurrentLimiter = concurrencyLimiter(LIMITS.untrustedIpConcurrent)
  const trustedIpConcurrentLimiter = concurrencyLimiter(LIMITS.trustedIpConcurrent)
  const untrustedHostnameConcurrentLimiter = concurrencyLimiter(LIMITS.untrustedHostnameConcurrent)
  const trustedHostnameConcurrentLimiter = concurrencyLimiter(LIMITS.trustedHostnameConcurrent)

  return async (req: express.Request, res: express.Response, next: express.NextFunction) => {
    if (isHealthProbePath(req.path)) {
      next()
      return
    }

    const connectingIp = ips.clientIp(req)
    const endClientIp = getClientIp(req, connectingIp)
    const hostname = normalizedRateLimitHost(req.hostname)
    // `Via` et `CDN-Loop` sont de simples en-têtes : ils ne valent que venant d'un pair Cloudflare ou d'un proxy listé.
    const cfImageService = ips.isTrustedProxy(req) && isCfImageService(req)
    const userProxy = ips.isUserProxyIp(connectingIp)
    const trustedClient = cfImageService || userProxy
    const trustReason = cfImageService ? 'cf-image' : userProxy ? 'user-proxy' : 'none'

    const { dbg, warn } = rateLimiterLogger
      .create(hostname || `unknown`)
      .breadcrumb(connectingIp)
      .breadcrumb(endClientIp)
    const logRateLimitExceeded = trustedClient ? warn : dbg

    if (trustedClient) {
      dbg(`Trusted client detected (reason=${trustReason})`, req.headers)
    }

    dbg(`\n`)
    dbg(`--------------------------------`)
    dbg(`Incoming request`)
    dbg(`Hostname: ${hostname}`)
    dbg(`Connecting IP: ${connectingIp}`)
    dbg(`End Client IP: ${endClientIp}`)
    dbg(`Trust: ${trustedClient} (reason=${trustReason})`)
    dbg(`\n`)

    if (!hostname) {
      warn(
        `Could not determine hostname. connectingIp=${connectingIp} endClientIp=${endClientIp} hostHeader=${req.get('host') ?? 'none'} xForwardedHost=${req.get('x-forwarded-host') ?? 'none'}`
      )
      res.status(400).send(`Hostname not found`)
      return
    }

    if (userProxy) {
      dbg(`User Proxy IP detected`, req.headers)
    }

    const consumeWeight = isPocketBaseFilesPath(req.path) ? FILES_WEIGHT_NUM : API_WEIGHT_NUM

    // Check rate limits first (requests per hour per IP per hostname)
    {
      const key = `${endClientIp}:${hostname}`
      const cfg = trustedClient ? LIMITS.trustedIp : LIMITS.untrustedIp
      const limiter = trustedClient ? trustedIpRateLimiter : untrustedIpRateLimiter
      try {
        const ipResult = await limiter.consume(key, consumeWeight)
        dbg(
          `${trustedClient ? 'Trusted' : 'Untrusted'} IP request accepted. Key: ${key}. Points remaining: ${ipResult.remainingPoints}${
            trustedClient ? ' (trusted)' : ''
          } (weight=${consumeWeight}/${WEIGHT_DEN})`
        )
      } catch (rateLimiterRes: any) {
        const retryAfter = Math.ceil(rateLimiterRes.msBeforeNext / 1000)
        logRateLimitExceeded(
          `[1] ${trustedClient ? 'Trusted' : 'Untrusted'} IP rate limit exceeded. ` +
            `key=${key} endClientIp=${endClientIp} connectingIp=${connectingIp} ` +
            `hostname=${hostname} trustReason=${trustReason} ` +
            `path=${req.path} weight=${consumeWeight}/${WEIGHT_DEN} ` +
            `limitApproxApi=${cfg.points}/${cfg.duration}s ` +
            `consumedMp=${rateLimiterRes.consumedPoints ?? 'n/a'} remainingMp=${rateLimiterRes.remainingPoints ?? 'n/a'} ` +
            `retryAfter=${retryAfter}s`,
          hostForensics(req)
        )
        res.set('Retry-After', String(retryAfter))
        res.status(429).send(`Too Many Requests: retry after ${retryAfter} seconds [1]`)
        return
      }
    }

    // Check hostname rate limit (requests per hour per hostname)
    {
      const key = hostname
      const cfg = trustedClient ? LIMITS.trustedHostname : LIMITS.untrustedHostname
      const limiter = trustedClient ? trustedHostnameRateLimiter : untrustedHostnameRateLimiter
      try {
        const hostnameResult = await limiter.consume(key, consumeWeight)
        dbg(
          `${trustedClient ? 'Trusted' : 'Untrusted'} Hostname request accepted. Key: ${key}. Points remaining: ${hostnameResult.remainingPoints}${
            trustedClient ? ' (trusted)' : ''
          } (weight=${consumeWeight}/${WEIGHT_DEN})`
        )
      } catch (rateLimiterRes: any) {
        const retryAfter = Math.ceil(rateLimiterRes.msBeforeNext / 1000)
        logRateLimitExceeded(
          `[2] ${trustedClient ? 'Trusted' : 'Untrusted'} Hostname rate limit exceeded. ` +
            `key=${key} endClientIp=${endClientIp} connectingIp=${connectingIp} ` +
            `hostname=${hostname} trustReason=${trustReason} ` +
            `path=${req.path} weight=${consumeWeight}/${WEIGHT_DEN} ` +
            `limitApproxApi=${cfg.points}/${cfg.duration}s ` +
            `consumedMp=${rateLimiterRes.consumedPoints ?? 'n/a'} remainingMp=${rateLimiterRes.remainingPoints ?? 'n/a'} ` +
            `retryAfter=${retryAfter}s`,
          hostForensics(req)
        )
        res.set('Retry-After', String(retryAfter))
        res.status(429).send(`Too Many Requests: retry after ${retryAfter} seconds [2]`)
        return
      }
    }

    const releaseConcurrentCallbacks: Array<() => void> = []

    // Helper to release concurrent points
    const releaseConcurrentPoints = () => {
      for (const release of releaseConcurrentCallbacks.splice(0, releaseConcurrentCallbacks.length)) release()
    }

    // Check concurrent limits per IP per hostname
    const ipConcurrentLimiterInstance = trustedClient ? trustedIpConcurrentLimiter : untrustedIpConcurrentLimiter
    const ipConcurrentCfg = trustedClient ? LIMITS.trustedIpConcurrent : LIMITS.untrustedIpConcurrent
    const ipConcurrentKey = `${endClientIp}:${hostname}`
    if (!ipConcurrentLimiterInstance.tryAcquire(ipConcurrentKey, consumeWeight)) {
      logRateLimitExceeded(
        `[3] ${trustedClient ? 'Trusted' : 'Untrusted'} IP concurrent limit exceeded. ` +
          `key=${ipConcurrentKey} endClientIp=${endClientIp} connectingIp=${connectingIp} ` +
          `hostname=${hostname} trustReason=${trustReason} ` +
          `path=${req.path} weight=${consumeWeight}/${WEIGHT_DEN} ` +
          `limitApproxApi=${ipConcurrentCfg.points}`,
        hostForensics(req)
      )
      res.set('Retry-After', '1')
      res.status(429).send(`Too Many Requests: concurrent request limit exceeded [3]`)
      return
    }
    releaseConcurrentCallbacks.push(() => ipConcurrentLimiterInstance.release(ipConcurrentKey, consumeWeight))

    // Check overall concurrent limits per host
    const hostnameConcurrentLimiterInstance = trustedClient
      ? trustedHostnameConcurrentLimiter
      : untrustedHostnameConcurrentLimiter
    const hostnameConcurrentCfg = trustedClient ? LIMITS.trustedHostnameConcurrent : LIMITS.untrustedHostnameConcurrent
    const hostnameConcurrentKey = hostname
    if (!hostnameConcurrentLimiterInstance.tryAcquire(hostnameConcurrentKey, consumeWeight)) {
      releaseConcurrentPoints()
      logRateLimitExceeded(
        `[4] ${trustedClient ? 'Trusted' : 'Untrusted'} Hostname concurrent limit exceeded. ` +
          `key=${hostnameConcurrentKey} endClientIp=${endClientIp} connectingIp=${connectingIp} ` +
          `hostname=${hostname} trustReason=${trustReason} ` +
          `path=${req.path} weight=${consumeWeight}/${WEIGHT_DEN} ` +
          `limitApproxApi=${hostnameConcurrentCfg.points}`,
        hostForensics(req)
      )
      res.set('Retry-After', '1')
      res.status(429).send(`Too Many Requests: concurrent request limit exceeded [4]`)
      return
    }
    releaseConcurrentCallbacks.push(() =>
      hostnameConcurrentLimiterInstance.release(hostnameConcurrentKey, consumeWeight)
    )

    // Release concurrent points when response finishes
    res.on('finish', releaseConcurrentPoints)
    res.on('close', releaseConcurrentPoints)

    next()
  }
}
