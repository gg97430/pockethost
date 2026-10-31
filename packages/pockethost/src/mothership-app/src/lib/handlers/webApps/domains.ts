import { PLATFORM_CONTROL_HOSTS } from '../../../../../common/platformDashboardHosts'
import { instanceOwnsHostname } from '../instance/hostnames'
import {
  assertWebQueueCapacity,
  queueWebDeployment,
  requireWebAppPermission,
  requireWebPermission,
  webInput,
  webJson,
  webRoot,
} from './api'
import { webRateLimit } from './gitSource'

const COLLECTION = 'web_app_domains'
/** Message unique : ni le détenteur d'un nom (plateforme, instance, autre équipe), ni son existence ne se déduisent. */
const UNAVAILABLE = 'Ce domaine n’est pas disponible.'
/** Vérifications DNS par minute et par application : chacune bloque une VM du pool de hooks jusqu'à ~18 s. */
const VERIFY_RATE_MAX = 6
const normalize = (value: unknown) => {
  const host = `${value || ''}`.trim().replace(/\.$/, '').toLowerCase()
  if (!/^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(host))
    throw new BadRequestError('Utilisez un nom de domaine complet, sans protocole, port ni chemin.')
  return host
}
const appId = (e: core.RequestEvent) => e.request!.pathValue('id')
/**
 * Seule une ligne dont la propriété est prouvée (TXT vérifié ou alias attribué par la plateforme) réserve un nom. Une
 * ligne pending_dns ne bloque ni une autre équipe ni une instance PocketBase : le premier qui prouve la propriété gagne
 * (index unique partiel, migration 1793271000). Les lignes pending_dns expirent après 7 jours (maintenance).
 */
const CLAIMED = 'status != "pending_dns"'
const rows = (id: string) => $app.findRecordsByFilter(COLLECTION, 'appId = {:id}', 'host', 100, 0, { id })
const publicIp = () => $os.getenv('WEB_APPS_PUBLIC_IP') || ''
const platformAlias = (host: string) => {
  const apex = $os.getenv('APEX_DOMAIN') || 'monappli.re',
    label = host.endsWith('.' + apex) ? host.slice(0, -(apex.length + 1)) : ''
  const protectedNames = [
    'app',
    'app2',
    'app3',
    'www',
    'api',
    'admin',
    'ftp',
    'sftp',
    'smtp',
    'mail',
    'mothership',
    'edge',
    'daemon',
    'acme',
    'static',
    'assets',
    'files',
  ]
  return (
    !!label &&
    !label.includes('.') &&
    !label.startsWith('web-') &&
    !protectedNames.includes(label) &&
    !/^app[0-9]*$/.test(label) &&
    host !== $os.getenv('APP_HOST') &&
    ($os.getenv('WEB_APPS_PLATFORM_ALIASES') || '')
      .split(',')
      .map((x) => x.trim().toLowerCase())
      .includes(host)
  )
}
export const webHttpsPreflight = () => {
  try {
    const worker = JSON.parse(toString($os.readFile(`${webRoot()}/worker-status.json`)))
    if (worker.httpsReady === true) return { ready: true, error: '' }
    return {
      ready: false,
      error:
        worker.httpsReady === false
          ? 'Le serveur HTTPS est incomplet : configurez le contact des certificats et vérifiez Certbot avant de continuer.'
          : 'Le service HTTPS doit être mis à jour pour vérifier sa configuration.',
    }
  } catch {
    return {
      ready: false,
      error: 'Le service de certificats HTTPS est indisponible. Vérifiez le moteur d’hébergement.',
    }
  }
}
const serialize = (row: core.Record) => ({
  ownershipRequired: !platformAlias(row.getString('host')),
  id: row.id,
  hostname: row.getString('host'),
  status: row.getString('status'),
  ownershipName: `_pockethost.${row.getString('host')}`,
  ownershipValue: `pockethost-verification=${row.getString('ownershipToken')}`,
  dnsTarget: publicIp(),
  lastError: row.getString('lastError'),
  certificateExpiresAt: row.getString('certificateExpiresAt'),
  created: row.getString('created'),
})
const findDomain = (e: core.RequestEvent) => {
  const id = e.request!.pathValue('domainId')
  if (!/^[a-z0-9]{15}$/.test(id)) throw new BadRequestError('Domaine invalide.')
  const row = $app.findRecordById(COLLECTION, id)
  if (row.getString('appId') !== appId(e)) throw new ForbiddenError('Accès refusé.')
  return row
}
/** Reuse normal app authorization, ownership checks and protected-name conflicts before any DNS write. */
export const assertWebDomainDnsAccess = (e: core.RequestEvent) => {
  const { app } = requireWebAppPermission(e, 'domains.manage')
  const row = findDomain(e)
  assertAliasAllowed(e, row.getString('host'))
  assertFree(row.getString('host'), app!.id, row.id)
  return { row, app: app! }
}
/** Used by PocketBase mutations as well: web and database domains cannot claim each other. */
export const assertNoWebDomainConflict = (hostname: string) => {
  if ($os.getenv('WEB_APPS_ENABLED') !== 'true' || !hostname) return
  try {
    $app.findCollectionByNameOrId(COLLECTION)
  } catch {
    return
  }
  const matches = $app.findRecordsByFilter(COLLECTION, `host = {:host} && ${CLAIMED}`, '', 1, 0, {
    host: hostname.trim().replace(/\.$/, '').toLowerCase(),
  })
  if (matches.length) throw new BadRequestError('Ce domaine est réservé à une application web.')
}
/** Conflits revérifiés à la création ET à la vérification (une ligne en attente ne réserve plus rien). */
const assertFree = (host: string, appId: string, exceptId = '') => {
  if (PLATFORM_CONTROL_HOSTS.includes(host)) throw new BadRequestError(UNAVAILABLE)
  const apex = $os.getenv('APEX_DOMAIN') || 'monappli.re'
  if ((host === apex || host.endsWith(`.${apex}`)) && !platformAlias(host)) throw new BadRequestError(UNAVAILABLE)
  if (instanceOwnsHostname(host)) throw new BadRequestError(UNAVAILABLE)
  let domainCollection: core.Collection | null = null
  try {
    domainCollection = $app.findCollectionByNameOrId('domains')
  } catch {}
  if (domainCollection && $app.findRecordsByFilter('domains', 'domain = {:host}', '', 1, 0, { host }).length)
    throw new BadRequestError(UNAVAILABLE)
  if (
    $app.findRecordsByFilter(
      COLLECTION,
      `host = {:host} && id != {:except} && (appId = {:appId} || ${CLAIMED})`,
      '',
      1,
      0,
      { host, appId, except: exceptId }
    ).length
  )
    throw new BadRequestError(UNAVAILABLE)
}
/** Un alias de la plateforme n'exige aucune preuve DNS : seule la plateforme l'attribue (création et vérification). */
const assertAliasAllowed = (e: core.RequestEvent, host: string) => {
  if (platformAlias(host)) requireWebPermission(e, { appId: appId(e) }, 'platform.settings')
}
export const getVerifiedWebDomains = (id: string) =>
  rows(id)
    .filter((row) => ['verified', 'pending_certificate', 'active'].includes(row.getString('status')))
    .map((row) => ({ id: row.id, host: row.getString('host'), certificate: row.getString('status') === 'active' }))
export const getWebDomainRouteSnapshot = getVerifiedWebDomains
export const getAllWebDomainRoutesSnapshot = () => {
  try {
    return $app
      .findRecordsByFilter(
        COLLECTION,
        'status = "verified" || status = "pending_certificate" || status = "active"',
        'host',
        1000,
        0
      )
      .map((row) => ({
        id: row.id,
        appId: row.getString('appId'),
        host: row.getString('host'),
        certificate: row.getString('status') === 'active',
      }))
  } catch {
    return []
  }
}
export const HandleWebDomainsList = (e: core.RequestEvent) => {
  requireWebAppPermission(e, 'app.read')
  return webJson(e, { items: rows(appId(e)).map(serialize) })
}
export const HandleWebDomainCreate = (e: core.RequestEvent) => {
  requireWebAppPermission(e, 'domains.manage')
  if (rows(appId(e)).length >= 20) throw new BadRequestError('Limite de 20 domaines personnalisés par application.')
  const host = normalize(webInput(e).hostname)
  assertAliasAllowed(e, host)
  assertFree(host, appId(e))
  const row = new Record($app.findCollectionByNameOrId(COLLECTION))
  row.set('appId', appId(e))
  row.set('host', host)
  row.set('status', 'pending_dns')
  row.set('ownershipToken', $security.randomString(40))
  $app.save(row)
  return webJson(e, { domain: serialize(row) }, 201)
}
export const HandleWebDomainVerify = (e: core.RequestEvent) => {
  const { user, app: found } = requireWebAppPermission(e, 'domains.manage'),
    app = found!,
    row = findDomain(e)
  assertAliasAllowed(e, row.getString('host'))
  if (!publicIp()) throw new BadRequestError('Adresse publique du serveur Applications web non configurée.')
  const https = webHttpsPreflight()
  if (!https.ready) throw new BadRequestError(https.error)
  // Une autre application (ou une instance) a pu prouver la propriété entre-temps.
  assertFree(row.getString('host'), app.id, row.id)
  const pending = $app.findRecordsByFilter(
    'web_deployments',
    'appId = {:id} && action = "domain" && (status = "queued" || status = "running")',
    '',
    1,
    0,
    { id: app.id }
  )
  if (pending.length) throw new BadRequestError('Une vérification de domaine est déjà en cours pour cette application.')
  // Avant le statut pending_certificate : un job refusé ici laisserait un domaine vérifié sans certificat.
  assertWebQueueCapacity(app, 'domain')
  webRateLimit(
    app.id,
    'web_domain_verify_rate',
    VERIFY_RATE_MAX,
    'Trop de vérifications DNS pour cette application. Réessayez dans une minute.'
  )
  const file = `${$app.dataDir()}/.web-domain-${$security.randomString(24)}.json`
  try {
    $os.writeFile(
      file,
      JSON.stringify({
        hostname: row.getString('host'),
        publicIp: publicIp(),
        ownershipToken: row.getString('ownershipToken'),
      }),
      0o600 as any
    )
    const helper = $os.getenv('WEB_APPS_DOMAIN_HELPER') || 'packages/pockethost/scripts/web_app_domains.py'
    const result = JSON.parse(toString($os.cmd('python3', helper, file).combinedOutput()))
    if (!result.verified) {
      row.set('lastError', result.error || 'Vérification DNS non réussie.')
      $app.save(row)
      throw new BadRequestError(row.getString('lastError'))
    }
    // A renewal keeps the currently valid certificate and public route active.
    if (!(row.getString('status') === 'active' && Date.parse(row.getString('certificateExpiresAt')) > Date.now()))
      row.set('status', 'pending_certificate')
    row.set('lastError', '')
    try {
      $app.save(row)
    } catch {
      throw new BadRequestError(UNAVAILABLE)
    }
    const job = queueWebDeployment(app, 'domain', user.id, { domainId: row.id, domain: row.getString('host') })
    return webJson(e, { domain: serialize(row), deploymentId: job.id }, 202)
  } catch (error) {
    if (error instanceof BadRequestError || error instanceof TooManyRequestsError) throw error
    throw new BadRequestError('Vérification DNS indisponible. Réessayez après propagation.')
  } finally {
    try {
      $os.remove(file)
    } catch {}
  }
}
export const HandleWebDomainDelete = (e: core.RequestEvent) => {
  const { user, app: found } = requireWebAppPermission(e, 'domains.manage'),
    app = found!,
    row = findDomain(e)
  const pending = $app.findRecordsByFilter(
    'web_deployments',
    'appId = {:id} && (status = "queued" || status = "running")',
    '',
    1,
    0,
    { id: app.id }
  )
  if (pending.length) throw new BadRequestError('Attendez la fin du déploiement avant de supprimer ce domaine.')
  const wasRouted = ['verified', 'pending_certificate', 'active'].includes(row.getString('status'))
  $app.delete(row)
  if (wasRouted) queueWebDeployment(app, 'domains-sync', user.id)
  return webJson(e, { deleted: true })
}

/** Native collection requests must obey the same domain reservations as the dashboard APIs. */
const guardNativeInstanceDomain = (record: core.Record, creating: boolean) => {
  if ($os.getenv('WEB_APPS_ENABLED') !== 'true') return
  const previous = record.original()
  const subdomain = record.getString('subdomain')
  if ((creating || subdomain !== previous.getString('subdomain')) && /^web-/i.test(subdomain))
    throw new BadRequestError('Le préfixe web- est réservé aux applications web.')
  if (creating || subdomain !== previous.getString('subdomain'))
    assertNoWebDomainConflict(`${subdomain}.${$os.getenv('APEX_DOMAIN') || 'monappli.re'}`)
  const cname = record.getString('cname')
  if (cname && (creating || cname !== previous.getString('cname'))) {
    const host = cname.trim().replace(/\.$/, '').toLowerCase()
    const apex = $os.getenv('APEX_DOMAIN') || 'monappli.re'
    if (host.startsWith('web-') && host.endsWith(`.${apex}`))
      throw new BadRequestError('Ce domaine est réservé aux applications web.')
    assertNoWebDomainConflict(host)
  }
}
export const GuardWebInstanceDomainCreate = (e: core.RecordRequestEvent) => {
  guardNativeInstanceDomain(e.record, true)
  return e.next()
}
export const GuardWebInstanceDomainUpdate = (e: core.RecordRequestEvent) => {
  guardNativeInstanceDomain(e.record, false)
  return e.next()
}
