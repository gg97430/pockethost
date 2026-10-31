import type { Handler, Request, RequestHandler } from 'express'

export const corsMiddleware: RequestHandler = (req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*')
  res.setHeader('Access-Control-Allow-Methods', 'GET,HEAD,PUT,PATCH,POST,DELETE,OPTIONS')
  const requestedHeaders = req.headers['access-control-request-headers']
  if (requestedHeaders) {
    res.setHeader('Access-Control-Allow-Headers', requestedHeaders)
  }
  if (req.method === 'OPTIONS') {
    res.status(204).end()
    return
  }
  next()
}

/**
 * `X-Forwarded-Proto` ne compte que venant d'un proxy de confiance : Cloudflare en mode Flexible parle HTTP à
 * l'origine, mais un client joignant directement l'origine sur le port 80 ne doit pas pouvoir s'en réclamer.
 */
export const createEnforceHttps =
  (isTrustedProxy: (req: Request) => boolean): RequestHandler =>
  (req, res, next) => {
    if (req.secure || (req.headers['x-forwarded-proto'] === 'https' && isTrustedProxy(req))) {
      next()
      return
    }
    const host = req.headers.host
    if (!host) {
      next()
      return
    }
    res.redirect(301, `https://${host}${req.originalUrl}`)
  }

export function vhost(hostname: string, handler: Handler): Handler {
  return (req, res, next) => {
    const host = req.headers.host?.split(':')[0]
    if (host === hostname) {
      return handler(req, res, next)
    }
    next()
  }
}
