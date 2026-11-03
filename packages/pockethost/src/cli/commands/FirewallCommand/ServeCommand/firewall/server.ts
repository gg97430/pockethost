import {
  APEX_DOMAIN,
  corsMiddleware,
  createEnforceHttps,
  DAEMON_PORT,
  IPCIDR_LIST,
  IS_DEV,
  Logger,
  MOTHERSHIP_NAME,
  MOTHERSHIP_PORT,
  PH_DISABLE_FIREWALL_RATE_LIMIT,
  PH_FIREWALL_DAEMON_GRACE_MS,
  PH_USER_PROXY_IPS,
  SSL_CERT,
  SSL_KEY,
} from '@'
import { exec } from 'child_process'
import express, { ErrorRequestHandler, Request, Response } from 'express'
import { existsSync, readFileSync } from 'fs'
import type { IncomingMessage } from 'http'
import http from 'http'
import { createProxyMiddleware } from 'http-proxy-middleware'
import https from 'https'
import type { Socket } from 'net'
import { resolve } from 'node:path'
import {
  legacyControllerHosts,
  legacyDashboardHosts,
  PLATFORM_CONTROL_HOSTS,
} from '../../../../../common/platformDashboardHosts'
import { FIREWALL_CLIENT_IP_HEADER } from '../../../../../webApps/proxyHeaders'
import { createWebAppRouting } from '../../../../../webApps/routing'
import { createTerminalGateway } from '../../../../../webApps/terminalGateway'
import { createIpWhitelistMiddleware } from './cidr'
import { createClientIpResolver } from './clientIp'
import { createVhostProxyMiddleware, type VhostUpgradeHandler } from './createVhostProxyMiddleware'
import { createDaemonGraceMiddleware, createDaemonProxyErrorHandler } from './daemonGrace'
import { createLegacyDashboardMiddleware, createSignedWebhookProxyMiddleware } from './legacyDashboard'
import { createRateLimiterMiddleware } from './rate-limiter'
import { secretMatches } from './secretMatches'
import {
  createIpRateLimit,
  createWebLimiter,
  rateLimitFromEnv,
  WEB_IP_RATE_LIMIT,
  WEBHOOK_IP_RATE_LIMIT,
} from './webLimit'
import { createWebLimitPolicy } from './webLimitPolicy'
import { isWorkerOnlyPath, refuseWorkerRoutes } from './workerRoutes'

export type FirewallOptions = {
  logger: Logger
}

export const firewall = async ({ logger }: FirewallOptions) => {
  const { dbg, info, error } = logger.create(`firewall`)

  // Seul point de calcul de l'IP client : limiteurs, terminal, relais et liste d'adresses partagent ce résultat.
  const ips = createClientIpResolver(PH_USER_PROXY_IPS(), (entry) =>
    error(`Invalid PH_USER_PROXY_IPS entry, skipping: ${entry}`)
  )

  // 127.0.0.1 et non localhost : le contrôleur n'écoute que sur la boucle locale IPv4.
  const dashboardHost = process.env.APP_HOST || `app.${APEX_DOMAIN()}`
  const legacyHosts = legacyDashboardHosts(dashboardHost)
  const PROD_ROUTES = {
    ...Object.fromEntries(
      legacyControllerHosts(dashboardHost).map((host) => [host, `http://127.0.0.1:${MOTHERSHIP_PORT()}`])
    ),
    [dashboardHost]: `http://127.0.0.1:5174`,
    [`${MOTHERSHIP_NAME()}.${APEX_DOMAIN()}`]: `http://127.0.0.1:${MOTHERSHIP_PORT()}`,
  }
  const DEV_ROUTES = {
    [`mail.${APEX_DOMAIN()}`]: `http://localhost:${1080}`,
    [`${MOTHERSHIP_NAME()}.${APEX_DOMAIN()}`]: `http://127.0.0.1:${MOTHERSHIP_PORT()}`,
    [`app.${APEX_DOMAIN()}`]: `http://localhost:${5174}`,
    [`${APEX_DOMAIN()}`]: `http://localhost:${5174}`,
  }
  const hostnameRoutes = IS_DEV() ? DEV_ROUTES : PROD_ROUTES
  const tlsReady = existsSync(SSL_KEY()) && existsSync(SSL_CERT())

  const webEnabled = process.env.WEB_APPS_ENABLED === 'true'
  const webRouting = createWebAppRouting({
    enabled: webEnabled,
    root: process.env.WEB_APPS_ROOT || resolve(process.env.DATA_ROOT || '.', 'web-apps'),
    apexDomain: APEX_DOMAIN(),
    protectedHosts: [
      ...Object.keys(hostnameRoutes),
      APEX_DOMAIN(),
      `app.${APEX_DOMAIN()}`,
      ...PLATFORM_CONTROL_HOSTS,
      process.env.APP_HOST || `app.${APEX_DOMAIN()}`,
    ],
    platformAliases: (process.env.WEB_APPS_PLATFORM_ALIASES || '')
      .split(',')
      .map((host) => host.trim().toLowerCase())
      .filter(Boolean),
    onError: (message) => error(message),
    clientIp: ips.clientIp,
  })

  const terminalGateway = createTerminalGateway({
    enabled: webEnabled,
    dashboardHost: process.env.APP_HOST || `app.${APEX_DOMAIN()}`,
    clientIp: ips.clientIp,
  })

  const webIpLimit = rateLimitFromEnv(process.env.WEB_APPS_IP_RATE_LIMIT, WEB_IP_RATE_LIMIT)
  const webLimiter = createWebLimiter({
    limit: webIpLimit,
    clientIp: ips.clientIp,
    readLimit: createWebLimitPolicy(
      process.env.WEB_APPS_ROOT || resolve(process.env.DATA_ROOT || '.', 'web-apps'),
      webIpLimit
    ),
  })
  if (webEnabled) {
    info(`Web sites rate limit: ${webIpLimit ? `${webIpLimit}/min/IP` : 'disabled'} (WEB_APPS_IP_RATE_LIMIT)`)
  }

  // Create Express app
  const app = express()
  // En-tête réservé au pare-feu : une valeur reçue d'Internet ne survit à aucun chemin (relais, démon, sites).
  app.use((req, _res, next) => {
    delete req.headers[FIREWALL_CLIENT_IP_HEADER]
    next()
  })
  // Routes du worker et des services locaux : jamais relayées, quel que soit l'hôte demandé.
  app.use(refuseWorkerRoutes)
  app.use(terminalGateway.middleware)
  // These handlers recognize only web-owned hosts. Existing PocketBase access rules stay below.
  // Le limiteur des sites passe avant le challenge ACME et ne dépend pas de PH_DISABLE_FIREWALL_RATE_LIMIT.
  if (webEnabled) {
    app.use((req, res, next) => (webRouting.lookup(req.headers.host) ? webLimiter.middleware(req, res, next) : next()))
  }
  app.use(webRouting.challenge)
  if (webEnabled && !PH_DISABLE_FIREWALL_RATE_LIMIT()) {
    const webLimit = createRateLimiterMiddleware(logger, ips)
    app.use((req, res, next) => (webRouting.lookup(req.headers.host) ? webLimit(req, res, next) : next()))
  }
  app.use(webRouting.middleware)

  app.options('/{*path}', corsMiddleware)
  app.use(corsMiddleware)
  if (!IS_DEV() || tlsReady) {
    app.use(createEnforceHttps(ips.isTrustedProxy))
  }

  app.get(`/api/firewall/health`, (req, res, next) => {
    dbg(`Health check`)
    res.json({ status: 'firewall ok', code: 200 })
    res.end()
  })

  app.get(`/api/_private/reset`, (req, res, next) => {
    dbg(`Reset password`)

    const secretParam = Array.isArray((req.query as any)?.secret)
      ? (req.query as any).secret[0]
      : (req.query as any)?.secret

    if (!secretMatches(secretParam, process.env.PH_SECRET)) {
      res.status(401).json({ error: 'unauthorized', code: 401 })
      res.end()
      return
    }

    const { error: logError } = logger.create(`firewall`)
    exec('pm2 restart edge-daemon', (err, stdout, stderr) => {
      if (err) {
        logError(err)
        res.status(500).json({ error: 'pm2 reset failed', code: 500 })
        res.end()
        return
      }
      info(stdout)
      info(stderr)

      res.json({ status: 'pm2 reset ok', code: 200 })
      res.end()
    })
  })

  // GitHub App and Git deploy-key deliveries may originate outside the dashboard's proxy
  // allowlist (exact paths only). The controller validates the raw-body HMAC (or the GitLab
  // token) before accepting any deployment job.
  if (webEnabled) {
    const dashboardHosts = new Set([...Object.keys(hostnameRoutes), dashboardHost, ...legacyHosts])
    // Chemins exacts mais publics : le HMAC est vérifié après lecture du corps, une limite par IP passe avant.
    const webhookLimit = createIpRateLimit({
      limit: rateLimitFromEnv(process.env.WEB_APPS_WEBHOOK_RATE_LIMIT, WEBHOOK_IP_RATE_LIMIT),
      clientIp: ips.clientIp,
    })
    app.use(
      createSignedWebhookProxyMiddleware({
        dashboardHost,
        dashboardHosts,
        controllerTarget: `http://127.0.0.1:${MOTHERSHIP_PORT()}`,
        ips,
        limit: webhookLimit,
      })
    )
  }

  // Use the IP blocker middleware
  app.use(createIpWhitelistMiddleware(IPCIDR_LIST(), ips.clientIp))
  if (PH_DISABLE_FIREWALL_RATE_LIMIT()) {
    info(`Firewall rate limiting disabled (PH_DISABLE_FIREWALL_RATE_LIMIT)`)
  } else {
    app.use(createRateLimiterMiddleware(logger, ips))
  }

  app.use(
    createLegacyDashboardMiddleware({
      dashboardHost,
      controllerTarget: `http://127.0.0.1:${MOTHERSHIP_PORT()}`,
      ips,
    })
  )

  const graceMs = PH_FIREWALL_DAEMON_GRACE_MS()
  if (graceMs > 0) {
    info(`Firewall daemon grace enabled: ${graceMs}ms (PH_FIREWALL_DAEMON_GRACE_MS)`)
  }

  const vhostUpgradeHandlers: VhostUpgradeHandler[] = []
  Object.entries(hostnameRoutes).forEach(([host, target]) => {
    const { middleware, upgrade } = createVhostProxyMiddleware(host, target, IS_DEV(), logger, ips)
    app.use(middleware)
    if (upgrade) {
      vhostUpgradeHandlers.push(upgrade)
    }
  })

  const handleVhostUpgrade = (req: IncomingMessage, socket: Socket, head: Buffer) => {
    delete req.headers[FIREWALL_CLIENT_IP_HEADER]
    if (isWorkerOnlyPath(req.url)) {
      socket.destroy()
      return
    }
    if (terminalGateway.upgrade(req, socket, head)) return
    for (const upgrade of vhostUpgradeHandlers) {
      if (upgrade(req, socket, head)) {
        return
      }
    }
    // Sites hébergés : plafond vérifié avant toute connexion vers le conteneur.
    if (webRouting.lookup(req.headers.host) && !webLimiter.upgrade(req, socket)) return
    if (webRouting.upgrade(req, socket, head)) return
    socket.destroy()
  }

  const daemonTarget = `http://localhost:${DAEMON_PORT()}`
  const daemonProxyErrorHandler = createDaemonProxyErrorHandler(logger)
  const handler = createProxyMiddleware<Request, Response>({
    target: daemonTarget,
    on: {
      error: (err, req, res) => {
        if (!('status' in res)) return
        daemonProxyErrorHandler(err, req, res, () => undefined)
      },
    },
  })

  app.use(createDaemonGraceMiddleware(logger))
  app.use((req, res, next) => {
    const method = req.method
    const fullUrl = req.protocol + '://' + req.get('host') + req.originalUrl

    dbg(`${method} ${fullUrl} -> ${daemonTarget}`)

    handler(req, res, next)
  })

  const errorHandler: ErrorRequestHandler = (err, req, res, next) => {
    if (res.headersSent) {
      next(err)
      return
    }
    daemonProxyErrorHandler(err, req, res, next)
  }
  app.use(errorHandler)

  const httpServer = http.createServer(app)
  httpServer.on('error', (err) => {
    error(err)
  })
  if (webEnabled || vhostUpgradeHandlers.length > 0) {
    httpServer.on('upgrade', handleVhostUpgrade)
  }
  httpServer.listen(80, () => {
    dbg(tlsReady ? 'HTTP redirect server listening on 80' : 'HTTP server listening on 80')
  })

  if (!tlsReady && !IS_DEV()) {
    throw new Error(`TLS cert missing: ${SSL_CERT()}`)
  }

  if (tlsReady) {
    const httpsOptions = {
      key: readFileSync(SSL_KEY()),
      cert: readFileSync(SSL_CERT()),
      ...(webEnabled
        ? {
            SNICallback: (
              servername: string,
              callback: (error: Error | null, context?: import('node:tls').SecureContext) => void
            ) => callback(null, webRouting.secureContext(servername)),
          }
        : {}),
    }

    const httpsServer = https.createServer(httpsOptions, app)
    httpsServer.on('error', (err) => {
      error(err)
    })
    if (webEnabled || vhostUpgradeHandlers.length > 0) {
      httpsServer.on('upgrade', handleVhostUpgrade)
    }

    httpsServer.listen(443, () => {
      dbg('HTTPS server running on port 443')
    })
  } else if (IS_DEV()) {
    dbg('HTTPS skipped — run pnpm dev:cli serve to generate dev TLS certs')
  }
}
