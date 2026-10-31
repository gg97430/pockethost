/**
 * Private migration identity, independent from dashboard ownership.
 *
 * Records live in the existing locked web_app_settings collection. The namespace is never serialized by any public
 * route. A malformed active grant fails closed: callers must not silently sync the dashboard owner's password.
 */
export type NativeAdminIdentity = {
  id: string
  email: string
  tokenKey: string
  passwordHash: string
  preserveExistingNativeIdentity?: true
}
const ID = /^[a-z0-9]{15}$/
const unavailable = () => new InternalServerError('Identité native préservée indisponible ; synchronisation refusée.')

export const migratedNativeAdmin = (instance: core.Record, owner: core.Record): NativeAdminIdentity | null => {
  if (!ID.test(instance.id) || instance.getString('uid') !== owner.id)
    throw new ForbiddenError('Propriétaire invalide.')
  const keyName = `migration:native-admin:${instance.id}`
  const rows = $app.findRecordsByFilter('web_app_settings', 'key = {:key}', '', 2, 0, { key: keyName })
  if (!rows.length) return null
  if (rows.length !== 1) throw unavailable()
  let payload: any
  try {
    const key = $os.getenv('WEB_APPS_MASTER_KEY')
    if (!/^[\x21-\x7e]{32}$/.test(key || '')) throw new Error('Key unavailable')
    payload = JSON.parse(toString($security.decrypt(rows[0]!.getString('valueEncrypted'), key)))
  } catch {
    throw unavailable()
  }
  if (!payload || !ID.test(payload.ownerId || '')) throw unavailable()
  // An explicit subsequent ownership change restores the usual owner sync.
  if (payload.ownerId !== owner.id) return null
  if (
    !ID.test(payload.nativeId || '') ||
    typeof payload.email !== 'string' ||
    payload.email.length > 254 ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(payload.email) ||
    typeof payload.tokenKey !== 'string' ||
    payload.tokenKey.length < 20 ||
    payload.tokenKey.length > 200 ||
    typeof payload.passwordHash !== 'string' ||
    !/^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$/.test(payload.passwordHash)
  )
    throw unavailable()
  return {
    id: payload.nativeId,
    email: payload.email,
    tokenKey: payload.tokenKey,
    passwordHash: payload.passwordHash,
    preserveExistingNativeIdentity: true,
  }
}
