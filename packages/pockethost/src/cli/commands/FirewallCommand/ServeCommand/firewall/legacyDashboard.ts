import type { Handler, Request, Response } from 'express'
import { createProxyMiddleware } from 'http-proxy-middleware'
import {
  dashboardRequestHost,
  legacyDashboardHosts,
  legacyGithubWebhookPath,
} from '../../../../../common/platformDashboardHosts'
import { setControllerProxyHeaders } from '../../../../../webApps/proxyHeaders'
import type { ClientIpResolver } from './clientIp'
import { isWebhookException } from './webhookException'

export { dashboardRequestHost }

/** Preserve raw-body HMAC and the current per-IP limit while mapping only the imported controller's legacy endpoint. */
export function createSignedWebhookProxyMiddleware(options: {
  dashboardHost: string
  dashboardHosts: Set<string>
  controllerTarget: string
  ips: ClientIpResolver
  limit: Handler
}): Handler {
  const proxy = createProxyMiddleware<Request, Response>({
    target: options.controllerTarget,
    changeOrigin: false,
    xfwd: false,
  })
  return (req, res, next) => {
    if (!isWebhookException(req.method, req.path, req.headers.host || '', options.dashboardHosts)) return next()
    options.limit(req, res, () => {
      const host = dashboardRequestHost(req.headers.host)
      const pathname = req.path
      const rewritten = legacyGithubWebhookPath(options.dashboardHost, host, pathname)
      if (rewritten !== pathname) req.url = rewritten + req.url.slice(pathname.length)
      setControllerProxyHeaders(req, host, options.ips.forwardedProto(req), options.ips.clientIp(req))
      return proxy(req, res, next)
    })
  }
}

/** Installed after the dashboard IP policy. Public signed webhooks have their own earlier, rate-limited path. */
export function createLegacyDashboardMiddleware(options: {
  dashboardHost: string
  controllerTarget: string
  ips: ClientIpResolver
}): Handler {
  const legacy = new Set(legacyDashboardHosts(options.dashboardHost))
  const proxy = createProxyMiddleware<Request, Response>({
    target: options.controllerTarget,
    changeOrigin: false,
    xfwd: false,
  })
  return (req, res, next) => {
    const host = dashboardRequestHost(req.headers.host)
    if (!legacy.has(host)) return next()
    if (req.path === '/api' || req.path.startsWith('/api/') || req.path === '/stats.json') {
      setControllerProxyHeaders(req, host, options.ips.forwardedProto(req), options.ips.clientIp(req))
      return proxy(req, res, next)
    }
    res.setHeader('Cache-Control', 'no-store')
    res.setHeader('Referrer-Policy', 'no-referrer')
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.status(405).end()
      return
    }
    res.redirect(302, `https://${options.dashboardHost}${req.originalUrl}`)
  }
}
