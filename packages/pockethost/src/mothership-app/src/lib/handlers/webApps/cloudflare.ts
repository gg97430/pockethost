import { decryptWebValue, encryptWebValue, requirePlatformAdmin, webInput, webJson } from './api'
import { assertWebDomainDnsAccess, webHttpsPreflight } from './domains'
import { webRateLimit } from './gitSource'

const key = 'cloudflare'
const setting = (name: string, store: any = $app): core.Record | undefined =>
  store.findRecordsByFilter('web_app_settings', 'key = {:key}', '', 1, 0, { key: name })[0]
const savedToken = () => {
  const row = setting(key)
  return row ? `${decryptWebValue(row.getString('valueEncrypted')).token || ''}` : ''
}
const credential = () => savedToken() || $os.getenv('MOTHERSHIP_CLOUDFLARE_API_TOKEN') || ''
const status = () => ({
  configured: !!credential(),
  source: savedToken() ? 'saved' : credential() ? 'server' : 'none',
  https: webHttpsPreflight(),
})
export const HandleWebCloudflareGet = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  return webJson(e, status())
}
export const HandleWebCloudflarePut = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const token = `${webInput(e).token || ''}`.trim()
  if (!/^[A-Za-z0-9_-]{20,512}$/.test(token))
    throw new BadRequestError('Saisissez un jeton API Cloudflare valide, avec Zone:Read et DNS:Edit.')
  const row = setting(key) || new Record($app.findCollectionByNameOrId('web_app_settings'))
  row.set('key', key)
  row.set('valueEncrypted', encryptWebValue({ token }))
  $app.save(row)
  return webJson(e, status())
}
const transport = (action: string, values: any) => {
  const token = credential()
  if (!token)
    throw new BadRequestError('Connectez Cloudflare avec un jeton Zone:Read et DNS:Edit limité aux zones souhaitées.')
  const file = `${$app.dataDir()}/.cloudflare-${$security.randomString(24)}.json`
  try {
    $os.writeFile(file, JSON.stringify({ action, token, ...values }), 0o600 as any)
    const helper =
      $os.getenv('WEB_APPS_CLOUDFLARE_HELPER') ||
      ($os.getenv('WEB_APPS_DOMAIN_HELPER') || 'packages/pockethost/scripts/web_app_domains.py').replace(
        /[^/]+$/,
        'web_cloudflare.py'
      )
    const result = JSON.parse(toString($os.cmd('python3', helper, file).combinedOutput()))
    if (result.error) throw new BadRequestError(result.error)
    if (!result.result) throw new Error('Missing result')
    return result.result
  } catch (error) {
    if (error instanceof BadRequestError) throw error
    throw new BadRequestError('Cloudflare est indisponible. Relisez les DNS avant de réessayer.')
  } finally {
    try {
      $os.remove(file)
    } catch {}
  }
}
const target = (e: core.RequestEvent) => {
  requirePlatformAdmin(e) // Global Cloudflare credentials never delegated to project members.
  const { row, app } = assertWebDomainDnsAccess(e)
  return {
    row,
    app,
    values: {
      hostname: row.getString('host'),
      publicIp: $os.getenv('WEB_APPS_PUBLIC_IP') || '',
      ownershipValue: `pockethost-verification=${row.getString('ownershipToken')}`,
    },
  }
}
const contextHash = (values: any) => $security.sha256(JSON.stringify(values) + credential())
export const HandleWebCloudflarePreview = (e: core.RequestEvent) => {
  const { row, app, values } = target(e)
  webRateLimit(app.id, 'cloudflare_preview', 6, 'Trop de lectures Cloudflare. Réessayez dans une minute.')
  const plan = transport('preview', values)
  const preview = new Record($app.findCollectionByNameOrId('web_app_settings'))
  const id = $security.randomString(32),
    expiresAt = Date.now() + 10 * 60 * 1000
  preview.set('key', `cloudflare-plan:${id}`)
  preview.set(
    'valueEncrypted',
    encryptWebValue({
      actor: e.auth!.id,
      app: app.id,
      domain: row.id,
      expiresAt,
      context: contextHash(values),
      revision: plan.revision,
      state: 'pending',
    })
  )
  $app.save(preview)
  // Keep only recent previews. Applied states retain an encrypted, non-secret audit summary.
  for (const old of $app.findRecordsByFilter(
    'web_app_settings',
    'key ~ "cloudflare-plan:" && created < {:before}',
    '',
    50,
    0,
    { before: new Date(Date.now() - 86400000 * 7).toISOString().replace('T', ' ') }
  ))
    $app.delete(old)
  return webJson(e, {
    previewId: id,
    expiresAt,
    hostname: plan.hostname,
    zone: plan.zone,
    current: plan.current,
    changes: plan.changes,
  })
}
export const HandleWebCloudflareApply = (e: core.RequestEvent) => {
  const { row, app, values } = target(e),
    input = webInput(e)
  if (input.confirmHostname !== values.hostname || !/^[A-Za-z0-9]{32}$/.test(`${input.previewId || ''}`))
    throw new BadRequestError('Confirmez le nom exact du domaine affiché dans l’aperçu.')
  webRateLimit(app.id, 'cloudflare_apply', 3, 'Trop de modifications Cloudflare. Réessayez dans une minute.')
  const context = contextHash(values)
  let revision = '',
    planRowId = ''
  $app.runInTransaction((tx) => {
    const pending = setting(`cloudflare-plan:${input.previewId}`, tx)
    if (!pending) throw new BadRequestError('Aperçu introuvable. Préparez un nouvel aperçu.')
    const plan = decryptWebValue(pending.getString('valueEncrypted'))
    if (
      plan.state !== 'pending' ||
      plan.actor !== e.auth!.id ||
      plan.app !== app.id ||
      plan.domain !== row.id ||
      plan.expiresAt < Date.now() ||
      plan.context !== context
    )
      throw new BadRequestError('Aperçu expiré, déjà utilisé ou configuration modifiée. Préparez un nouvel aperçu.')
    revision = plan.revision
    planRowId = pending.id
    pending.set('valueEncrypted', encryptWebValue({ ...plan, state: 'applying' }))
    tx.save(pending)
  })
  let result: any, failure: unknown
  try {
    result = transport('apply', { ...values, revision })
  } catch (error) {
    failure = error
  }
  const audit = $app.findRecordById('web_app_settings', planRowId),
    plan = decryptWebValue(audit.getString('valueEncrypted'))
  audit.set(
    'valueEncrypted',
    encryptWebValue({
      ...plan,
      state: failure ? 'failed' : 'applied',
      hostname: values.hostname,
      finishedAt: new Date().toISOString(),
      changes: result?.changes || 0,
    })
  )
  $app.save(audit)
  if (failure) throw failure
  return webJson(e, {
    ...result,
    message:
      'DNS enregistrés en mode DNS uniquement. La propagation peut prendre quelques minutes ; lancez ensuite Vérifier DNS et HTTPS.',
  })
}
