import { requirePlatformAdmin, webGithub, webJson } from './api'
import { githubConnectionConfigured, githubWebhookUrl, webGithubConnection } from './githubConnections'
/** URL comparable : schéma et hôte en minuscules, port par défaut retiré, sans requête, fragment ni / final. */
export const normalizeWebhookUrl = (value: string) => {
  const match = /^([a-z][a-z0-9+.-]*):\/\/([^/?#]+)([^?#]*)/i.exec(`${value || ''}`.trim())
  if (!match) return null
  const scheme = match[1].toLowerCase(),
    port = scheme === 'https' ? ':443' : scheme === 'http' ? ':80' : ''
  let host = match[2].toLowerCase()
  if (port && host.endsWith(port)) host = host.slice(0, -port.length)
  return { origin: `${scheme}://${host}`, path: match[3].replace(/\/+$/, '') || '/' }
}
/**
 * Décalage entre l'URL configurée sur GitHub et celle de la connexion. Un chemin différent est toujours signalé (le
 * routage se fait par le chemin). Un hôte différent (alias, URL saisie à la main avant l'assistant) ne l'est pas si
 * cette connexion a reçu une livraison depuis moins de 24 h : l'alias atteint bien ce serveur.
 */
export const githubWebhookUrlMismatch = (configuredUrl: string, expectedUrl: string, lastReceivedAt = '') => {
  if (!configuredUrl) return false
  const configured = normalizeWebhookUrl(configuredUrl),
    expected = normalizeWebhookUrl(expectedUrl)
  if (!configured || !expected || configured.path !== expected.path) return true
  if (configured.origin === expected.origin) return false
  const last = Date.parse(`${lastReceivedAt || ''}`.replace(' ', 'T'))
  return !(Number.isFinite(last) && Date.now() - last < 24 * 3600000)
}
/**
 * GET /api/web-apps/github/health?connectionId= — plateforme. Sans paramètre : connexion principale. Consultable pour
 * une connexion désactivée. `webhook.urlMismatch` : l'URL configurée sur GitHub (`/app/hook/config`) diffère de celle
 * de cette connexion, cause la plus probable de pushs perdus avec plusieurs Apps.
 */
export const HandleWebGithubHealth = (e: core.RequestEvent) => {
  requirePlatformAdmin(e)
  const connection = webGithubConnection(e.request!.url!.query().get('connectionId') || 'default', $app, {
      allowDisabled: true,
      allowMissing: true,
    }),
    id = connection.id,
    configured = githubConnectionConfigured(connection.value)
  let value: any = { configured, ok: false }
  try {
    value = { ...webGithub('health', {}, id, { allowDisabled: true }), configured }
  } catch {
    value.error = 'La connexion GitHub ne peut pas être vérifiée. Vérifiez l’installation, la clé et les autorisations.'
  }
  value.ok = value.ok === true && configured
  if (!configured && !value.error) value.error = 'Terminez la connexion GitHub et la configuration du webhook.'
  const url = githubWebhookUrl(id)
  let configuredUrl = ''
  if (configured)
    try {
      configuredUrl = `${webGithub('hook-config', {}, id, { allowDisabled: true }).url || ''}`
    } catch {}
  // Dernière livraison de CETTE connexion (les livraisons « Dépôt Git » ont connectionId '').
  let last: core.Record | undefined
  try {
    last = $app.findRecordsByFilter('web_webhook_deliveries', 'connectionId = {:id}', '-created', 1, 0, { id })[0]
  } catch {}
  const lastReceivedAt = last?.getString('lastReceivedAt') || last?.getString('created') || '',
    urlMismatch = githubWebhookUrlMismatch(configuredUrl, url, lastReceivedAt)
  return webJson(e, {
    ...value,
    connectionId: id,
    label: connection.meta.label,
    disabled: connection.meta.disabled,
    checkedAt: new Date().toISOString(),
    webhook: {
      url,
      configuredUrl,
      urlMismatch,
      lastReceivedAt,
      lastStatus: last?.getString('status') || '',
    },
  })
}
