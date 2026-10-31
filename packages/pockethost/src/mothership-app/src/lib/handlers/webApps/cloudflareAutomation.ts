import { isWebPlatformAdmin } from './access'
import { decryptWebValue, encryptWebValue } from './api'
import { verifyAndQueueWebDomain } from './domains'

const PREFIX = 'cloudflare-domain:',
  PENDING = 'cloudflare-pending:'
export const cloudflareSetting = (key: string, store: any = $app): core.Record | undefined =>
  store.findRecordsByFilter('web_app_settings', 'key={:key}', '', 1, 0, { key })[0]
export const cloudflareCredential = () => {
  const row = cloudflareSetting('cloudflare')
  return (
    (row ? `${decryptWebValue(row.getString('valueEncrypted')).token || ''}` : '') ||
    $os.getenv('MOTHERSHIP_CLOUDFLARE_API_TOKEN') ||
    ''
  )
}
export const cloudflareTransport = (action: string, values: any) => {
  const token = cloudflareCredential()
  if (!token) throw new BadRequestError('Connectez Cloudflare avec un jeton Zone:Read et DNS:Edit.')
  const file = `${$app.dataDir()}/.cloudflare-${$security.randomString(24)}.json`
  try {
    $os.writeFile(file, JSON.stringify({ action, token, ...values }), 0o600 as any)
    const helper =
      $os.getenv('WEB_APPS_CLOUDFLARE_HELPER') ||
      ($os.getenv('WEB_APPS_DOMAIN_HELPER') || 'packages/pockethost/scripts/web_app_domains.py').replace(
        /[^/]+$/,
        'web_cloudflare.py'
      )
    const response = JSON.parse(toString($os.cmd('python3', helper, file).combinedOutput()))
    if (response.error) throw new BadRequestError(response.error)
    if (!response.result) throw new Error('Missing helper result')
    return response.result
  } catch (error) {
    if (error instanceof BadRequestError) throw error
    throw new BadRequestError('Cloudflare est indisponible. Relisez les DNS avant de réessayer.')
  } finally {
    try {
      $os.remove(file)
    } catch {}
  }
}
const stateOf = (domainId: string, store: any = $app): any => {
  const row = cloudflareSetting(PREFIX + domainId, store)
  return row ? decryptWebValue(row.getString('valueEncrypted')) : null
}
const writeState = (state: any, store: any = $app, pending = false) => {
  const row =
    cloudflareSetting(PREFIX + state.domainId, store) || new Record(store.findCollectionByNameOrId('web_app_settings'))
  row.set('key', PREFIX + state.domainId)
  row.set('valueEncrypted', encryptWebValue(state))
  store.save(row)
  const marker = cloudflareSetting(PENDING + state.domainId, store)
  if (pending) {
    const next = marker || new Record(store.findCollectionByNameOrId('web_app_settings'))
    next.set('key', PENDING + state.domainId)
    next.set('valueEncrypted', encryptWebValue({ domainId: state.domainId }))
    store.save(next)
  } else if (marker) store.delete(marker)
}
export const cloudflareDomainValues = (domain: core.Record) => ({
  hostname: domain.getString('host'),
  publicIp: $os.getenv('WEB_APPS_PUBLIC_IP') || '',
  ownershipValue: 'pockethost-verification=' + domain.getString('ownershipToken'),
})
export const cloudflareCertificateReady = (domain: core.Record) =>
  domain.getString('status') === 'active' && Date.parse(domain.getString('certificateExpiresAt')) > Date.now() + 60000
export const webCloudflareDomainView = (domain: core.Record) => {
  try {
    const state = stateOf(domain.id)
    if (!state || state.appId !== domain.getString('appId') || state.hostname !== domain.getString('host'))
      return undefined
    return { enabled: state.enabled === true, status: state.status, lastError: `${state.lastError || ''}` }
  } catch {
    return undefined
  }
}
export const assertCloudflareIdle = (domainId: string, store: any = $app) => {
  if ((stateOf(domainId, store)?.leaseUntil || 0) > Date.now())
    throw new BadRequestError('La configuration Cloudflare est en cours. Réessayez dans un instant.')
}
export const beginCloudflareConfiguration = (
  domain: core.Record,
  actorId: string,
  enabled: boolean,
  expectedProxied: boolean,
  store: any
) => {
  assertCloudflareIdle(domain.id, store)
  const leaseToken = $security.randomString(32)
  writeState(
    {
      version: 1,
      appId: domain.getString('appId'),
      domainId: domain.id,
      hostname: domain.getString('host'),
      actorId,
      enabled,
      expectedProxied,
      status: 'pending_validation',
      attempts: 0,
      certificateAttempts: 0,
      nextAttemptAt: 0,
      lastError: '',
      leaseToken,
      leaseUntil: Date.now() + 180000,
    },
    store,
    true
  )
  return leaseToken
}
const context = (state: any, store: any = $app) => {
  const actor = store.findRecordById('users', state.actorId),
    domain = store.findRecordById('web_app_domains', state.domainId),
    app = store.findRecordById('web_apps', state.appId)
  if (
    !isWebPlatformAdmin(actor) ||
    actor.getString('suspension') ||
    app.getString('deletionId') ||
    app.getString('suspension') ||
    domain.getString('appId') !== app.id ||
    domain.getString('host') !== state.hostname
  )
    throw new BadRequestError(
      'La configuration a changé ou son autorisation a été retirée. Préparez un nouvel aperçu Cloudflare.'
    )
  return { actor, domain, app }
}
export const finishCloudflareConfiguration = (domainId: string, leaseToken: string, result: any, error = '') => {
  $app.runInTransaction((tx) => {
    const state = stateOf(domainId, tx)
    if (!state || state.leaseToken !== leaseToken) return
    if (error) {
      writeState({ ...state, status: 'error', lastError: error, leaseUntil: 0, leaseToken: '' }, tx)
      return
    }
    const { domain } = context(state, tx)
    if (
      typeof result.zoneId !== 'string' ||
      !result.zoneId ||
      typeof result.recordId !== 'string' ||
      !result.recordId ||
      typeof result.proxied !== 'boolean'
    )
      throw new BadRequestError('La configuration DNS distante doit être relue avant de continuer.')
    const ready = cloudflareCertificateReady(domain)
    const status =
      ready && result.proxied && state.enabled ? 'active' : ready && !state.enabled ? 'disabled' : 'pending_validation'
    writeState(
      {
        ...state,
        status,
        zoneId: result.zoneId,
        recordId: result.recordId,
        lastError: '',
        leaseUntil: 0,
        leaseToken: '',
        nextAttemptAt: 0,
      },
      tx,
      !['active', 'disabled'].includes(status)
    )
  })
}
export const forgetCloudflareConfiguration = (domainId: string, store: any = $app) => {
  for (const prefix of [PREFIX, PENDING]) {
    const row = cloudflareSetting(prefix + domainId, store)
    if (row) store.delete(row)
  }
}
/** A proxied public IP proves nothing about the origin: require the provider A plus public/provider TXT. */
export const verifyCloudflareOrigin = (domain: core.Record) => {
  const result = cloudflareTransport('verify', cloudflareDomainValues(domain)),
    state = stateOf(domain.id)
  if (
    result.verified !== true ||
    (state?.recordId && (state.recordId !== result.recordId || state.zoneId !== result.zoneId))
  )
    throw new BadRequestError('Les DNS Cloudflare ont changé. Préparez un nouvel aperçu.')
  return result
}

/** Durable, bounded automation: survives navigation, retries propagation, never re-applies a DNS plan. */
export const HandleWebCloudflareMaintenance = () => {
  if ($os.getenv('WEB_APPS_ENABLED') !== 'true') return
  const markers = $app.findRecordsByFilter('web_app_settings', 'key ~ {:prefix}', 'updated,id', 100, 0, {
    prefix: PENDING,
  })
  let processed = 0
  for (const marker of markers) {
    if (processed >= 2) break
    const domainId = marker.getString('key').slice(PENDING.length)
    if (!/^[a-z0-9]{15}$/.test(domainId)) continue
    let state: any
    $app.runInTransaction((tx) => {
      const current = stateOf(domainId, tx)
      if (!current) {
        const orphan = cloudflareSetting(PENDING + domainId, tx)
        if (orphan) tx.delete(orphan)
        return
      }
      if (current.leaseUntil > Date.now() || current.nextAttemptAt > Date.now()) return
      state = { ...current, leaseToken: $security.randomString(32), leaseUntil: Date.now() + 180000 }
      writeState(state, tx, true)
    })
    if (!state) continue
    processed++
    let next = { ...state },
      pending = true,
      permanentFailure = false
    const authorized = () => {
      try {
        return context(state)
      } catch {
        permanentFailure = true
        throw new BadRequestError(
          'La configuration a changé ou son autorisation a été retirée. Préparez un nouvel aperçu Cloudflare.'
        )
      }
    }
    try {
      const { domain, app, actor } = authorized()
      // An interrupted apply is recovered by a read, never by replaying the DNS write.
      if (!next.zoneId || !next.recordId) {
        const proof = cloudflareTransport('verify', cloudflareDomainValues(domain))
        if (proof.verified !== true || proof.proxied !== state.expectedProxied || !proof.zoneId || !proof.recordId)
          throw new BadRequestError('Relisez et confirmez les DNS Cloudflare avant de continuer.')
        next.zoneId = proof.zoneId
        next.recordId = proof.recordId
      }
      if (cloudflareCertificateReady(domain)) {
        if (state.enabled) {
          authorized() // Revalidate ownership and authorization immediately before the external write.
          const activated = cloudflareTransport('activate', {
            ...cloudflareDomainValues(domain),
            zoneId: next.zoneId,
            recordId: next.recordId,
          })
          if (activated.proxied !== true) throw new BadRequestError('Le proxy Cloudflare n’est pas encore confirmé.')
        }
        next.status = state.enabled ? 'active' : 'disabled'
        next.lastError = ''
        pending = false
      } else {
        const jobs = $app.findRecordsByFilter(
          'web_deployments',
          'appId={:app} && action="domain" && (status="queued" || status="running")',
          '',
          1,
          0,
          { app: app.id }
        )
        if (!jobs.length) {
          if ((state.certificateAttempts || 0) >= 3) {
            permanentFailure = true
            throw new BadRequestError(
              'La validation HTTPS a échoué après 3 demandes automatiques. Vérifiez le domaine et préparez un nouvel aperçu Cloudflare.'
            )
          }
          if (!app.getString('activeDeployment'))
            throw new BadRequestError('Publiez le site pour terminer la validation HTTPS.')
          verifyAndQueueWebDomain(domain, app, actor.id)
          next.certificateAttempts = (state.certificateAttempts || 0) + 1
        }
        next.status = 'pending_https'
        next.lastError = ''
        next.nextAttemptAt = Date.now() + 15000
      }
    } catch (error) {
      next.attempts = (state.attempts || 0) + 1
      next.status = 'error'
      next.lastError =
        error instanceof BadRequestError
          ? `${error.message}`.slice(0, 500)
          : 'La validation automatique a échoué. Préparez un nouvel aperçu si le problème persiste.'
      next.nextAttemptAt = Date.now() + Math.min(300000, 60000 * 2 ** Math.min(next.attempts - 1, 3))
      pending = !permanentFailure && next.attempts < 8
    }
    $app.runInTransaction((tx) => {
      const current = stateOf(domainId, tx)
      if (!current || current.leaseToken !== state.leaseToken) return
      writeState({ ...next, leaseUntil: 0, leaseToken: '' }, tx, pending)
    })
  }
}
