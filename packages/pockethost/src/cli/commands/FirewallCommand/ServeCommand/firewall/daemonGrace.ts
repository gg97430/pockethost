import { DAEMON_PORT, Logger, mkInternalUrl, PH_FIREWALL_DAEMON_GRACE_MS, PH_FIREWALL_DAEMON_GRACE_RETRY_MS } from '@'
import type { ErrorRequestHandler, RequestHandler, Response } from 'express'
import { isHealthProbePath } from './rateLimiterPure'

const daemonHealthUrl = () => `${mkInternalUrl(DAEMON_PORT())}/_api/daemon/health`

const isDaemonReady = async (url: string, timeoutMs: number) => {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
    if (!res.ok) return false
    const body = (await res.json()) as { status?: string }
    return body.status === 'ok'
  } catch {
    return false
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/**
 * Une seule sonde pour toutes les requêtes retenues : N requêtes en attente ne font pas N boucles vers le démon. Chaque
 * requête garde sa propre échéance ; la boucle s'arrête dès qu'il n'y a plus personne à servir.
 */
export const createDaemonWaiter = (
  probe: (timeoutMs: number) => Promise<boolean>,
  retryMs: () => number,
  onProbeFailed: () => void = () => undefined
) => {
  const waiting = new Set<(ready: boolean) => void>()
  let polling = false
  const poll = async () => {
    polling = true
    while (waiting.size) {
      if (await probe(retryMs())) {
        for (const settle of [...waiting]) settle(true)
        break
      }
      onProbeFailed()
      await sleep(retryMs())
    }
    polling = false
  }
  return (timeoutMs: number) =>
    new Promise<boolean>((resolve) => {
      const settle = (ready: boolean) => {
        clearTimeout(deadline)
        waiting.delete(settle)
        resolve(ready)
      }
      const deadline = setTimeout(settle, timeoutMs, false)
      waiting.add(settle)
      if (!polling) void poll()
    })
}

export const isDaemonUnreachableError = (err: unknown): boolean => {
  const msg = err instanceof Error ? err.message : `${err}`
  const code = err && typeof err === 'object' && 'code' in err ? `${(err as { code?: string }).code}` : ''
  return /ECONNREFUSED|ECONNRESET|EPIPE|ETIMEDOUT|socket hang up/i.test(`${code} ${msg}`)
}

export const sendDaemonUnavailable = (res: Response) => {
  if (res.headersSent || res.writableEnded) return
  res.set('Retry-After', '5')
  res
    .status(503)
    .send('PocketHost edge is temporarily unavailable while the hosting daemon restarts. Please retry shortly.')
}

export const createDaemonGraceMiddleware = (logger: Logger): RequestHandler => {
  const { dbg, info } = logger.create(`firewall:daemon-grace`)
  let lastOutageLogAt = 0
  const waitForDaemon = createDaemonWaiter(
    (timeoutMs) => isDaemonReady(daemonHealthUrl(), timeoutMs),
    PH_FIREWALL_DAEMON_GRACE_RETRY_MS,
    () => {
      if (Date.now() - lastOutageLogAt > 5000) {
        info(
          `Daemon unavailable — holding traffic up to ${PH_FIREWALL_DAEMON_GRACE_MS()}ms (PH_FIREWALL_DAEMON_GRACE_MS)`
        )
        lastOutageLogAt = Date.now()
      }
      dbg(`Daemon health check failed, retrying in ${PH_FIREWALL_DAEMON_GRACE_RETRY_MS()}ms`)
    }
  )

  return async (req, res, next) => {
    const graceMs = PH_FIREWALL_DAEMON_GRACE_MS()

    if (graceMs <= 0 || isHealthProbePath(req.path)) {
      next()
      return
    }

    const ready = await waitForDaemon(graceMs)
    if (req.aborted || res.writableEnded || res.headersSent) return
    if (ready) next()
    else sendDaemonUnavailable(res)
  }
}

export const createDaemonProxyErrorHandler = (logger: Logger): ErrorRequestHandler => {
  const { error } = logger.create(`firewall:daemon-proxy`)

  return (err, req, res, next) => {
    if (res.headersSent) {
      next(err)
      return
    }

    if (isDaemonUnreachableError(err)) {
      sendDaemonUnavailable(res)
      return
    }

    // Le détail (chemins, adresses internes) reste dans les journaux.
    error(err)
    res.status(502).send('PocketHost edge could not complete this request.')
  }
}
