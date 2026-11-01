import { decryptWebValue, encryptWebValue, requirePlatformAdmin, webInput, webJson } from './api'
import {
  assertCloudflareIdle,
  beginCloudflareConfiguration,
  cloudflareCertificateReady,
  cloudflareCredential,
  cloudflareDomainValues,
  cloudflareSetting,
  finishCloudflareConfiguration,
  cloudflareTransport as transport,
} from './cloudflareAutomation'
import { assertWebDomainDnsAccess, webHttpsPreflight } from './domains'
import { webRateLimit } from './gitSource'
export { HandleWebCloudflareMaintenance } from './cloudflareAutomation'

const key = 'cloudflare'
const setting = cloudflareSetting
const savedToken = () => {
  const row = setting(key)
  return row ? `${decryptWebValue(row.getString('valueEncrypted')).token || ''}` : ''
}
const credential = cloudflareCredential
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
const target = (e: core.RequestEvent) => {
  requirePlatformAdmin(e) // Global Cloudflare credentials never delegated to project members.
  const { row, app } = assertWebDomainDnsAccess(e)
  return {
    row,
    app,
    values: cloudflareDomainValues(row),
  }
}
const contextHash = (values: any) => $security.sha256(JSON.stringify(values) + credential())
export const HandleWebCloudflarePreview = (e: core.RequestEvent) => {
  const { row, app, values } = target(e)
  webRateLimit(app.id, 'cloudflare_preview', 6, 'Trop de lectures Cloudflare. Réessayez dans une minute.')
  const input = webInput(e),
    proxyEnabled = input.proxyEnabled === undefined ? true : input.proxyEnabled
  if (typeof proxyEnabled !== 'boolean') throw new BadRequestError('Le choix du proxy Cloudflare doit être explicite.')
  assertCloudflareIdle(row.id)
  const proxied = proxyEnabled && cloudflareCertificateReady(row)
  const plan = transport('preview', { ...values, proxied })
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
      context: contextHash({ ...values, proxied }),
      proxyEnabled,
      proxied,
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
    proxyEnabled,
    proxyDeferred: proxyEnabled && !proxied,
  })
}
export const HandleWebCloudflareApply = (e: core.RequestEvent) => {
  const { row, app, values } = target(e),
    input = webInput(e)
  if (input.confirmHostname !== values.hostname || !/^[A-Za-z0-9]{32}$/.test(`${input.previewId || ''}`))
    throw new BadRequestError('Confirmez le nom exact du domaine affiché dans l’aperçu.')
  webRateLimit(app.id, 'cloudflare_apply', 3, 'Trop de modifications Cloudflare. Réessayez dans une minute.')
  let revision = '',
    planRowId = '',
    leaseToken = '',
    proxied = false
  $app.runInTransaction((tx) => {
    const pending = setting(`cloudflare-plan:${input.previewId}`, tx)
    if (!pending) throw new BadRequestError('Aperçu introuvable. Préparez un nouvel aperçu.')
    const plan = decryptWebValue(pending.getString('valueEncrypted'))
    const wanted = plan.proxyEnabled === true
    proxied = wanted && cloudflareCertificateReady(row)
    const context = contextHash({ ...values, proxied })
    if (
      plan.state !== 'pending' ||
      plan.actor !== e.auth!.id ||
      plan.app !== app.id ||
      plan.domain !== row.id ||
      plan.expiresAt < Date.now() ||
      plan.context !== context
    )
      throw new BadRequestError('Aperçu expiré, déjà utilisé ou configuration modifiée. Préparez un nouvel aperçu.')
    leaseToken = beginCloudflareConfiguration(row, e.auth!.id, wanted, proxied, tx)
    revision = plan.revision
    planRowId = pending.id
    pending.set('valueEncrypted', encryptWebValue({ ...plan, state: 'applying' }))
    tx.save(pending)
  })
  let result: any, failure: unknown
  try {
    result = transport('apply', { ...values, revision, proxied })
    finishCloudflareConfiguration(row.id, leaseToken, result)
  } catch (error) {
    failure = error
    finishCloudflareConfiguration(
      row.id,
      leaseToken,
      null,
      'La configuration DNS doit être relue. Préparez un nouvel aperçu Cloudflare.'
    )
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
    message: proxied
      ? 'DNS enregistrés et proxy Cloudflare activé.'
      : plan.proxyEnabled
        ? 'DNS enregistrés. La validation DNS et HTTPS se poursuit automatiquement, puis le proxy Cloudflare sera activé.'
        : 'DNS enregistrés sans proxy. La validation DNS et HTTPS se poursuit automatiquement.',
  })
}
