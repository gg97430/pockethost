/** Public database addresses are stable across physical host migrations. No URL, path or wildcard is accepted. */
export type InstanceHostnameFields = {
  id?: string
  subdomain: string
  cname?: string
  publicHostname?: string
  legacyHostnames?: unknown
}
const HOSTNAME = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/
export const normalizeInstanceHostname = (value: unknown): string | undefined => {
  if (typeof value !== 'string') return
  const host = value.trim().toLowerCase().replace(/\.$/, '')
  return HOSTNAME.test(host) ? host : undefined
}
/** Generated address stays stable; a user-managed custom domain remains an independent override. */
export const instanceGeneratedHostname = (instance: InstanceHostnameFields, apex: string) =>
  instance.publicHostname || `${instance.subdomain}.${apex}`
export const instancePublicHostname = (
  instance: InstanceHostnameFields,
  apex: string,
  legacy: 'public' | 'generated' | 'canonical' = 'public'
) => {
  if (instance.publicHostname) return instance.cname || instance.publicHostname
  if (legacy === 'generated') return `${instance.subdomain}.${apex}`
  if (legacy === 'canonical') return instance.cname || `${instance.id}.${apex}`
  return instance.cname || `${instance.subdomain}.${apex}`
}
export const normalizeInstanceRequestHost = (value: unknown) => {
  if (typeof value !== 'string') return
  const host = value.replace(/:\d{1,5}$/, '')
  return normalizeInstanceHostname(host)
}
export const instanceRoutingHostnames = (instance: InstanceHostnameFields, apex: string): string[] => {
  const legacy = Array.isArray(instance.legacyHostnames) ? instance.legacyHostnames : []
  return [
    ...new Set(
      [
        instance.cname,
        instance.publicHostname,
        ...legacy,
        `${instance.subdomain}.${apex}`,
        ...(instance.id ? [`${instance.id}.${apex}`] : []),
      ]
        .map(normalizeInstanceHostname)
        .filter((host): host is string => !!host)
    ),
  ]
}
export const normalizeAssignedInstanceHostnames = (publicHostname: unknown, legacyHostnames: unknown) => {
  if (publicHostname !== undefined && publicHostname !== null && typeof publicHostname !== 'string')
    throw Error('Adresse publique invalide.')
  const primary = publicHostname ? normalizeInstanceHostname(publicHostname) : ''
  if (publicHostname && !primary) throw Error('Adresse publique invalide.')
  const supplied = legacyHostnames === undefined || legacyHostnames === null ? [] : legacyHostnames
  if (!Array.isArray(supplied) || supplied.length > 16) throw Error('Seize anciens noms d’hôte au maximum.')
  const legacy = supplied.map((value) => {
    const host = normalizeInstanceHostname(value)
    if (!host) throw Error('Ancien nom d’hôte invalide.')
    return host
  })
  return { publicHostname: primary || '', legacyHostnames: [...new Set(legacy)].sort() }
}
