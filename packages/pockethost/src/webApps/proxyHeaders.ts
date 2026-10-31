import type { IncomingMessage } from 'node:http'

/**
 * IP client calculée par le pare-feu pour le contrôleur (`trustedProxy.headers`, migration
 * 1793271500_enable_web_rate_limits). Volontairement distinct de `X-Pockethost-Client-Ip`, en-tête public que les
 * proxys listés dans `PH_USER_PROXY_IPS` envoient au limiteur du pare-feu : l'un est une déclaration d'un tiers,
 * l'autre une valeur que seul le pare-feu écrit.
 */
export const FIREWALL_CLIENT_IP_HEADER = 'x-pockethost-firewall-client-ip'

const DROPPED_HEADERS = [
  'forwarded',
  'via',
  'x-real-ip',
  'x-forwarded-port',
  'cf-connecting-ip',
  'true-client-ip',
  'x-pockethost-client-ip',
  'x-pockethost-secret',
  FIREWALL_CLIENT_IP_HEADER,
]

/**
 * Réécrit la requête avant le proxy : `proxyReq` n'est pas émis avec `Expect` et les WebSocket n'ont aucun hook, seul
 * `req.headers` couvre tous les chemins.
 */
export function setProxyHeaders(req: IncomingMessage, host: string, protocol: string, clientIp: string) {
  for (const name of DROPPED_HEADERS) delete req.headers[name]
  req.headers.host = host
  req.headers['x-forwarded-for'] = clientIp
  req.headers['x-forwarded-host'] = host
  req.headers['x-forwarded-proto'] = protocol
}

/**
 * Relais vers le contrôleur, direct ou via le dashboard. `X-Forwarded-For` reste présent : `webDirectLoopback` s'en
 * sert pour refuser les routes locales ; l'en-tête interne porte l'IP calculée, jamais celle fournie par le client.
 */
export function setControllerProxyHeaders(req: IncomingMessage, host: string, protocol: string, clientIp: string) {
  setProxyHeaders(req, host, protocol, clientIp)
  req.headers[FIREWALL_CLIENT_IP_HEADER] = clientIp
}
