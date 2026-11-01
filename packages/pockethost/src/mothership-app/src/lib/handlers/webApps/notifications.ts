import {
  WEB_NOTIFICATION_EVENTS as EVENTS,
  WEB_ALERT_BRAND,
  WEB_NOTIFICATION_DEFAULT_EVENTS,
  WEB_NOTIFICATION_LABELS,
  WEB_PLATFORM_DEFAULT_EVENTS,
  WEB_PLATFORM_EVENTS,
  WEB_WEBHOOK_FORMATS,
} from '../../../../../common/webNotifications'
import { normalizeDiscordWebhook, normalizeSlackWebhook } from '../instance/api/instanceMonitoring'
import {
  decryptWebValue,
  encryptWebValue,
  isWebPlatformAdmin,
  requirePlatformAdmin,
  requireWebAppPermission,
  WEB_MEMBERS,
  webAppSettingActive,
  webDirectLoopback,
  webEnabled,
  webInput,
  webJson,
} from './api'
import {
  deploymentEmailApplication,
  deploymentFailureAttachment,
  deploymentFailureDetails,
} from './deploymentFailureEmail'
import { webPlatformRecoveryApi } from './recovery'
const SETTINGS = 'web_notification_settings',
  DELIVERIES = 'web_notification_deliveries',
  OUTBOX = 'web_alert_events',
  PLATFORM = 'web_platform_notification_settings'
/** Conservé pour les imports existants ; la liste et les libellés vivent dans common/webNotifications. */
export const WEB_NOTIFICATION_EVENTS: string[] = [...EVENTS]
const PLATFORM_EVENTS: string[] = [...WEB_PLATFORM_EVENTS]
const label = (event: string) =>
  (WEB_NOTIFICATION_LABELS as Record<string, { title: string; sentence: string; tab: string }>)[event] ||
  WEB_NOTIFICATION_LABELS.test
const HOUR = 3600000
/** Alertes automatiques et résumés : 12 par canal et par heure. Les tests manuels ont leurs propres plafonds. */
const AUTO_PER_HOUR = 12,
  TESTS_PER_CHANNEL_HOUR = 10,
  TESTS_PER_USER_HOUR = 20
/** Analyse des sources au plus toutes les 15 s (les réclamations du worker ont lieu toutes les 3 s). */
const SCAN_EVERY = 15000
/**
 * Première occurrence de l'heure toujours transmise, même plafond atteint (une rafale d'autres alertes ne la masque
 * pas).
 */
const CRITICAL = ['deployment_failed', 'backup_failed', 'certificate_failed', 'health_degraded', 'runtime_stopped']
/**
 * Capacité de l'hôte : jamais transmise aux canaux des clients. Réservée aux applications opérées par la plateforme
 * (legacy, ou projet dont tous les membres sont superAdmins), et sans aucune mesure. Le canal plateforme la reçoit.
 */
const PLATFORM_ONLY = ['disk_low', 'disk_recovered']
/** Copies optionnelles des échecs d'applications vers le canal plateforme. */
const COPIED = ['health_degraded', 'deployment_failed', 'deployment_succeeded', 'backup_failed']
const DEPLOYMENT_FAILURES = ['deployment_failed', 'app_deployment_failed']
const DEPLOYMENT_SUCCESSES = ['deployment_succeeded', 'app_deployment_succeeded']
const DEPLOYMENT_RESULTS = [...DEPLOYMENT_FAILURES, ...DEPLOYMENT_SUCCESSES]
const RATE_LIMITED = 'Notification regroupée par la limite de fréquence.'
const WORKER_DOWN_AFTER = 180000
const stamp = (time = Date.now()) => new Date(time).toISOString().replace('T', ' ')
const id = (value: unknown) => {
  if (typeof value !== 'string' || !/^[a-z0-9]{15}$/.test(value)) throw new BadRequestError('Notification invalide.')
  return value
}
const defaults = () => ({
  webhook: { enabled: false, url: '', secret: '', format: 'generic' },
  email: { enabled: false, recipients: [] as string[], projectAdmins: false },
  events: [...WEB_NOTIFICATION_DEFAULT_EVENTS],
  cooldownSeconds: 300,
  copyAppEvents: false,
})
const platformDefaults = () => ({ ...defaults(), events: [...WEB_PLATFORM_DEFAULT_EVENTS] })
const merge = (base: any, stored: any) => ({
  ...base,
  ...stored,
  webhook: { ...base.webhook, ...(stored?.webhook || {}) },
  email: { ...base.email, ...(stored?.email || {}) },
})
const find = (appId: string, store: any = $app): core.Record | null => {
  try {
    return store.findFirstRecordByFilter(SETTINGS, 'appId={:id}', { id: appId })
  } catch {
    return null
  }
}
const config = (row: core.Record | null, platform = false) => {
  const base = platform ? platformDefaults() : defaults()
  const stored = row?.getString('configEncrypted') ? decryptWebValue(row.getString('configEncrypted')) : null
  return stored ? merge(base, stored) : base
}
const platformCollection = (store: any = $app) => {
  try {
    store.findCollectionByNameOrId(PLATFORM)
    return true
  } catch {
    return false
  }
}
/** Ligne unique du canal plateforme, créée désactivée au premier besoin. */
const platformRow = (store: any = $app): core.Record => {
  try {
    return store.findFirstRecordByFilter(PLATFORM, 'key="platform"')
  } catch {
    const row = new Record(store.findCollectionByNameOrId(PLATFORM))
    row.set('key', 'platform')
    row.set('enabled', false)
    row.set('revision', $security.randomString(24))
    store.save(row)
    return row
  }
}
const projectOf = (appId: string, store: any = $app) => {
  try {
    return store.findFirstRecordByFilter('web_app_environments', 'appId={:id}', { id: appId }).getString('projectId')
  } catch {
    return ''
  }
}
const userOf = (id: string, store: any): core.Record | null => {
  try {
    return store.findRecordById('users', id)
  } catch {
    return null
  }
}
const verifiedEmail = (user: core.Record | null) =>
  user && user.getBool('verified') && !user.getString('suspension') ? user.getString('email').toLowerCase() : ''
/**
 * Destinataires autorisés : adresses vérifiées des membres actuels du projet, et des superAdmins. Le dashboard n'est
 * jamais un relais vers des adresses tierces. Pour une application de projet, l'enregistrement n'accepte en plus que
 * l'adresse de son auteur (aucune énumération des comptes de la plateforme) ; l'envoi accepte aussi les superAdmins.
 */
const allowedRecipients = (appId: string, store: any, author?: core.Record) => {
  const emails = new Set<string>(),
    projectId = projectOf(appId, store)
  if (projectId)
    for (const member of store.findRecordsByFilter(WEB_MEMBERS, 'project={:project}', '', 1000, 0, {
      project: projectId,
    }))
      emails.add(verifiedEmail(userOf(member.getString('user'), store)))
  if (author) emails.add(verifiedEmail(author))
  if (!author || !projectId)
    for (const admin of store.findRecordsByFilter('users', 'superAdmin=true && verified=true', '', 500, 0))
      if (isWebPlatformAdmin(admin)) emails.add(verifiedEmail(admin))
  emails.delete('')
  return emails
}
/**
 * Destinataires implicites : owners et administrateurs actuels du projet de l'application (comptes vérifiés, non
 * suspendus, non désabonnés ; 10 au plus). Résolus à chaque envoi : un membre retiré ne reçoit plus rien.
 */
const projectAdmins = (appId: string, store: any = $app): string[] => {
  const projectId = projectOf(appId, store)
  if (!projectId) return []
  return store
    .findRecordsByFilter(WEB_MEMBERS, 'project={:p} && (role="owner" || role="admin")', 'created', 10, 0, {
      p: projectId,
    })
    .map((member: core.Record) => userOf(member.getString('user'), store))
    .filter((user: core.Record | null) => user && !user.getBool('unsubscribe'))
    .map((user: core.Record) => verifiedEmail(user))
    .filter(Boolean)
}
const deliverableRecipients = (appId: string, recipients: string[], store: any = $app) => {
  const allowed = allowedRecipients(appId, store)
  return recipients.filter((address) => allowed.has(address.toLowerCase()))
}
/** Destinataires effectifs d'une application : explicites + owners/admins si demandé, revalidés à l'envoi. */
const recipientsOf = (value: any, appId: string, store: any = $app): string[] => {
  const explicit = [...value.email.recipients].map((address: string) => address.toLowerCase())
  const all = [...new Set([...explicit, ...(value.email.projectAdmins ? projectAdmins(appId, store) : [])])]
  return deliverableRecipients(appId, all, store).slice(0, 15)
}
/** Canal plateforme : adresses saisies par un superAdmin (opérateurs), sans filtre de projet. */
const recipientsFor = (scope: string, value: any, appId: string, store: any = $app): string[] =>
  scope === 'platform' ? [...value.email.recipients] : recipientsOf(value, appId, store)
/** The operator who configured a platform channel must still have platform authority at delivery time. */
const platformSettingActive = (settings: core.Record, store: any = $app) =>
  isWebPlatformAdmin(userOf(settings.getString('actorId'), store))
/** Opérée par la plateforme : legacy (superAdmins seulement) ou projet sans aucun membre client. */
const platformOperated = (appId: string, store: any) => {
  const projectId = projectOf(appId, store)
  if (!projectId) return true
  return store
    .findRecordsByFilter(WEB_MEMBERS, 'project={:project}', '', 1000, 0, { project: projectId })
    .every((member: core.Record) => isWebPlatformAdmin(userOf(member.getString('user'), store)))
}
/** Nom d'application sans lien ni nom de domaine : il est choisi par le client et ne doit pas servir d'hameçon. */
const safeName = (value: unknown) =>
  (typeof value === 'string' ? value : '')
    .replace(/[\r\n\t]/g, ' ')
    .replace(/[a-z][a-z0-9+.-]*:\/\/\S*/gi, ' ')
    .replace(/\S*[a-z0-9-]\.[a-z]{2,}\S*/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 60) || 'Site web'
const smtpAvailable = () => {
  const settings = $app.settings()
  return !!(settings.smtp?.enabled && settings.smtp.host && settings.meta?.senderAddress)
}
const healthState = (appId: string) => {
  try {
    const profile = $app.findRecordsByFilter('web_runtime_profiles', 'appId={:id}', '', 1, 0, { id: appId })[0]
    if (profile && JSON.parse(profile.getString('profile') || '{}').kind === 'worker') return 'not_applicable'
  } catch {}
  try {
    const row = $app.findRecordsByFilter('web_app_health', 'appId={:id}', '', 1, 0, { id: appId })[0]
    return row?.getBool('enabled') ? 'enabled' : 'disabled'
  } catch {
    return 'disabled'
  }
}
/**
 * Les alertes appartiennent à l'application, pas à leur auteur : actives tant que l'application existe dans un projet
 * (legacy : tant que l'auteur reste superAdmin). Toute modification exige notifications.manage à l'écriture.
 */
const settingActive = (actorId: string, appId: string, store: any = $app) => webAppSettingActive(appId, actorId, store)
const channels = (value: any) => {
  const host = /^https:\/\/([^/]+)/.exec(value.webhook.url)?.[1] || ''
  return {
    webhook: {
      enabled: value.webhook.enabled,
      configured: !!value.webhook.url,
      endpointHost: host,
      hasSecret: !!value.webhook.secret,
      format: value.webhook.format || 'generic',
    },
    email: {
      enabled: value.email.enabled,
      configured: !!value.email.recipients.length || !!value.email.projectAdmins,
      recipients: value.email.recipients,
      projectAdmins: !!value.email.projectAdmins,
    },
    events: value.events,
    cooldownSeconds: value.cooldownSeconds,
    smtpAvailable: smtpAvailable(),
  }
}
const response = (row: core.Record | null, appId: string) => {
  const value = config(row),
    view = channels(value),
    projectId = projectOf(appId)
  return {
    revision: row?.getString('revision') || '',
    ...view,
    email: { ...view.email, projectAdminCount: projectAdmins(appId).length },
    inProject: !!projectId,
    platformOperated: platformOperated(appId, $app),
    healthMonitoring: healthState(appId),
    // Auteur legacy rétrogradé : les envois reprennent dès que ses droits reviennent ou qu'un admin ré-enregistre.
    paused: !!row && row.getBool('enabled') && !settingActive(row.getString('actorId'), appId),
    // Lignes désactivées en silence par une ancienne version (canaux actifs mais ligne coupée).
    pausedLegacy: !!row && !row.getBool('enabled') && (value.webhook.enabled || value.email.enabled),
  }
}
const requireWorker = (e: core.RequestEvent) => {
  if (!webEnabled()) throw new NotFoundError()
  const expected = $os.getenv('WEB_APPS_WORKER_SECRET')
  if (expected.length < 32 || !$security.equal(expected, e.request!.header.get('X-Web-Worker') || ''))
    throw new UnauthorizedError()
  if (!webDirectLoopback(e)) throw new ForbiddenError('Accès local requis.')
}
const metadata = (row: core.Record) => ({
  id: row.id,
  channel: row.getString('channel'),
  event: row.getString('event'),
  status: row.getString('status'),
  attempts: row.getInt('attempts'),
  nextAttemptAt: row.getString('nextAttemptAt'),
  created: row.getString('created'),
  sentAt: row.getString('sentAt'),
  httpStatus: row.getInt('httpStatus'),
  lastError: row.getString('lastError'),
  trigger: row.getString('trigger'),
})
const validUrl = (value: unknown) =>
  typeof value === 'string' &&
  value.length <= 2000 &&
  /^https:\/\/(?=.{1,253}(?::443)?(?:\/|$))(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}(?::443)?(?:\/[^\s#]*)?$/.test(
    value
  ) &&
  !/[\r\n\0]/.test(value)
const validEmail = (address: any) =>
  typeof address === 'string' &&
  address.length <= 254 &&
  /^\S+@[^\s@]+\.[^\s@]+$/.test(address) &&
  !/[\r\n\0]/.test(address)
/**
 * Validation commune des canaux (application et plateforme). Retourne le secret généré, affiché une seule fois. Slack
 * et Discord ne vérifient aucune signature : aucun secret n'est exigé ni généré pour eux (un secret déjà enregistré est
 * conservé pour un retour au format générique).
 */
const applyChannels = (
  input: any,
  value: any,
  recipientCheck: (recipients: string[]) => void,
  canEmail: () => boolean
) => {
  let generatedSecret = ''
  for (const channel of ['webhook', 'email'])
    if (input[channel] !== undefined) {
      const body = input[channel]
      if (!body || typeof body !== 'object' || Array.isArray(body) || typeof body.enabled !== 'boolean')
        throw new BadRequestError('Canal invalide.')
      if (body.remove === true) {
        value[channel] = defaults()[channel as 'webhook' | 'email']
        continue
      }
      if (channel === 'webhook') {
        if (
          Object.keys(body).some(
            (key) => !['enabled', 'url', 'secret', 'remove', 'format', 'rotateSecret'].includes(key)
          )
        )
          throw new BadRequestError('Options webhook invalides.')
        if (body.format !== undefined) {
          if (!(WEB_WEBHOOK_FORMATS as readonly string[]).includes(body.format))
            throw new BadRequestError('Format de webhook invalide.')
          value.webhook.format = body.format
        }
        if (body.url !== undefined && body.url !== '') {
          if (!validUrl(body.url))
            throw new BadRequestError('Utilisez une URL HTTPS publique sur le port 443, sans identifiant.')
          value.webhook.url = body.url
        }
        const format = value.webhook.format || 'generic'
        if (value.webhook.url && format !== 'generic')
          try {
            value.webhook.url =
              format === 'slack' ? normalizeSlackWebhook(value.webhook.url) : normalizeDiscordWebhook(value.webhook.url)
          } catch (error) {
            throw new BadRequestError(
              typeof (error as any)?.message === 'string' ? (error as any).message : 'Webhook invalide.'
            )
          }
        if (body.rotateSecret !== undefined && typeof body.rotateSecret !== 'boolean')
          throw new BadRequestError('Options webhook invalides.')
        if (body.secret !== undefined && body.secret !== '') {
          if (
            typeof body.secret !== 'string' ||
            body.secret.length < 16 ||
            body.secret.length > 256 ||
            /[\r\n\0]/.test(body.secret)
          )
            throw new BadRequestError('Secret webhook de 16 à 256 caractères requis.')
          value.webhook.secret = body.secret
        } else if (body.rotateSecret === true) {
          if (!value.webhook.url) throw new BadRequestError('Enregistrez le webhook avant de changer son secret.')
          if (format !== 'generic')
            throw new BadRequestError('Slack et Discord ne vérifient pas de signature : aucun secret à renouveler.')
          value.webhook.secret = generatedSecret = $security.randomString(48)
        } else if (value.webhook.url && !value.webhook.secret && format === 'generic')
          value.webhook.secret = generatedSecret = $security.randomString(48)
        if (body.enabled && !value.webhook.url) throw new BadRequestError('Renseignez le webhook avant de l’activer.')
        value.webhook.enabled = body.enabled
      } else {
        if (Object.keys(body).some((key) => !['enabled', 'recipients', 'remove', 'projectAdmins'].includes(key)))
          throw new BadRequestError('Options email invalides.')
        if (body.recipients !== undefined) {
          if (!Array.isArray(body.recipients) || body.recipients.length > 5 || !body.recipients.every(validEmail))
            throw new BadRequestError('Indiquez au maximum cinq adresses email valides.')
          recipientCheck(body.recipients)
          value.email.recipients = [...new Set(body.recipients as string[])]
        }
        if (body.projectAdmins !== undefined) {
          if (typeof body.projectAdmins !== 'boolean') throw new BadRequestError('Options email invalides.')
          value.email.projectAdmins = body.projectAdmins
        }
        if (body.enabled && (!canEmail() || !smtpAvailable()))
          throw new BadRequestError('Configurez SMTP et au moins un destinataire avant d’activer les emails.')
        value.email.enabled = body.enabled
      }
    }
  if (input.cooldownSeconds !== undefined) {
    if (!Number.isInteger(input.cooldownSeconds) || input.cooldownSeconds < 60 || input.cooldownSeconds > 86400)
      throw new BadRequestError('Le délai de regroupement doit être compris entre 60 et 86 400 secondes.')
    value.cooldownSeconds = input.cooldownSeconds
  }
  return generatedSecret
}
/** Ré-enregistrer ne perd pas d'événements : le point de départ n'est remis à zéro qu'à l'activation. */
const saveSettings = (row: core.Record, old: core.Record | null, actorId: string, value: any, tx: any) => {
  const wasEnabled = !!old?.getBool('enabled'),
    nowEnabled = value.webhook.enabled || value.email.enabled
  row.set('actorId', actorId)
  row.set('revision', $security.randomString(24))
  row.set('configEncrypted', encryptWebValue(value))
  row.set('enabled', nowEnabled)
  if (!old || !row.getString('activatedAt') || (!wasEnabled && nowEnabled)) {
    row.set('activatedAt', stamp())
    row.set('sourceCursors', {})
  }
  row.set('scanAfter', stamp(Date.now() - SCAN_EVERY))
  tx.save(row)
}
/**
 * Changement d'équipe (rattachement hors de la lignée du dernier projet quitté) : les canaux appartenaient à l'équipe
 * précédente. Webhook (URL, secret, format) et destinataires effacés, envois coupés, nouvelle révision (les livraisons
 * en file sont annulées) et curseurs remis à zéro : la nouvelle équipe enregistre ses propres canaux et ne reçoit pas
 * l'historique de l'application.
 */
export const resetWebNotificationChannels = (appId: string, actorId: string, store: any) => {
  const row = find(appId, store)
  if (!row) return false
  const value = config(row),
    base = defaults(),
    configured =
      row.getBool('enabled') || !!value.webhook.url || !!value.email.recipients.length || !!value.email.projectAdmins
  value.webhook = base.webhook
  value.email = base.email
  row.set('actorId', actorId)
  row.set('revision', $security.randomString(24))
  row.set('configEncrypted', encryptWebValue(value))
  row.set('enabled', false)
  row.set('activatedAt', '')
  row.set('sourceCursors', {})
  store.save(row)
  return configured
}
export const HandleWebNotificationsGet = (e: core.RequestEvent) => {
  // La réponse contient les e-mails des destinataires : réservée aux gestionnaires des alertes.
  const app = requireWebAppPermission(e, 'notifications.manage').app!
  return webJson(e, response(find(app.id), app.id))
}
export const HandleWebNotificationsPut = (e: core.RequestEvent) => {
  const { user: actor, app: found } = requireWebAppPermission(e, 'notifications.manage'),
    app = found!,
    input = webInput(e)
  let result: any,
    generatedSecret = ''
  if (Object.keys(input).some((key) => !['revision', 'webhook', 'email', 'events', 'cooldownSeconds'].includes(key)))
    throw new BadRequestError('Options de notification invalides.')
  $app.runInTransaction((tx) => {
    const old = find(app.id, tx),
      value = config(old)
    if (input.revision !== (old?.getString('revision') || ''))
      throw new BadRequestError('Les canaux ont changé. Rechargez leur configuration.')
    generatedSecret = applyChannels(
      input,
      value,
      (recipients) => {
        const allowed = allowedRecipients(app.id, tx, actor)
        if (recipients.some((address: string) => !allowed.has(address.toLowerCase())))
          throw new BadRequestError(
            'Chaque destinataire doit être l’adresse vérifiée d’un membre du projet, ou la vôtre.'
          )
      },
      () => recipientsOf(value, app.id, tx).length > 0
    )
    if (input.events !== undefined) {
      if (!Array.isArray(input.events) || input.events.some((event: any) => !WEB_NOTIFICATION_EVENTS.includes(event)))
        throw new BadRequestError('Types d’alerte invalides.')
      if (input.events.includes('deployment_succeeded') && !value.events.includes('deployment_succeeded'))
        value.deploymentSuccessActivatedAt = stamp()
      value.events = [...new Set(input.events)]
    }
    const row = old || new Record(tx.findCollectionByNameOrId(SETTINGS))
    row.set('appId', app.id)
    saveSettings(row, old, actor.id, value, tx)
    result = row
  })
  // Secret généré : renvoyé une seule fois, jamais journalisé ni relu (GET ne donne que hasSecret).
  return webJson(e, { ...response(result, app.id), ...(generatedSecret ? { generatedSecret } : {}) })
}
const dashboardBase = () =>
  (
    $os.getenv('APP_URL') ||
    `https://${$os.getenv('APP_HOST') || 'app.' + ($os.getenv('APEX_DOMAIN') || 'monappli.re')}`
  )
    .trim()
    .replace(/\/+$/, '')
const dashboardLink = (appId: string, tab: string) =>
  `${dashboardBase()}/applications/${appId}${tab ? '?tab=' + encodeURIComponent(tab) : ''}`
const platformLink = (event: string) =>
  `${dashboardBase()}/applications${event.startsWith('recovery_') ? '/recovery' : ''}`
const enqueue = (
  settings: core.Record,
  app: core.Record | null,
  event: string,
  eventKey: string,
  details: any,
  store: any,
  trigger = 'auto',
  only = '',
  requestedBy = '',
  scope = 'app'
): core.Record[] => {
  const platform = scope === 'platform',
    value = config(settings, platform),
    text = label(event),
    domain = app ? JSON.parse(app.getString('config') || '{}').generatedDomain || '' : '',
    payload = {
      version: 2,
      event,
      occurredAt: new Date().toISOString(),
      title: text.title,
      message: text.sentence,
      application: app
        ? {
            id: app.id,
            name: safeName(app.getString('name')),
            url: domain ? `https://${domain}` : '',
            dashboardUrl: dashboardLink(app.id, text.tab),
          }
        : null,
      ...(app ? {} : { dashboardUrl: platformLink(event) }),
      platform: { host: $os.getenv('APP_HOST') || $os.getenv('APEX_DOMAIN') || '' },
      details,
    }
  const created: core.Record[] = []
  if (trigger === 'auto' && !value.events.includes(event)) return created
  if (!platform && app && PLATFORM_ONLY.includes(event) && !platformOperated(app.id, store)) return created
  const appId = platform ? '' : app!.id
  for (const channel of ['webhook', 'email']) {
    if (only && only !== channel) continue
    const recipients = channel === 'email' ? recipientsFor(scope, value, appId, store) : []
    if (channel === 'webhook' ? !value.webhook.url : !recipients.length) continue
    if (trigger !== 'test' && !value[channel].enabled) continue
    const key = $security.sha256(eventKey),
      fingerprint = $security.sha256(channel === 'webhook' ? value.webhook.url : [...recipients].sort().join(','))
    if (
      store.findRecordsByFilter(
        DELIVERIES,
        (platform ? 'scope="platform"' : 'scope!="platform" && appId={:app}') +
          ' && channel={:channel} && eventKey={:key}',
        '',
        1,
        0,
        { scope: platform ? 'platform' : '', app: appId, channel, key }
      ).length
    )
      continue
    // Capacité de l'hôte : une transition est partagée par toutes les applications ; une même destination ne la
    // reçoit qu'une fois, quel que soit le nombre d'applications qui la signalent.
    if (
      !platform &&
      PLATFORM_ONLY.includes(event) &&
      store.findRecordsByFilter(
        DELIVERIES,
        'event={:event} && eventKey={:key} && fingerprint={:fingerprint}',
        '',
        1,
        0,
        { event, key, fingerprint }
      ).length
    )
      continue
    // Tests et résumés ne sont jamais regroupés ; les tests ne consomment pas le budget des alertes automatiques.
    let rate = false
    // Each deployment result gets its own email. Other emails and all webhooks keep their budgets.
    if (trigger === 'auto' && !(channel === 'email' && DEPLOYMENT_RESULTS.includes(event))) {
      const sent = (filter: string, since: number, limit: number) =>
        store.findRecordsByFilter(
          DELIVERIES,
          `settingsId={:id} && channel={:channel} && created>={:since} && status!="suppressed" && trigger!="test"${filter}`,
          '-created',
          limit,
          0,
          { id: settings.id, channel, event, since: stamp(since) }
        ).length
      const cooled = sent(' && event={:event}', Date.now() - value.cooldownSeconds * 1000, 1) > 0,
        full = sent('', Date.now() - HOUR, AUTO_PER_HOUR) >= AUTO_PER_HOUR
      rate = cooled || (full && !(CRITICAL.includes(event) && !sent(' && event={:event}', Date.now() - HOUR, 1)))
    }
    const row = new Record(store.findCollectionByNameOrId(DELIVERIES))
    for (const [name, item] of Object.entries({
      scope: platform ? 'platform' : '',
      appId,
      settingsId: settings.id,
      settingsRevision: settings.getString('revision'),
      actorId: settings.getString('actorId'),
      channel,
      event,
      eventKey: key,
      fingerprint,
      payload,
      trigger,
      requestedBy,
      rateLimited: rate,
      status: rate ? 'suppressed' : 'pending',
      nextAttemptAt: stamp(),
      lastError: rate ? RATE_LIMITED : '',
    }))
      row.set(name, item)
    store.save(row)
    created.push(row)
  }
  return created
}
/**
 * Événement poussé (sans source à scanner) : runtime arrêté, planification de sauvegarde en échec, tâche suspendue,
 * worker injoignable, échec de reprise… Idempotent par (scope, appId, clé) ; ne lève jamais d'erreur pour ne pas faire
 * échouer la transaction de déploiement, de sauvegarde ou de tâche qui l'émet.
 */
export const emitWebAlert = (
  store: any,
  scope: 'app' | 'platform',
  appId: string,
  event: string,
  key: string,
  details: any = {}
) => {
  try {
    const eventKey = $security.sha256(key)
    if (
      store.findRecordsByFilter(OUTBOX, 'scope={:scope} && appId={:app} && eventKey={:key}', '', 1, 0, {
        scope,
        app: appId,
        key: eventKey,
      }).length
    )
      return false
    const row = new Record(store.findCollectionByNameOrId(OUTBOX))
    row.set('scope', scope)
    row.set('appId', scope === 'platform' ? '' : appId)
    row.set('event', event)
    row.set('eventKey', eventKey)
    row.set('details', details)
    store.save(row)
    return true
  } catch {
    return false
  }
}
/** Résumé d'erreur : une ligne, 160 caractères, sans valeurs d'environnement, jetons ni identifiants d'URL. */
export const webAlertSummary = (text: string, app: core.Record | null, store: any = $app) => {
  let value = `${text || ''}`.split(/\r?\n/).find((line) => line.trim()) || ''
  const secrets: string[] = []
  const collect = (env: any) => {
    for (const entry of Object.values(env || {}) as any[])
      if (typeof entry?.value === 'string' && entry.value.length >= 4) secrets.push(entry.value)
  }
  if (app) {
    try {
      collect(decryptWebValue(app.getString('envEncrypted')))
    } catch {}
    try {
      const published = store.findRecordById('web_deployments', app.getString('activeDeployment'))
      collect(decryptWebValue(published.getString('snapshotEncrypted'))?.env)
    } catch {}
  }
  for (const secret of [...new Set(secrets)].sort((a, b) => b.length - a.length))
    value = value.split(secret).join('[masqué]')
  return value
    .replace(/https?:\/\/[^\s/@]+@/g, 'https://[masqué]@')
    .replace(
      /\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|x-access-token:[^\s@]+)/g,
      '[masqué]'
    )
    .replace(/[A-Za-z0-9+/=_-]{40,}/g, '[masqué]')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 160)
}
/** Valeurs fictives d'exercice, sans aucun secret : les destinataires testent leur routage par type d'alerte. */
const sampleDetails = (event: string): any =>
  (
    ({
      health_degraded: {
        status: 'degraded',
        httpStatus: 503,
        summary: 'Exemple : le contrôle de santé répond HTTP 503.',
      },
      health_recovered: { status: 'healthy', httpStatus: 200, summary: 'Exemple : le site répond à nouveau HTTP 200.' },
      deployment_failed: {
        deploymentId: 'test00000000000',
        stage: 'Construction',
        summary: 'Exemple : la commande de build a échoué (code 1).',
      },
      backup_failed: { backupId: 'test00000000000', summary: 'Exemple : la copie S3 de la sauvegarde a échoué.' },
      runtime_stopped: {
        deploymentId: 'test00000000000',
        reason: 'cache_or_disk',
        summary: 'Exemple : arrêt par la plateforme.',
      },
      task_failed: {
        taskName: 'Exemple',
        status: 'failed',
        exitCode: 1,
        summary: 'Exemple : la tâche a quitté avec le code 1.',
      },
      certificate_expiring: { summary: 'Exemple : un certificat expire dans 10 jours.' },
      certificate_failed: { summary: 'Exemple : le renouvellement du certificat a échoué.' },
      disk_low: { summary: 'Exemple : réserve disque sous 15 Gio.' },
      disk_recovered: { summary: 'Exemple : réserve disque rétablie.' },
    }) as Record<string, any>
  )[event] || { summary: 'Notification de test.' }
const escape = (value: string) =>
  value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!)
const settingsOf = (row: core.Record, store: any = $app) => {
  const platform = row.getString('scope') === 'platform'
  if (platform) return platformCollection(store) ? platformRow(store) : null
  return find(row.getString('appId'), store)
}
const deploymentTargetActive = (row: core.Record, store: any = $app) => {
  if (row.getString('trigger') === 'test' || !DEPLOYMENT_RESULTS.includes(row.getString('event'))) return true
  try {
    const payload = JSON.parse(row.getString('payload') || '{}')
    return !!deploymentEmailApplication(
      row.getString('scope') === 'platform' ? payload.details?.appId : row.getString('appId'),
      store
    )
  } catch {
    return false
  }
}
/**
 * Envoi email d'une livraison (route du worker, test synchrone, chien de garde quand le worker est arrêté). Revalide le
 * canal, la révision et, pour une application, les droits et les destinataires. Idempotent (emailSentAt).
 */
const sendDeliveryEmail = (row: core.Record) => {
  const platform = row.getString('scope') === 'platform',
    settings = settingsOf(row),
    value = config(settings, platform),
    appId = row.getString('appId')
  if (
    row.getString('channel') !== 'email' ||
    !settings ||
    settings.getString('revision') !== row.getString('settingsRevision') ||
    (platform && !platformSettingActive(settings)) ||
    !deploymentTargetActive(row) ||
    (row.getString('trigger') !== 'test' && (!settings.getBool('enabled') || !value.email.enabled)) ||
    (!platform && !settingActive(row.getString('actorId'), appId)) ||
    !smtpAvailable()
  )
    throw new BadRequestError('Le canal email n’est plus disponible.')
  if (row.getString('emailSentAt')) return
  // Revalidé à chaque envoi : un membre retiré du projet ne reçoit plus rien.
  const recipients = recipientsFor(row.getString('scope'), value, appId)
  if (!recipients.length)
    throw new BadRequestError('Aucun destinataire enregistré n’est encore membre vérifié du projet.')
  const payload = JSON.parse(row.getString('payload') || '{}'),
    text = label(payload.event || row.getString('event')),
    settingsMail = $app.settings(),
    host = $os.getenv('APP_HOST') || $os.getenv('APEX_DOMAIN') || '',
    name = payload.application ? safeName(payload.application?.name) : `Plateforme${host ? ' ' + host : ''}`,
    test = row.getString('trigger') === 'test' || payload.details?.drill === true,
    details = payload.details || {},
    attachment = DEPLOYMENT_FAILURES.includes(row.getString('event'))
      ? deploymentFailureAttachment(row, payload, test)
      : null,
    deployment =
      attachment?.details ||
      (DEPLOYMENT_SUCCESSES.includes(row.getString('event')) ? { ...details, stage: 'Publié' } : null),
    detail =
      (attachment && !test ? deployment?.summary || 'Détails indisponibles. Consultez le dashboard.' : '') ||
      (typeof details.summary === 'string' && details.summary) ||
      [details.stage, details.backupId, details.deploymentId, details.taskName].filter(
        (item) => typeof item === 'string' && item
      )[0] ||
      '',
    open = payload.application?.dashboardUrl || payload.dashboardUrl || '',
    manage = platform ? `${dashboardBase()}/applications/recovery` : dashboardLink(appId, 'notifications'),
    lines = [
      text.title + (test ? ' (test)' : ''),
      text.sentence,
      payload.application
        ? `Application : ${name}${payload.application.url ? ` (${payload.application.url})` : ''}`
        : name,
      `Date : ${payload.occurredAt || new Date().toISOString()} UTC`,
      ...(deployment
        ? [
            `Déploiement : ${deployment.deploymentId}`,
            ...(deployment.repository ? [`Dépôt : ${deployment.repository}`] : []),
            ...(deployment.branch ? [`Branche : ${deployment.branch}`] : []),
            ...(deployment.commit ? [`Commit : ${deployment.commit}`] : []),
            ...(deployment.stage ? [`Phase : ${deployment.stage}`] : []),
            ...(deployment.finishedAt ? [`Fin du déploiement : ${deployment.finishedAt} UTC`] : []),
          ]
        : []),
      ...(detail ? [`Détail : ${`${detail}`.replace(/\s+/g, ' ').slice(0, 300)}`] : []),
      ...(attachment ? ['Journal du déploiement en pièce jointe (1 Mio maximum, valeurs sensibles masquées).'] : []),
      ...(open ? [`Ouvrir dans le dashboard : ${open}`] : []),
      `Gérer ces alertes : ${manage}`,
      `— ${WEB_ALERT_BRAND}${host ? ' · ' + host : ''}`,
    ]
  // Détail et nom d'application viennent du locataire : seules nos propres URL deviennent des liens.
  const base = dashboardBase(),
    linkable = (value: string) => value === base || value.startsWith(`${base}/`)
  const color = DEPLOYMENT_SUCCESSES.includes(row.getString('event'))
    ? '#087443'
    : DEPLOYMENT_FAILURES.includes(row.getString('event'))
      ? '#b42318'
      : '#2455d6'
  const html = lines
    .slice(3)
    .map((line) => {
      const match = /^(.*?: )(\S+)$/.exec(line)
      const content =
        match && linkable(match[2]!)
          ? `${escape(match[1]!)}<a style="color:#2455d6" href="${escape(match[2]!)}">${escape(match[2]!)}</a>`
          : escape(line)
      return `<p style="margin:0 0 12px;font-size:14px;line-height:1.6;color:#344054;overflow-wrap:anywhere">${content}</p>`
    })
    .join('')
  let reader: io.ReadSeekCloser | undefined
  try {
    if (attachment) reader = $filesystem.fileFromBytes(attachment.text, attachment.filename).reader.open()
    $app.newMailClient().send(
      new MailerMessage({
        from: { address: settingsMail.meta.senderAddress, name: settingsMail.meta.senderName || WEB_ALERT_BRAND },
        to: recipients.map((address: string) => ({ address })),
        subject: `[${WEB_ALERT_BRAND}] ${test ? 'TEST · ' : ''}${text.title} · ${name}`.replace(/[\r\n]/g, ' '),
        text: lines.join('\n'),
        html: `<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body style="margin:0;background:#edf1f6;font-family:Verdana,Geneva,sans-serif"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 8px"><table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#fff;border:1px solid #dce3ec;border-radius:12px"><tr><td style="background:#162137;padding:24px 28px;color:#fff;border-radius:12px 12px 0 0"><strong style="font-size:23px;letter-spacing:3px">KOLAB</strong><p style="margin:7px 0 0;color:#b9c7dc;font-size:11px;letter-spacing:2px">HÉBERGEMENT</p></td></tr><tr><td style="padding:28px;border-top:4px solid ${color}"><h1 style="margin:0 0 14px;font-size:26px;color:${color}">${escape(text.title)}${test ? ' (test)' : ''}</h1><p style="margin:0 0 14px;font-size:20px;font-weight:700;color:#162137;overflow-wrap:anywhere">${escape(name)}</p><p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#475467">${escape(text.sentence)}</p>${html}${open && linkable(open) ? `<table role="presentation" cellpadding="0" cellspacing="0"><tr><td bgcolor="#2455d6" style="border-radius:6px"><a href="${escape(open)}" style="display:inline-block;padding:14px 20px;color:#fff;text-decoration:none;font-size:14px;font-weight:700">Ouvrir dans Kolab &rarr;</a></td></tr></table>` : ''}</td></tr></table></td></tr></table></body></html>`,
        headers: { 'Message-ID': `<ph-web-${row.id}@${$os.getenv('APEX_DOMAIN') || 'localhost'}>` },
        ...(attachment && reader ? { attachments: { [attachment.filename]: reader } } : {}),
      })
    )
  } catch {
    throw new BadRequestError('Le serveur SMTP a refusé cet envoi.')
  } finally {
    reader?.close()
  }
  row.set('emailSentAt', stamp())
  $app.save(row)
}
/** Envoi synchrone d'un test (application ou plateforme) : résultat réel du canal, attendu 12 s au plus. */
const runTest = (
  e: core.RequestEvent,
  settings: core.Record,
  app: core.Record | null,
  scope: string,
  input: any,
  userId: string
) => {
  let row: core.Record | undefined
  $app.runInTransaction((tx) => {
    // Plafonds propres aux tests (refus explicite, rien n'est mis en file) : par canal et par auteur.
    const since = stamp(Date.now() - HOUR),
      count = (filter: string, limit: number) =>
        tx.findRecordsByFilter(DELIVERIES, filter + ' && trigger="test" && created>={:since}', '', limit, 0, {
          scope: scope === 'platform' ? 'platform' : '',
          app: app?.id || '',
          channel: input.channel,
          user: userId,
          since,
        }).length
    if (count('scope={:scope} && appId={:app} && channel={:channel}', TESTS_PER_CHANNEL_HOUR) >= TESTS_PER_CHANNEL_HOUR)
      throw new TooManyRequestsError('Dix tests par heure et par canal au maximum. Réessayez plus tard.')
    if (count('requestedBy={:user}', TESTS_PER_USER_HOUR) >= TESTS_PER_USER_HOUR)
      throw new TooManyRequestsError('Vingt tests d’alerte par heure et par compte au maximum. Réessayez plus tard.')
    const event = input.event === undefined ? 'test' : input.event
    row = enqueue(
      settings,
      app,
      event,
      'test:' + $security.randomString(32),
      { drill: true, ...sampleDetails(event) },
      tx,
      'test',
      input.channel,
      userId,
      scope
    )[0]
    if (!row) throw new BadRequestError('Ce canal n’a aucun destinataire valide.')
    if (input.channel === 'email') {
      // Envoyé ici même : le worker ne doit pas le réclamer en parallèle.
      row.set('status', 'sending')
      row.set('attempts', 1)
      row.set('leaseAt', stamp())
      tx.save(row)
    }
  })
  let current = $app.findRecordById(DELIVERIES, row!.id)
  if (input.channel === 'email') {
    try {
      sendDeliveryEmail(current)
      current = $app.findRecordById(DELIVERIES, row!.id)
      current.set('status', 'sent')
      current.set('sentAt', stamp())
      current.set('lastError', '')
    } catch (error) {
      current = $app.findRecordById(DELIVERIES, row!.id)
      current.set('status', 'failed')
      // BadRequestError n'est pas une instance d'Error dans Goja : lire son message directement.
      const raw = (error as any)?.message
      current.set('lastError', typeof raw === 'string' && raw.trim() ? raw.slice(0, 300) : 'Envoi SMTP impossible.')
    }
    $app.save(current)
  } else {
    // Webhook : seul le worker l'envoie (épinglage DNS, refus des adresses privées). Le test prouve ce chemin réel.
    const deadline = Date.now() + 12000
    while (['pending', 'sending'].includes(current.getString('status')) && Date.now() < deadline) {
      sleep(300)
      current = $app.findRecordById(DELIVERIES, row!.id)
    }
  }
  const done = ['sent', 'failed', 'suppressed'].includes(current.getString('status'))
  return webJson(e, { result: metadata(current), done }, done ? 200 : 202)
}
const testInput = (e: core.RequestEvent, events: string[]) => {
  const input = webInput(e)
  if (Object.keys(input).some((key) => !['channel', 'event'].includes(key))) throw new BadRequestError('Test invalide.')
  if (input.event !== undefined && input.event !== 'test' && !events.includes(input.event))
    throw new BadRequestError('Type d’alerte invalide.')
  return input
}
export const HandleWebNotificationTest = (e: core.RequestEvent) => {
  const { user, app: found } = requireWebAppPermission(e, 'alerts.test'),
    app = found!,
    input = testInput(e, WEB_NOTIFICATION_EVENTS),
    settings = find(app.id)
  if (!settings || !['webhook', 'email'].includes(input.channel))
    throw new BadRequestError('Enregistrez d’abord le canal à tester.')
  const value = config(settings)
  if (input.channel === 'webhook' ? !value.webhook.url : !recipientsOf(value, app.id).length || !smtpAvailable())
    throw new BadRequestError(
      input.channel === 'email' && value.email.recipients.length
        ? 'Aucun destinataire enregistré n’est encore membre vérifié du projet.'
        : 'Ce canal n’est pas configuré.'
    )
  if (!value[input.channel].enabled) throw new BadRequestError('Activez et enregistrez ce canal avant de l’essayer.')
  if (PLATFORM_ONLY.includes(input.event) && !platformOperated(app.id, $app))
    throw new BadRequestError('Cette alerte est réservée aux applications opérées par la plateforme.')
  return runTest(e, settings, app, 'app', input, user.id)
}
export const HandleWebNotificationHistory = (e: core.RequestEvent) => {
  const app = requireWebAppPermission(e, 'logs.read').app!,
    query = e.request!.url!.query()
  if (query.get('id')) {
    const rows = $app.findRecordsByFilter(DELIVERIES, 'appId={:app} && id={:id} && scope!="platform"', '', 1, 0, {
      app: app.id,
      id: id(query.get('id')),
    })
    return webJson(e, { items: rows.map(metadata), page: 1, hasMore: false })
  }
  const page = Math.max(1, Math.min(100000, Math.floor(Number(query.get('page')) || 1))),
    rows = $app.findRecordsByFilter(
      DELIVERIES,
      'appId={:id} && scope!="platform"',
      '-created,-id',
      21,
      (page - 1) * 20,
      {
        id: app.id,
      }
    )
  return webJson(e, { items: rows.slice(0, 20).map(metadata), page, hasMore: rows.length > 20 })
}
type Cursor = { at: string; id: string }
/** Lecture incrémentale d'une source : (timeField, id) strictement après le curseur, 200 lignes au plus. */
const takeRows = (
  store: any,
  cursors: Record<string, Cursor>,
  start: string,
  collection: string,
  filter: string,
  timeField: string,
  params: Record<string, string> = {}
) => {
  const cursor = cursors[collection] || { at: start, id: '' }
  const rows = store.findRecordsByFilter(
    collection,
    `${filter} && (${timeField}>{:at} || (${timeField}={:at} && id>{:cursor}))`,
    `${timeField},id`,
    200,
    0,
    { ...params, at: cursor.at, cursor: cursor.id }
  ) as core.Record[]
  if (rows.length)
    cursors[collection] = { at: rows[rows.length - 1]!.getString(timeField), id: rows[rows.length - 1]!.id }
  return rows
}
type Host = { low: boolean; changedAt: string } | null
type ScanContext = { host: Host; platform: core.Record | null }
const scan = (settings: core.Record, context: ScanContext, store: any) => {
  // Réglage de l'application : jamais désactivé en silence. Auteur legacy rétrogradé : rien n'est envoyé ni consommé,
  // les événements partent dès que ses droits reviennent ou qu'un administrateur ré-enregistre.
  if (!settingActive(settings.getString('actorId'), settings.getString('appId'), store)) {
    // Repoussé seulement (pas de curseur avancé) : ces lignes ne monopolisent pas le lot de 200 analyses.
    settings.set('scanAfter', stamp())
    store.save(settings)
    return
  }
  const app = store.findRecordById('web_apps', settings.getString('appId')),
    until = stamp()
  const cursors = JSON.parse(settings.getString('sourceCursors') || '{}')
  const take = (collection: string, filter: string, timeField: string) =>
    takeRows(
      store,
      cursors,
      settings.getString('activatedAt') || until,
      collection,
      `appId={:app} && ${filter}`,
      timeField,
      {
        app: app.id,
      }
    )
  const local = config(settings),
    copy = context.platform?.getBool('enabled') ? config(context.platform, true) : null
  const emit = (event: string, key: string, details: any) => {
    enqueue(settings, app, event, key, details, store)
    if (
      copy?.copyAppEvents &&
      COPIED.includes(event) &&
      (event !== 'deployment_succeeded' ||
        details.finishedAt >= (copy.deploymentSuccessActivatedAt || context.platform!.getString('activatedAt'))) &&
      (!['deployment_failed', 'deployment_succeeded'].includes(event) ||
        (details.finishedAt &&
          details.finishedAt >= context.platform!.getString('activatedAt') &&
          deploymentEmailApplication(app.id, store)))
    )
      enqueue(
        context.platform!,
        ['deployment_failed', 'deployment_succeeded'].includes(event) ? app : null,
        'app_' + event,
        'copy:' + app.id + ':' + key,
        { ...details, appId: app.id, appName: safeName(app.getString('name')) },
        store,
        'auto',
        '',
        '',
        'platform'
      )
  }
  for (const row of take('web_app_health_events', 'id!=""', 'created')) {
    if (row.getString('status') === 'degraded')
      emit('health_degraded', 'health:' + row.id, {
        status: 'degraded',
        httpStatus: row.getInt('httpStatus'),
        reason: row.getString('reason'),
      })
    else if (row.getString('status') === 'healthy' && row.getString('fromStatus') === 'degraded')
      emit('health_recovered', 'health:' + row.id, { status: 'healthy', httpStatus: row.getInt('httpStatus') })
  }
  for (const row of take('web_deployments', '(status="failed" || status="ready")', 'finishedAt')) {
    const deploy = ['deploy', 'ci-deploy'].includes(row.getString('action')),
      success = row.getString('status') === 'ready'
    if (
      success &&
      (!deploy ||
        row.getString('finishedAt') < (local.deploymentSuccessActivatedAt || settings.getString('activatedAt')))
    )
      continue
    if (deploy && !deploymentEmailApplication(app.id, store)) continue
    emit(
      success
        ? 'deployment_succeeded'
        : row.getString('action') === 'domain'
          ? 'certificate_failed'
          : 'deployment_failed',
      'deployment:' + row.id,
      {
        ...(deploy ? deploymentFailureDetails(row) : {}),
        deploymentId: row.id,
        status: success ? 'ready' : 'failed',
        action: row.getString('action'),
        stage: success ? 'Publié' : row.getString('stage'),
        summary: success ? 'La nouvelle version est publiée.' : webAlertSummary(row.getString('error'), app, store),
      }
    )
  }
  // Une seule alerte par sauvegarde, quelle que soit l'évolution ultérieure de s3State (rétention, suppression).
  // Les archives créées par la reprise de la plateforme ne concernent pas le client, ni une suppression abandonnée
  // (rétention ou suppression manuelle : canal plateforme, `backup_cleanup_failed`).
  for (const row of take(
    'web_app_backups',
    '(status="failed" || s3State="failed") && status!="deleting" && status!="deleted" && operation!="delete" && trigger!="platform"',
    'updated'
  ))
    emit('backup_failed', 'backup:' + row.id, {
      backupId: row.id,
      trigger: row.getString('trigger'),
      local: row.getString('status') === 'failed',
      s3: row.getString('s3State') === 'failed',
      summary: webAlertSummary(
        row.getString('status') === 'failed' ? row.getString('error') : row.getString('s3Error'),
        app,
        store
      ),
    })
  for (const row of take(OUTBOX, 'scope="app"', 'created'))
    emit(row.getString('event'), 'outbox:' + row.getString('eventKey'), JSON.parse(row.getString('details') || '{}'))
  // Le journal de la tâche n'est jamais transmis (il peut contenir des secrets même masqué).
  for (const row of take('web_task_runs', '(status="failed" || status="timed_out")', 'finishedAt'))
    emit('task_failed', 'task:' + row.id, {
      taskName: row.getString('taskName').slice(0, 80),
      status: row.getString('status'),
      exitCode: row.getInt('exitCode'),
      trigger: row.getString('trigger'),
    })
  for (const row of store.findRecordsByFilter(
    'web_app_domains',
    'appId={:id} && status="active" && certificateExpiresAt!="" && certificateExpiresAt<={:expiry}',
    'certificateExpiresAt',
    100,
    0,
    { id: app.id, expiry: stamp(Date.now() + 30 * 86400000) }
  ))
    emit('certificate_expiring', 'certificate:' + row.id + ':' + row.getString('certificateExpiresAt'), {
      domain: row.getString('host'),
      expiresAt: row.getString('certificateExpiresAt'),
    })
  // État disque de l'hôte (une transition partagée par toutes les applications), sans aucune mesure.
  const host = context.host
  if (host && host.low !== settings.getBool('diskLow')) {
    emit(host.low ? 'disk_low' : 'disk_recovered', 'disk:' + host.low + ':' + host.changedAt, {})
    settings.set('diskLow', host.low)
  }
  digest(settings, app, store)
  settings.set('sourceCursors', cursors)
  settings.set('scanAfter', until)
  store.save(settings)
}
/** Une notification par canal et par heure close résume les alertes regroupées par la limite de fréquence. */
const digest = (settings: core.Record, app: core.Record | null, store: any, scope = 'app') => {
  const end = Math.floor(Date.now() / HOUR) * HOUR,
    start = end - HOUR
  for (const channel of ['webhook', 'email']) {
    const rows = store.findRecordsByFilter(
      DELIVERIES,
      'settingsId={:id} && channel={:channel} && rateLimited=true && created>={:from} && created<{:to}',
      'created',
      500,
      0,
      { id: settings.id, channel, from: stamp(start), to: stamp(end) }
    )
    if (!rows.length) continue
    const events: Record<string, number> = {}
    for (const row of rows) events[row.getString('event')] = (events[row.getString('event')] || 0) + 1
    enqueue(
      settings,
      app,
      'alerts_grouped',
      'digest:' + stamp(start),
      { count: rows.length, events, from: new Date(start).toISOString(), to: new Date(end).toISOString() },
      store,
      'digest',
      channel,
      '',
      scope
    )
  }
}
/** Outbox plateforme → livraisons du canal plateforme (curseurs gardés sur la ligne plateforme). */
const scanPlatformOutbox = (p: core.Record, store: any) => {
  if (!p.getBool('enabled') || !platformSettingActive(p, store)) return false
  const cursors = JSON.parse(p.getString('sourceCursors') || '{}'),
    start = p.getString('activatedAt') || stamp(),
    rows = takeRows(store, cursors, start, OUTBOX, 'scope="platform"', 'created')
  let advanced = rows.length > 0
  const value = config(p, true)
  // Platform copies must also cover applications without an app-level alert configuration.
  if (value.copyAppEvents && value.events.some((event: string) => DEPLOYMENT_RESULTS.includes(event))) {
    const jobs = takeRows(
      store,
      cursors,
      start,
      'web_deployments',
      '(status="failed" || status="ready") && (action="deploy" || action="ci-deploy")',
      'finishedAt'
    )
    advanced ||= jobs.length > 0
    for (const job of jobs) {
      if (
        job.getString('status') === 'ready' &&
        job.getString('finishedAt') < (value.deploymentSuccessActivatedAt || start)
      )
        continue
      const app = deploymentEmailApplication(job.getString('appId'), store)
      if (!app) continue
      enqueue(
        p,
        app,
        job.getString('status') === 'ready' ? 'app_deployment_succeeded' : 'app_deployment_failed',
        `copy:${app.id}:deployment:${job.id}`,
        {
          ...deploymentFailureDetails(job),
          appId: app.id,
          appName: safeName(app.getString('name')),
          status: job.getString('status'),
          stage: job.getString('status') === 'ready' ? 'Publié' : job.getString('stage'),
          summary:
            job.getString('status') === 'ready'
              ? 'La nouvelle version est publiée.'
              : webAlertSummary(job.getString('error'), app, store),
        },
        store,
        'auto',
        '',
        '',
        'platform'
      )
    }
  }
  digest(p, null, store, 'platform')
  // Ligne plateforme réécrite seulement quand l'outbox a avancé (pas une écriture toutes les 3 s).
  if (!advanced) return false
  for (const row of rows)
    enqueue(
      p,
      null,
      row.getString('event'),
      'outbox:' + row.getString('eventKey'),
      JSON.parse(row.getString('details') || '{}'),
      store,
      'auto',
      '',
      '',
      'platform'
    )
  p.set('sourceCursors', cursors)
  p.set('scanAfter', stamp())
  return true
}
/** Battement du worker, état disque de l'hôte et outbox plateforme, dans la transaction de réclamation. */
const scanPlatform = (disk: number | undefined, store: any): ScanContext => {
  if (!platformCollection(store)) return { host: null, platform: null }
  const p = platformRow(store)
  let changed = false
  if (!p.getString('lastClaimAt') || Date.now() - Date.parse(p.getString('lastClaimAt')) > 30000) {
    p.set('lastClaimAt', stamp())
    changed = true
  }
  if (p.getBool('workerDown')) {
    p.set('workerDown', false)
    emitWebAlert(store, 'platform', '', 'worker_recovered', 'worker-up:' + stamp(), {})
    changed = true
  }
  if (disk !== undefined) {
    const low = disk < 15 * 1024 ** 3
    if (low !== p.getBool('diskLow') || !p.getString('diskChangedAt')) {
      const transition = low !== p.getBool('diskLow')
      p.set('diskLow', low)
      p.set('diskChangedAt', stamp())
      if (transition)
        emitWebAlert(
          store,
          'platform',
          '',
          low ? 'disk_low' : 'disk_recovered',
          'disk:' + low + ':' + p.getString('diskChangedAt'),
          {
            freeBytes: disk,
          }
        )
      changed = true
    }
  }
  if (scanPlatformOutbox(p, store)) changed = true
  if (changed) store.save(p)
  return {
    host: p.getString('diskChangedAt') ? { low: p.getBool('diskLow'), changedAt: p.getString('diskChangedAt') } : null,
    platform: p,
  }
}
const lease = (input: any, store: any = $app) => {
  const row = store.findRecordById(DELIVERIES, id(input.id))
  if (
    row.getString('status') !== 'sending' ||
    typeof input.proof !== 'string' ||
    !/^[A-Za-z0-9]{80}$/.test(input.proof) ||
    !$security.equal(row.getString('leaseHash'), $security.sha256(input.proof))
  )
    throw new UnauthorizedError('Envoi invalide.')
  return row
}
/** Webhook du canal plateforme, gardé en mémoire par le worker pour « contrôleur injoignable ». */
const platformWebhook = (store: any) => {
  if (!platformCollection(store)) return null
  const p = platformRow(store)
  if (!p.getBool('enabled')) return null
  const value = config(p, true)
  if (!value.webhook.enabled || !value.webhook.url || !value.events.includes('controller_unreachable')) return null
  return {
    url: value.webhook.url,
    secret: value.webhook.secret || '',
    format: value.webhook.format || 'generic',
    host: $os.getenv('APP_HOST') || '',
  }
}
export const HandleWebNotificationClaim = (e: core.RequestEvent) => {
  requireWorker(e)
  const input = webInput(e)
  if (input.diskFreeBytes !== undefined && (!Number.isSafeInteger(input.diskFreeBytes) || input.diskFreeBytes < 0))
    throw new BadRequestError('Mesure disque invalide.')
  let result: any = null,
    hook: any = null
  $app.runInTransaction((tx) => {
    for (const old of tx.findRecordsByFilter(DELIVERIES, 'created<{:cutoff}', 'created', 500, 0, {
      cutoff: stamp(Date.now() - 30 * 86400000),
    }))
      tx.delete(old)
    try {
      for (const old of tx.findRecordsByFilter(OUTBOX, 'created<{:cutoff}', 'created', 500, 0, {
        cutoff: stamp(Date.now() - 30 * 86400000),
      }))
        tx.delete(old)
    } catch {}
    let context: ScanContext = { host: null, platform: null }
    try {
      context = scanPlatform(input.diskFreeBytes, tx)
    } catch {}
    for (const settings of tx.findRecordsByFilter(SETTINGS, 'enabled=true && scanAfter<={:due}', 'scanAfter', 200, 0, {
      due: stamp(Date.now() - SCAN_EVERY),
    }))
      try {
        scan(settings, context, tx)
      } catch {}
    for (const row of tx.findRecordsByFilter(DELIVERIES, 'status="sending" && leaseAt<{:stale}', 'leaseAt', 100, 0, {
      stale: stamp(Date.now() - 60000),
    })) {
      row.set('status', row.getInt('attempts') >= 3 || row.getString('trigger') === 'test' ? 'failed' : 'pending')
      row.set('leaseHash', '')
      row.set('nextAttemptAt', stamp(Date.now() + 30000))
      row.set('lastError', 'Le délai de confirmation de l’envoi a expiré.')
      tx.save(row)
    }
    const rows = tx.findRecordsByFilter(DELIVERIES, 'status="pending" && nextAttemptAt<={:now}', 'created,id', 100, 0, {
      now: stamp(),
    })
    for (const row of rows) {
      if (Date.parse(row.getString('created')) < Date.now() - 86400000) {
        row.set('status', 'suppressed')
        row.set('lastError', 'Cette notification a expiré après 24 heures.')
        tx.save(row)
        continue
      }
      const platform = row.getString('scope') === 'platform',
        settings = settingsOf(row, tx),
        value = config(settings, platform),
        channel = row.getString('channel')
      if (
        !settings ||
        (platform && !platformSettingActive(settings, tx)) ||
        !deploymentTargetActive(row, tx) ||
        (platform ? !settings.getBool('enabled') && row.getString('trigger') !== 'test' : false) ||
        (!platform && !settingActive(row.getString('actorId'), row.getString('appId'), tx)) ||
        settings.getString('revision') !== row.getString('settingsRevision') ||
        (row.getString('trigger') !== 'test' && !value[channel].enabled)
      ) {
        row.set('status', 'suppressed')
        row.set('lastError', 'Le canal ou les droits ont changé avant cet envoi.')
        tx.save(row)
        continue
      }
      if (channel === 'email' && !smtpAvailable()) {
        row.set('status', 'failed')
        row.set('lastError', 'SMTP est indisponible sur la plateforme.')
        tx.save(row)
        continue
      }
      if (channel === 'email' && !recipientsFor(row.getString('scope'), value, row.getString('appId'), tx).length) {
        row.set('status', 'failed')
        row.set('lastError', 'Aucun destinataire enregistré n’est encore membre vérifié du projet.')
        tx.save(row)
        continue
      }
      const proof = $security.randomString(80)
      row.set('status', 'sending')
      row.set('attempts', row.getInt('attempts') + 1)
      row.set('leaseAt', stamp())
      row.set('leaseHash', $security.sha256(proof))
      tx.save(row)
      result = {
        id: row.id,
        proof,
        channel,
        scope: platform ? 'platform' : 'app',
        trigger: row.getString('trigger'),
        payload: JSON.parse(row.getString('payload') || '{}'),
        ...(channel === 'webhook'
          ? { url: value.webhook.url, secret: value.webhook.secret, format: value.webhook.format || 'generic' }
          : {}),
      }
      break
    }
    try {
      hook = platformWebhook(tx)
    } catch {}
  })
  // platformWebhook toujours présent : null vide le cache mémoire du worker.
  return webJson(e, { job: result, platformWebhook: hook })
}
const ERROR_CLASSES = [
  '',
  'url_refused',
  'dns',
  'dns_timeout',
  'address_refused',
  'connect',
  'tls',
  'timeout',
  'redirect',
  'rate_limited',
  'http_4xx',
  'http_5xx',
  'format',
  'payload',
  'send',
  'smtp',
]
const CLASS_MESSAGES: Record<string, string> = {
  tls: 'Certificat TLS du webhook invalide.',
  dns: 'Nom de domaine du webhook introuvable.',
  dns_timeout: 'Résolution DNS du webhook trop lente.',
  address_refused: 'Le webhook pointe vers une adresse privée : refusé.',
  url_refused: 'URL de webhook refusée (HTTPS public sur le port 443 uniquement).',
  rate_limited: 'Le canal limite le débit (HTTP 429).',
  redirect: 'Le webhook répond par une redirection : non suivie.',
  http_4xx: 'Le canal a refusé la notification (HTTP 4xx).',
  http_5xx: 'Le canal est en erreur (HTTP 5xx).',
  connect: 'Connexion au webhook impossible.',
  timeout: 'Le webhook ne répond pas dans le délai.',
  smtp: 'Envoi SMTP impossible.',
  format: 'Le format du webhook ne correspond pas à son URL (Slack/Discord officiels).',
}
const CODE_MESSAGES: Record<string, string> = {
  ssrf: 'Le webhook doit pointer exclusivement vers une adresse HTTPS publique.',
  timeout: 'Le canal n’a pas répondu dans le délai prévu.',
  smtp: 'Le serveur SMTP a refusé cet envoi.',
  invalid_format: 'Le format du webhook ne correspond pas à son URL (Slack/Discord officiels).',
}
export const HandleWebNotificationReport = (e: core.RequestEvent) => {
  requireWorker(e)
  const input = webInput(e)
  if (
    typeof input.success !== 'boolean' ||
    !Number.isInteger(input.httpStatus) ||
    input.httpStatus < 0 ||
    input.httpStatus > 599 ||
    (input.errorClass !== undefined && !ERROR_CLASSES.includes(input.errorClass)) ||
    (input.durationMs !== undefined &&
      (!Number.isInteger(input.durationMs) || input.durationMs < 0 || input.durationMs > 60000)) ||
    (input.final !== undefined && typeof input.final !== 'boolean')
  )
    throw new BadRequestError('Compte rendu invalide.')
  $app.runInTransaction((tx) => {
    const row = lease(input, tx)
    row.set('httpStatus', input.httpStatus)
    row.set('leaseHash', '')
    if (input.success) {
      row.set('status', 'sent')
      row.set('sentAt', stamp())
      row.set('lastError', '')
    } else {
      // Test, refus d'adresse ou format invalide : un nouvel essai ne changerait rien.
      const permanent =
        input.final === true ||
        row.getString('trigger') === 'test' ||
        row.getInt('attempts') >= 3 ||
        ['ssrf', 'invalid_format'].includes(input.errorCode)
      row.set('status', permanent ? 'failed' : 'pending')
      row.set('nextAttemptAt', stamp(Date.now() + 30000 * 2 ** (row.getInt('attempts') - 1)))
      row.set(
        'lastError',
        CODE_MESSAGES[input.errorCode === 'invalid_format' || input.errorCode === 'ssrf' ? input.errorCode : ''] ||
          CLASS_MESSAGES[input.errorClass] ||
          CODE_MESSAGES[input.errorCode] ||
          'Le canal a refusé la notification ou ne répond pas.'
      )
    }
    tx.save(row)
  })
  return webJson(e, { ok: true })
}
export const HandleWebNotificationEmail = (e: core.RequestEvent) => {
  requireWorker(e)
  const row = lease(webInput(e))
  sendDeliveryEmail(row)
  return webJson(e, { sent: true })
}
/** Chien de garde (cron, chaque minute) : worker muet, emails plateforme sans worker, état du service de reprise. */
export const HandleWebAlertWatchdog = () => {
  if (!webEnabled() || !platformCollection()) return
  let workerDown = false,
    checkRecovery = false,
    activatedAt = ''
  $app.runInTransaction((tx) => {
    const p = platformRow(tx),
      lastClaim = p.getString('lastClaimAt'),
      last = Date.parse(lastClaim || '')
    if (last && Date.now() - last > WORKER_DOWN_AFTER && !p.getBool('workerDown')) {
      p.set('workerDown', true)
      emitWebAlert(tx, 'platform', '', 'worker_unresponsive', 'worker-down:' + lastClaim, { lastClaimAt: lastClaim })
    }
    workerDown = p.getBool('workerDown')
    // Worker arrêté : personne d'autre ne transforme l'outbox plateforme en livraisons.
    if (workerDown) scanPlatformOutbox(p, tx)
    const checked = Date.parse(p.getString('recoveryCheckedAt') || '')
    if (!checked || Date.now() - checked > 15 * 60000) {
      p.set('recoveryCheckedAt', stamp())
      checkRecovery = true
    }
    activatedAt = p.getString('activatedAt')
    tx.save(p)
  })
  // Worker arrêté : les emails plateforme partent d'ici (les webhooks restent réservés au worker, épinglage DNS).
  if (workerDown)
    for (const row of $app.findRecordsByFilter(
      DELIVERIES,
      'scope="platform" && channel="email" && status="pending" && nextAttemptAt<={:now}',
      'created',
      5,
      0,
      { now: stamp() }
    )) {
      row.set('status', 'sending')
      row.set('attempts', row.getInt('attempts') + 1)
      row.set('leaseAt', stamp())
      $app.save(row)
      try {
        sendDeliveryEmail(row)
        const sent = $app.findRecordById(DELIVERIES, row.id)
        sent.set('status', 'sent')
        sent.set('sentAt', stamp())
        sent.set('lastError', '')
        $app.save(sent)
      } catch (error) {
        const failed = $app.findRecordById(DELIVERIES, row.id)
        failed.set('status', failed.getInt('attempts') >= 3 ? 'failed' : 'pending')
        failed.set('nextAttemptAt', stamp(Date.now() + 60000 * 2 ** (failed.getInt('attempts') - 1)))
        failed.set('lastError', CLASS_MESSAGES.smtp!)
        $app.save(failed)
      }
    }
  if (checkRecovery)
    try {
      recoveryWatch(activatedAt)
    } catch (error) {
      console.log(
        'web alert watchdog: recovery status unavailable',
        `${(error as any)?.message || error}`.slice(0, 200)
      )
    }
}
/**
 * Filet de sécurité de la route poussée par le service de reprise : mêmes clés (`platform-recovery:<runId>`), donc un
 * seul événement par exécution, que la poussée soit arrivée ou non.
 */
const recoveryWatch = (activatedAt: string) => {
  const status = webPlatformRecoveryApi('status').response
  if (!status || status.unavailable || !status.configured) return
  const since = Date.parse(activatedAt || '') || 0
  if (status.settings?.enabled && !status.worker?.online)
    emitWebAlert(
      $app,
      'platform',
      '',
      'recovery_service_offline',
      'recovery-offline:' + (status.worker?.lastSeenAt || 'never'),
      {}
    )
  const finished = Date.parse(status.latest?.finishedAt || '')
  if (status.settings?.enabled && finished && Date.now() - finished > 36 * HOUR)
    emitWebAlert($app, 'platform', '', 'recovery_capture_stale', 'recovery-stale:' + status.latest.id, {
      latestId: `${status.latest.id}`.slice(0, 64),
    })
  const runs = webPlatformRecoveryApi('runs', { page: 1 }).response?.items
  for (const run of Array.isArray(runs) ? runs : []) {
    if (!run || run.status !== 'failed' || !/^[a-f0-9]{32}$/.test(run.id || '')) continue
    if (since && Date.parse(run.finishedAt || '') < since) continue
    emitWebAlert($app, 'platform', '', 'recovery_failed', 'platform-recovery:' + run.id, {
      runId: run.id,
      operation: ['capture', 'drill'].includes(run.operation) ? run.operation : '',
      stage: `${run.stage || ''}`.slice(0, 80),
      summary: `${run.error || ''}`.slice(0, 160),
    })
  }
}
const RECOVERY_ERRORS = ['CONFIGURATION', 'INSUFFICIENT_SPACE', 'DEPENDENCIES_NOT_READY', 'VERIFICATION']
/** Poussée du service de reprise (X-Web-Worker, boucle locale) : un échec de capture ou de test. Idempotente. */
export const HandleWebPlatformRecoveryEvent = (e: core.RequestEvent) => {
  requireWorker(e)
  const input = webInput(e)
  const ok =
    input.version === 1 &&
    input.event === 'recovery_failed' &&
    typeof input.runId === 'string' &&
    /^platform-recovery:[a-f0-9]{32}$/.test(input.eventKey || '') &&
    input.eventKey === 'platform-recovery:' + input.runId &&
    typeof input.platform === 'string' &&
    /^[a-z0-9][a-z0-9-]{0,40}$/.test(input.platform) &&
    ['capture', 'drill'].includes(input.operation) &&
    ['manual', 'scheduled', 'cli'].includes(input.trigger) &&
    typeof input.stage === 'string' &&
    input.stage.length <= 20 &&
    RECOVERY_ERRORS.includes(input.errorCode) &&
    typeof input.occurredAt === 'string' &&
    !isNaN(Date.parse(input.occurredAt)) &&
    typeof input.message === 'string' &&
    input.message.length <= 200
  if (!ok) throw new BadRequestError('Événement de reprise invalide.')
  if (!platformCollection()) throw new BadRequestError('Alertes de la plateforme non installées.')
  // Le rappel de runInTransaction ne doit rien retourner (une valeur serait lue comme une erreur).
  $app.runInTransaction((tx) => {
    emitWebAlert(tx, 'platform', '', 'recovery_failed', input.eventKey, {
      runId: input.runId,
      operation: input.operation,
      trigger: input.trigger,
      stage: input.stage,
      errorCode: input.errorCode,
      summary: input.message.replace(/\s+/g, ' ').slice(0, 160),
    })
  })
  // Doublon : déjà enregistré, toujours 202 (le service cesse ses nouveaux essais).
  return webJson(e, { accepted: true }, 202)
}
const platformResponse = (row: core.Record) => {
  const value = config(row, true),
    last = row.getString('lastClaimAt')
  return {
    revision: row.getString('configEncrypted') ? row.getString('revision') : '',
    ...channels(value),
    copyAppEvents: !!value.copyAppEvents,
    availableEvents: PLATFORM_EVENTS,
    worker: {
      lastClaimAt: last,
      down: row.getBool('workerDown') || (!!last && Date.now() - Date.parse(last) > WORKER_DOWN_AFTER),
    },
    disk: { low: row.getBool('diskLow'), changedAt: row.getString('diskChangedAt') },
    recovery: { checkedAt: row.getString('recoveryCheckedAt') },
  }
}
const requirePlatformChannel = (e: core.RequestEvent) => {
  const actor = requirePlatformAdmin(e)
  if (!platformCollection()) throw new BadRequestError('Alertes de la plateforme non installées.')
  return actor
}
export const HandleWebPlatformNotificationsGet = (e: core.RequestEvent) => {
  requirePlatformChannel(e)
  let row: core.Record | undefined
  $app.runInTransaction((tx) => {
    row = platformRow(tx)
  })
  return webJson(e, platformResponse(row!))
}
export const HandleWebPlatformNotificationsPut = (e: core.RequestEvent) => {
  const actor = requirePlatformChannel(e),
    input = webInput(e)
  if (
    Object.keys(input).some(
      (key) =>
        !['revision', 'webhook', 'email', 'events', 'cooldownSeconds', 'copyAppEvents', 'useOperator'].includes(key)
    )
  )
    throw new BadRequestError('Options de notification invalides.')
  let result: any,
    generatedSecret = ''
  $app.runInTransaction((tx) => {
    const row = platformRow(tx),
      saved = !!row.getString('configEncrypted'),
      value = config(row, true)
    if (input.revision !== (saved ? row.getString('revision') : ''))
      throw new BadRequestError('Les canaux ont changé. Rechargez leur configuration.')
    if (input.useOperator !== undefined && typeof input.useOperator !== 'boolean')
      throw new BadRequestError('Options email invalides.')
    if (
      input.useOperator === true &&
      input.email &&
      !input.email.recipients?.length &&
      !value.email.recipients.length
    ) {
      const own = verifiedEmail(actor)
      if (own) input.email = { ...input.email, recipients: [own] }
    }
    generatedSecret = applyChannels(
      input,
      value,
      () => {},
      () => value.email.recipients.length > 0
    )
    if (input.events !== undefined) {
      if (!Array.isArray(input.events) || input.events.some((event: any) => !PLATFORM_EVENTS.includes(event)))
        throw new BadRequestError('Types d’alerte invalides.')
      if (input.events.includes('app_deployment_succeeded') && !value.events.includes('app_deployment_succeeded'))
        value.deploymentSuccessActivatedAt = stamp()
      value.events = [...new Set(input.events)]
    }
    if (input.copyAppEvents !== undefined) {
      if (typeof input.copyAppEvents !== 'boolean') throw new BadRequestError('Options de notification invalides.')
      value.copyAppEvents = input.copyAppEvents
    }
    saveSettings(row, saved ? row : null, actor.id, value, tx)
    result = row
  })
  return webJson(e, { ...platformResponse(result), ...(generatedSecret ? { generatedSecret } : {}) })
}
export const HandleWebPlatformNotificationTest = (e: core.RequestEvent) => {
  const actor = requirePlatformChannel(e),
    input = testInput(e, PLATFORM_EVENTS),
    row = platformRow()
  if (!row.getString('configEncrypted') || !['webhook', 'email'].includes(input.channel))
    throw new BadRequestError('Enregistrez d’abord le canal à tester.')
  const value = config(row, true)
  if (input.channel === 'webhook' ? !value.webhook.url : !value.email.recipients.length || !smtpAvailable())
    throw new BadRequestError('Ce canal n’est pas configuré.')
  if (!value[input.channel].enabled) throw new BadRequestError('Activez et enregistrez ce canal avant de l’essayer.')
  return runTest(e, row, null, 'platform', input, actor.id)
}
export const HandleWebPlatformNotificationHistory = (e: core.RequestEvent) => {
  requirePlatformChannel(e)
  const query = e.request!.url!.query()
  if (query.get('id'))
    return webJson(e, {
      items: $app
        .findRecordsByFilter(DELIVERIES, 'scope="platform" && id={:id}', '', 1, 0, { id: id(query.get('id')) })
        .map(metadata),
      page: 1,
      hasMore: false,
    })
  const page = Math.max(1, Math.min(100000, Math.floor(Number(query.get('page')) || 1))),
    rows = $app.findRecordsByFilter(DELIVERIES, 'scope="platform"', '-created,-id', 21, (page - 1) * 20)
  return webJson(e, { items: rows.slice(0, 20).map(metadata), page, hasMore: rows.length > 20 })
}
