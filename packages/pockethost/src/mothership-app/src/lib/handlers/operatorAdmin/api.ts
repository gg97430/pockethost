import { mkLog } from '$util/Logger'
import { ReconcileBackupPolicyCrons, TestBackupS3Config } from '../instance/api/HandleInstanceBackups'
import { listWebMemberships } from '../webApps/access'
import { saveWebUserProjects, validateWebUserProjects } from '../webApps/members'
import { requireOperatorAdmin } from './auth'
import { scanOrphanInstanceStorage } from './diskCleanup'
import {
  type OperatorSettings,
  normalizeOperatorSettings,
  readOperatorSettings,
  serializeOperatorSettings,
  writeOperatorSettings,
} from './operatorSettings'
import {
  validateOperatorSettingsInput,
  validateOperatorUserInput,
  validateS3Configuration,
  validateSMTPConfiguration,
} from './settingsValidation'
import { SMTP_TEST_WORKER } from './smtpTest'
import { sendAccountWelcomeEmail, welcomeMailSettings } from './welcomeEmail'

const readJsonBody = <T extends Record<string, any>>(e: core.RequestEvent): T => {
  try {
    const body = JSON.parse(JSON.stringify(e.requestInfo().body || {}))
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('object expected')
    return body as T
  } catch {
    throw new BadRequestError('Requête JSON invalide.')
  }
}

const suggestUniqueAuthRecordUsername = (collection: string, baseUsername: string) => {
  let username = baseUsername
  for (let i = 0; i < 10; i++) {
    try {
      const total = $app.countRecords(
        collection,
        $dbx.exp('LOWER([[username]])={:username}', { username: username.toLowerCase() })
      )
      if (total === 0) break
    } catch {}
    username = baseUsername + $security.randomStringWithAlphabet(3 + i, '123456789')
  }
  return username
}

const userExists = (email: string, exceptId = '') => {
  try {
    const user = $app.findFirstRecordByData('users', 'email', email)
    return user.id !== exceptId
  } catch {
    return false
  }
}

const countInstancesForUser = (userId: string) => {
  try {
    return $app.countRecords('instances', $dbx.exp('uid = {:uid}', { uid: userId }))
  } catch {
    return 0
  }
}

const validateManagedInstances = (ids: string[]) => {
  const unique = Array.from(new Set(ids))
  if ($app.findRecordsByIds('instances', unique).length !== unique.length)
    throw new BadRequestError('Une base sélectionnée a été supprimée ou est introuvable. Rechargez la liste.')
  return unique
}

const serializeUser = (record: models.Record) => ({
  id: record.id,
  email: record.getString('email'),
  username: record.getString('username'),
  name: record.getString('name'),
  verified: record.getBool('verified'),
  superAdmin: record.getBool('superAdmin'),
  subscription: record.getString('subscription') || 'free',
  subscription_interval: record.getString('subscription_interval'),
  subscription_quantity: Number(record.get('subscription_quantity') || 0),
  suspension: record.getString('suspension'),
  created: record.getString('created'),
  updated: record.getString('updated'),
  instanceCount: countInstancesForUser(record.id),
  managedInstances: record.getStringSlice('managedInstances') || [],
  webProjects: listWebMemberships(record),
})

const userTimestampMs = (record: models.Record) => {
  const timestamp = Date.parse(record.getString('created') || record.getString('updated') || '')
  return Number.isFinite(timestamp) ? timestamp : 0
}

const listOperatorUsers = () =>
  $app
    .findRecordsByFilter('users', 'id != ""', '')
    .filter((record): record is models.Record => !!record)
    .sort((a, b) => userTimestampMs(b) - userTimestampMs(a) || b.id.localeCompare(a.id))
    .map(serializeUser)

const ensureAnotherSuperAdminExists = (currentUserId: string) => {
  const count = $app.countRecords(
    'users',
    $dbx.exp('[[superAdmin]]=true AND [[suspension]]="" AND [[id]] != {:id}', { id: currentUserId })
  )
  if (!count) throw new BadRequestError('Impossible de retirer ou suspendre le dernier superadmin actif.')
}

const ensureValidServerTimezone = (timezoneName: string) => {
  const zone = new Timezone(timezoneName)
  const loadedName = zone.string()
  if (!['UTC', 'Etc/UTC', 'Local'].includes(timezoneName) && loadedName === 'UTC') {
    throw new BadRequestError(`Fuseau horaire serveur invalide: ${timezoneName}. Exemple: Indian/Reunion.`)
  }
}

const normalizeEmailAddress = (value: unknown, field = 'Email') => {
  const email = `${value || ''}`.trim().toLowerCase()
  if (!email) throw new BadRequestError(`${field} obligatoire.`)
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new BadRequestError(`${field} invalide.`)
  return email
}

const mergeOperatorSettingsInput = (current: OperatorSettings, input: Partial<OperatorSettings>) => {
  const backupS3 = {
    ...current.backupS3,
    ...(input.backupS3 || {}),
  }

  if (backupS3.secretAccessKey === '' || backupS3.secretAccessKey === undefined) {
    backupS3.secretAccessKey = current.backupS3.secretAccessKey
  }

  const smtp = {
    ...current.smtp,
    ...(input.smtp || {}),
  }

  if (smtp.password === '' || smtp.password === undefined) {
    smtp.password = current.smtp.password
  }
  if (input.smtp && Object.prototype.hasOwnProperty.call(input.smtp, 'username') && !smtp.username.trim())
    smtp.password = ''

  return normalizeOperatorSettings({
    ...current,
    ...input,
    backupS3,
    smtp,
  })
}

const sendOperatorSMTPTest = (settings: OperatorSettings, to: string) => {
  validateSMTPConfiguration(settings.smtp)
  if (!settings.smtp.enabled) throw new BadRequestError('SMTP est désactivé.')
  const path = `${$app.dataDir()}/.smtp-test-${$security.randomString(32)}.json`
  try {
    $os.writeFile(path, JSON.stringify({ ...settings.smtp, to }), 0o600 as any)
    const result = JSON.parse(toString($os.cmd('python3', '-c', SMTP_TEST_WORKER, path).output()))
    if (!result.ok) throw new BadRequestError(result.message)
    return { to, host: settings.smtp.host, port: settings.smtp.port, message: result.message }
  } finally {
    try {
      $os.remove(path)
    } catch {}
  }
}

export const HandleOperatorAdminOverview = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  e.response.header().set('Cache-Control', 'no-store')
  const users = listOperatorUsers()
  const totalInstances = $app.countRecords('instances')

  return e.json(200, {
    settings: serializeOperatorSettings(readOperatorSettings()),
    users,
    instances: $app
      .findAllRecords('instances')
      .map((record) => ({
        id: record.id,
        subdomain: record.getString('subdomain'),
        uid: record.getString('uid'),
      }))
      .sort((a, b) => a.subdomain.localeCompare(b.subdomain)),
    webProjects: $app
      .findAllRecords('web_projects')
      .map((record) => ({ id: record.id, name: record.getString('name'), repository: record.getString('repository') }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    stats: {
      totalUsers: users.length,
      verifiedUsers: users.filter((user) => user.verified).length,
      superAdmins: users.filter((user) => user.superAdmin).length,
      totalInstances,
      suspendedUsers: users.filter((user) => !!user.suspension).length,
    },
  })
}

export const HandleOperatorAdminCreateUser = (e: core.RequestEvent) => {
  const log = mkLog('operator-admin:create-user')
  const actor = requireOperatorAdmin(e)

  const settings = readOperatorSettings()
  const body = readJsonBody<{
    email?: string
    password?: string
    verified?: boolean
    superAdmin?: boolean
    subscription?: string
    subscription_quantity?: number
    suspension?: string
    managedInstances?: string[]
    webProjects?: { projectId: string; role: string }[]
    sendWelcomeEmail?: boolean
  }>(e)

  if (body.sendWelcomeEmail !== undefined && typeof body.sendWelcomeEmail !== 'boolean')
    throw new BadRequestError('Le choix d’envoi du mail doit être un booléen.')
  const welcomeSettings = body.sendWelcomeEmail === true ? welcomeMailSettings() : undefined
  validateOperatorUserInput(body)
  if (body.webProjects !== undefined) validateWebUserProjects(body.webProjects)
  const email = normalizeEmailAddress(body.email)
  const password = `${body.password || ''}`
  if (!email) throw new BadRequestError("L'email est obligatoire.")
  if (userExists(email)) throw new BadRequestError('Ce compte existe deja.')
  if (password.length < 8) throw new BadRequestError('Le mot de passe doit contenir au moins 8 caracteres.')

  const collection = $app.findCollectionByNameOrId('users')
  const record = new Record(collection)
  const username = suggestUniqueAuthRecordUsername('users', 'user' + $security.randomStringWithAlphabet(5, '123456789'))

  record.set('username', username)
  record.set('email', email)
  record.set('subscription', body.subscription || settings.defaultSubscription)
  record.set('subscription_quantity', body.subscription_quantity ?? settings.defaultUserQuota)
  record.set('verified', body.verified ?? settings.autoVerifyUsers)
  record.set('superAdmin', !!body.superAdmin)
  record.set('managedInstances', validateManagedInstances(body.managedInstances || []))
  record.set('suspension', `${body.suspension || ''}`.trim())
  record.setPassword(password)

  $app.runInTransaction((tx) => {
    tx.save(record)
    if (body.webProjects !== undefined) saveWebUserProjects(tx, actor, record, body.webProjects)
  })
  log(`created ${email}`)

  let warning = ''
  let welcomeEmailSent = false
  if (welcomeSettings) {
    try {
      sendAccountWelcomeEmail(record, password, welcomeSettings)
      welcomeEmailSent = true
    } catch {
      warning =
        'Compte créé, mais le mail contenant les identifiants n’a pas pu être envoyé. Vérifiez SMTP. Ne recréez pas ce compte.'
    }
  } else if (!record.getBool('verified')) {
    try {
      $mails.sendRecordVerification($app, record)
    } catch {
      warning = 'Compte créé, mais l’email de vérification n’a pas pu être envoyé. Vérifiez SMTP.'
    }
  }
  return e.json(200, { user: serializeUser(record), warning, welcomeEmailSent })
}

export const HandleOperatorAdminUpdateUser = (e: core.RequestEvent) => {
  const actor = requireOperatorAdmin(e)
  const id = e.request.pathValue('id')
  if (!id) throw new BadRequestError("L'identifiant utilisateur est obligatoire.")

  const body = readJsonBody<{
    email?: string
    password?: string
    verified?: boolean
    superAdmin?: boolean
    subscription?: string
    subscription_quantity?: number
    suspension?: string
    managedInstances?: string[]
    webProjects?: { projectId: string; role: string }[]
  }>(e)

  validateOperatorUserInput(body)
  if (body.webProjects !== undefined) validateWebUserProjects(body.webProjects)
  const record = $app.findRecordById('users', id)
  if (
    record.getBool('superAdmin') &&
    (body.superAdmin === false || (typeof body.suspension === 'string' && body.suspension.trim()))
  )
    ensureAnotherSuperAdminExists(record.id)

  if (typeof body.email === 'string') {
    const email = normalizeEmailAddress(body.email)
    if (!email) throw new BadRequestError("L'email est obligatoire.")
    if (userExists(email, id)) throw new BadRequestError('Cet email est deja utilise.')
    record.set('email', email)
  }

  if (typeof body.password === 'string' && body.password.trim()) {
    const password = body.password
    if (password.length < 8) throw new BadRequestError('Le mot de passe doit contenir au moins 8 caracteres.')
    record.setPassword(password)
  }

  if (typeof body.verified === 'boolean') record.set('verified', body.verified)
  if (body.managedInstances !== undefined)
    record.set('managedInstances', validateManagedInstances(body.managedInstances))
  if (typeof body.subscription === 'string') record.set('subscription', body.subscription)
  if (typeof body.subscription_quantity !== 'undefined') {
    record.set('subscription_quantity', Math.max(0, Math.floor(Number(body.subscription_quantity) || 0)))
  }
  if (typeof body.suspension === 'string') record.set('suspension', body.suspension.trim())
  if (typeof body.superAdmin === 'boolean') {
    if (!body.superAdmin && record.getBool('superAdmin')) ensureAnotherSuperAdminExists(record.id)
    record.set('superAdmin', body.superAdmin)
  }

  $app.runInTransaction((tx) => {
    tx.save(record)
    if (body.webProjects !== undefined) saveWebUserProjects(tx, actor, record, body.webProjects)
  })

  return e.json(200, { user: serializeUser(record) })
}

export const HandleOperatorAdminUpdateSettings = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  const current = readOperatorSettings()
  const body = readJsonBody<Partial<OperatorSettings>>(e)
  validateOperatorSettingsInput(body)
  const nextSettings = mergeOperatorSettingsInput(current, body)
  ensureValidServerTimezone(nextSettings.serverTimezone)
  const settings = writeOperatorSettings(nextSettings)
  ReconcileBackupPolicyCrons()

  e.response.header().set('Cache-Control', 'no-store')
  return e.json(200, { settings: serializeOperatorSettings(settings) })
}

export const HandleOperatorAdminTestBackupS3 = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  const current = readOperatorSettings()
  const body = readJsonBody<Partial<OperatorSettings>>(e)
  validateOperatorSettingsInput(body)
  const settings = mergeOperatorSettingsInput(current, body)
  validateS3Configuration(settings.backupS3)
  let result: ReturnType<typeof TestBackupS3Config>
  try {
    result = TestBackupS3Config(settings)
  } catch (error) {
    const raw = String(error)
    const reason = /AccessDenied|Forbidden/i.test(raw)
      ? 'Accès refusé : vérifiez les permissions du bucket.'
      : /SignatureDoesNotMatch|InvalidAccessKeyId/i.test(raw)
        ? 'Identifiants S3/R2 refusés.'
        : /NoSuchBucket/i.test(raw)
          ? 'Bucket introuvable.'
          : /connect|timeout|endpoint/i.test(raw)
            ? 'Connexion impossible : vérifiez l’endpoint et le réseau.'
            : 'Vérifiez l’activation, le bucket, les clés et la disponibilité du client AWS.'
    throw new BadRequestError('Test S3/R2 échoué. ' + reason)
  }

  return e.json(200, {
    test: {
      ...result,
      message: `Connexion S3/R2 valide pour ${result.bucket}.`,
    },
  })
}

export const HandleOperatorAdminTestSMTP = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  const current = readOperatorSettings()
  const body = readJsonBody<Partial<OperatorSettings> & { testEmail?: string }>(e)
  validateOperatorSettingsInput(body)
  const settings = mergeOperatorSettingsInput(current, body)
  const target = normalizeEmailAddress(
    body.testEmail || settings.supportEmail || settings.smtp.senderAddress,
    'Email de test'
  )
  const result = sendOperatorSMTPTest(settings, target)

  return e.json(200, {
    test: {
      ...result,
      message: result.message,
    },
  })
}

export const HandleOperatorAdminDiskCleanupPreview = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  return e.json(200, { cleanup: scanOrphanInstanceStorage(false) })
}

export const HandleOperatorAdminDiskCleanupRun = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  const input = readJsonBody<{ snapshot?: string }>(e)
  return e.json(200, { cleanup: scanOrphanInstanceStorage(true, input.snapshot || '') })
}
