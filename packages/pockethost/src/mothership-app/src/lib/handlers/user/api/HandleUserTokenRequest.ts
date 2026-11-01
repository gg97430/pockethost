import { migratedNativeAdmin } from '../../../security/migratedNativeAdmin'

export const HandleUserTokenRequest = (e: core.RequestEvent) => {
  if (!e.hasSuperuserAuth()) throw new ForbiddenError('Accès technique requis.')
  const id = e.request.pathValue('id')

  // log({ id })

  if (!id) {
    throw new BadRequestError(`L'identifiant utilisateur est obligatoire.`)
  }

  const rec = $app.findRecordById('users', id)
  const instanceId = e.request?.url?.query().get('instanceId') || ''
  e.response.header().set('Cache-Control', 'no-store')
  if (instanceId) {
    if (!/^[a-z0-9]{15}$/.test(instanceId)) throw new BadRequestError('Instance invalide.')
    const instance = $app.findRecordById('instances', instanceId)
    if (instance.getString('uid') !== rec.id) throw new ForbiddenError('Propriétaire invalide.')
    const native = migratedNativeAdmin(instance, rec)
    if (native) return e.json(200, native)
  }
  const tokenKey = rec.getString('tokenKey')
  const passwordHash = rec.getString('password:hash')
  const email = rec.getString(`email`)
  // log({ email, passwordHash, tokenKey })

  return e.json(200, { ...(instanceId ? { id: rec.id } : {}), email, passwordHash, tokenKey })
}
