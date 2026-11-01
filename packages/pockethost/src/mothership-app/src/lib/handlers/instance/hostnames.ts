import {
  instanceRoutingHostnames,
  normalizeAssignedInstanceHostnames,
  normalizeInstanceHostname,
} from '../../../../../common/instanceHostnames'
import { PLATFORM_CONTROL_HOSTS } from '../../../../../common/platformDashboardHosts'

const apex = () => $os.getenv('APEX_DOMAIN') || 'pockethost.lvh.me'
const assigned = (record: core.Record) =>
  normalizeAssignedInstanceHostnames(
    record.getString('publicHostname'),
    JSON.parse(record.getString('legacyHostnames') || '[]')
  )
export const instanceHostnameView = (record: core.Record) => ({
  id: record.id,
  subdomain: record.getString('subdomain'),
  cname: record.getString('cname'),
  ...assigned(record),
})
const eachRecord = (store: any, collection: string, visit: (record: core.Record) => boolean) => {
  for (let offset = 0; ; offset += 500) {
    const rows = store.findRecordsByFilter(collection, 'id != ""', 'id', 500, offset) as core.Record[]
    for (const record of rows) if (visit(record)) return true
    if (rows.length < 500) return false
  }
}
/** The two assigned fields are writable only by current platform operators, including through the native API. */
export const canAssignInstanceHostnames = (auth: core.Record | null | undefined) =>
  !!auth &&
  (auth.collection().name === '_superusers' ||
    (auth.collection().name === 'users' && auth.getBool('superAdmin') && !auth.getString('suspension')))
export const assertInstanceHostnameOperator = (auth: core.Record | null | undefined) => {
  if (!canAssignInstanceHostnames(auth))
    throw new ForbiddenError('Les adresses de migration sont réservées à la plateforme.')
}
export const GuardInstanceHostnamesRequest = (e: any) => {
  let next: ReturnType<typeof assigned>
  try {
    next = assigned(e.record)
  } catch {
    throw new BadRequestError('Adresses de migration invalides.')
  }
  let before = { publicHostname: '', legacyHostnames: [] as string[] }
  if (!e.record.isNew()) before = assigned((e.app || $app).findRecordById('instances', e.record.id))
  if (JSON.stringify(before) !== JSON.stringify(next)) assertInstanceHostnameOperator(e.auth)
  return e.next()
}
/** Exact ownership lookup is shared with website domain claims; an old database URL must never become a site. */
export const instanceOwnsHostname = (hostname: string, exceptId = '', store: any = $app) => {
  const host = normalizeInstanceHostname(hostname)
  if (!host) return false
  return eachRecord(
    store,
    'instances',
    (record) => record.id !== exceptId && instanceRoutingHostnames(instanceHostnameView(record), apex()).includes(host)
  )
}
const protectedHost = (host: string) => {
  const currentApex = apex(),
    ownHost = ($os.getenv('APP_HOST') || '').toLowerCase(),
    appUrlHost = /^https?:\/\/([^/:]+)/i.exec($os.getenv('APP_URL') || '')?.[1]?.toLowerCase()
  return [
    currentApex,
    ownHost,
    appUrlHost,
    `app.${currentApex}`,
    `${$os.getenv('MOTHERSHIP_NAME') || 'pockethost-central'}.${currentApex}`,
    ...PLATFORM_CONTROL_HOSTS,
  ].includes(host)
}
const collectionExists = (store: any, collection: string) => {
  try {
    store.findCollectionByNameOrId(collection)
    return true
  } catch {
    return false
  }
}
const websiteOwnsHostname = (host: string, store: any) => {
  if (
    collectionExists(store, 'web_app_domains') &&
    store.findRecordsByFilter('web_app_domains', 'host={:host} && status!="pending_dns"', '', 1, 0, { host }).length
  )
    return true
  return (
    collectionExists(store, 'web_apps') &&
    eachRecord(store, 'web_apps', (record) => {
      const config = JSON.parse(record.getString('config') || '{}')
      return normalizeInstanceHostname(config.generatedDomain) === host
    })
  )
}
/** Model guard covers custom platform routes, direct collection API calls and internal writes alike. */
export const ValidateInstanceHostnames = (e: any) => {
  const record = e.record,
    store = e.app || $app
  let values: ReturnType<typeof assigned>
  try {
    values = assigned(record)
  } catch {
    throw new BadRequestError('Adresses de migration invalides (FQDN uniquement, seize alias maximum).')
  }
  record.set('publicHostname', values.publicHostname)
  record.set('legacyHostnames', values.legacyHostnames)
  const hosts = instanceRoutingHostnames({ ...instanceHostnameView(record), ...values }, apex())
  if (!record.isNew()) {
    const before = store.findRecordById('instances', record.id)
    if (JSON.stringify(hosts) === JSON.stringify(instanceRoutingHostnames(instanceHostnameView(before), apex())))
      return e.next()
  }
  for (const host of hosts) {
    if (protectedHost(host) || instanceOwnsHostname(host, record.id, store) || websiteOwnsHostname(host, store))
      throw new BadRequestError('Ce nom d’hôte est déjà réservé.')
  }
  return e.next()
}
