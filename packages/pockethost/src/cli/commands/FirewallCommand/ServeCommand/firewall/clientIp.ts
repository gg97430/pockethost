import type { IncomingMessage } from 'node:http'
import { BlockList, isIP } from 'node:net'
import { toProxyCidrString } from './rateLimiterPure'

/**
 * Plages publiées par Cloudflare : https://www.cloudflare.com/ips-v4 et https://www.cloudflare.com/ips-v6 (relevées le
 * 2026-10-30). Une plage absente n'ouvre rien : la requête est simplement attribuée au pair TCP (Cloudflare).
 */
export const CLOUDFLARE_IP_RANGES = [
  '173.245.48.0/20',
  '103.21.244.0/22',
  '103.22.200.0/22',
  '103.31.4.0/22',
  '141.101.64.0/18',
  '108.162.192.0/18',
  '190.93.240.0/20',
  '188.114.96.0/20',
  '197.234.240.0/22',
  '198.41.128.0/17',
  '162.158.0.0/15',
  '104.16.0.0/13',
  '104.24.0.0/14',
  '172.64.0.0/13',
  '131.0.72.0/22',
  '2400:cb00::/32',
  '2606:4700::/32',
  '2803:f800::/32',
  '2405:b500::/32',
  '2405:8100::/32',
  '2a06:98c0::/29',
  '2c0f:f248::/32',
]

/** `::ffff:1.2.3.4` et `1.2.3.4` sont la même adresse : limiteurs et journaux n'en voient qu'une. */
const unmapped = (ip: string) => ip.replace(/^::ffff:(?=\d+\.\d+\.\d+\.\d+$)/i, '')

export const validClientIp = (value: string | string[] | undefined) => {
  if (typeof value !== 'string') return undefined
  const ip = unmapped(value.trim())
  return isIP(ip) && !ip.includes('%') ? ip : undefined
}

/** Pair TCP : la seule adresse qu'un client ne choisit pas. */
export const peerIp = (req: IncomingMessage) => unmapped(req.socket?.remoteAddress || '') || 'unknown'

const addRange = (list: BlockList, entry: string) => {
  const [address = '', prefix = ''] = toProxyCidrString(entry).split('/')
  const family = isIP(address)
  if (!family || !/^\d+$/.test(prefix)) return false
  try {
    list.addSubnet(address, Number(prefix), family === 4 ? 'ipv4' : 'ipv6')
    return true
  } catch {
    return false
  }
}

const contains = (list: BlockList, value: string) => {
  const ip = validClientIp(value)
  return !!ip && list.check(ip, isIP(ip) === 4 ? 'ipv4' : 'ipv6')
}

/** Le contrôleur exclut la boucle locale de ses limites : aucun proxy, même de confiance, ne peut s'en réclamer. */
const LOOPBACK = new BlockList()
LOOPBACK.addSubnet('127.0.0.0', 8, 'ipv4')
LOOPBACK.addAddress('::1', 'ipv6')

const declaredClientIp = (req: IncomingMessage) => {
  const ip = validClientIp(req.headers['cf-connecting-ip'])
  return ip && !contains(LOOPBACK, ip) ? ip : undefined
}

export type ClientIpResolver = {
  /** IP du client : pair TCP, ou `CF-Connecting-IP` valide si ce pair est un proxy de confiance. */
  clientIp: (req: IncomingMessage) => string
  isTrustedProxy: (req: IncomingMessage) => boolean
  /** Adresse listée dans `PH_USER_PROXY_IPS` (pair direct ou client vu à travers Cloudflare). */
  isUserProxyIp: (ip: string) => boolean
  /** `X-Forwarded-Proto` ne compte que venant d'un proxy de confiance (Cloudflare Flexible parle HTTP à l'origine). */
  forwardedProto: (req: IncomingMessage) => 'https' | 'http'
}

/**
 * Seul point de calcul de l'IP client du pare-feu. L'origine reste joignable en direct : aucun en-tête n'est lu tant
 * que le pair n'est pas de confiance. `True-Client-IP`, `X-Forwarded-For` et `X-Real-Ip` ne sont jamais lus.
 */
export const createClientIpResolver = (
  userProxies: string[] = [],
  onInvalid?: (entry: string) => void
): ClientIpResolver => {
  const trusted = new BlockList()
  const users = new BlockList()
  for (const range of CLOUDFLARE_IP_RANGES) addRange(trusted, range)
  for (const raw of userProxies) {
    const entry = raw.trim()
    if (!entry) continue
    if (!addRange(trusted, entry) || !addRange(users, entry)) onInvalid?.(entry)
  }

  const isTrustedProxy = (req: IncomingMessage) => contains(trusted, peerIp(req))

  return {
    clientIp: (req) => (isTrustedProxy(req) && declaredClientIp(req)) || peerIp(req),
    isTrustedProxy,
    isUserProxyIp: (ip) => contains(users, ip),
    forwardedProto: (req) =>
      (req.socket as any)?.encrypted || (isTrustedProxy(req) && req.headers['x-forwarded-proto'] === 'https')
        ? 'https'
        : 'http',
  }
}
