import { mkLog } from '$util/Logger'
import { ReconcileBackupPolicyCrons, TestBackupS3Config } from '../instance/api/HandleInstanceBackups'
import { requireOperatorAdmin } from './auth'
import { scanOrphanInstanceStorage } from './diskCleanup'
import {
  type OperatorSettings,
  normalizeOperatorSettings,
  readOperatorSettings,
  serializeOperatorSettings,
  writeOperatorSettings,
} from './operatorSettings'

const readJsonBody = <T extends Record<string, any>>(e: core.RequestEvent): T => {
  const rawBody = readerToString(e.request.body)
  if (!rawBody.trim()) return {} as T
  try {
    return JSON.parse(rawBody) as T
  } catch (error) {
    throw new BadRequestError(`Impossible d'analyser la requete JSON.`, error)
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
})

const listOperatorUsers = () =>
  $app
    .findRecordsByFilter('users', 'id != ""', '-created')
    .filter((record): record is models.Record => !!record)
    .map(serializeUser)

const ensureAnotherSuperAdminExists = (currentUserId: string) => {
  const superAdmins = $app
    .findRecordsByFilter('users', 'superAdmin = true')
    .filter((record): record is models.Record => !!record)

  if (superAdmins.length <= 1 && superAdmins[0]?.id === currentUserId) {
    throw new BadRequestError('Impossible de retirer le dernier superadmin.')
  }
}

const ensureValidServerTimezone = (timezoneName: string) => {
  const zone = new Timezone(timezoneName)
  const loadedName = zone.string()
  if (!['UTC', 'Etc/UTC', 'Local'].includes(timezoneName) && loadedName === 'UTC') {
    throw new BadRequestError(`Fuseau horaire serveur invalide: ${timezoneName}. Exemple: Indian/Reunion.`)
  }
}

const mergeOperatorSettingsInput = (current: OperatorSettings, input: Partial<OperatorSettings>) => {
  const backupS3 = {
    ...current.backupS3,
    ...(input.backupS3 || {}),
  }

  if (!`${backupS3.secretAccessKey || ''}`.trim()) {
    backupS3.secretAccessKey = current.backupS3.secretAccessKey
  }

  return normalizeOperatorSettings({
    ...current,
    ...input,
    backupS3,
  })
}

export const HandleOperatorAdminOverview = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  const users = listOperatorUsers()
  const totalInstances = $app.countRecords('instances')

  return e.json(200, {
    settings: serializeOperatorSettings(readOperatorSettings()),
    users,
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
  requireOperatorAdmin(e)

  const settings = readOperatorSettings()
  const body = readJsonBody<{
    email?: string
    password?: string
    verified?: boolean
    superAdmin?: boolean
    subscription?: string
    subscription_quantity?: number
    suspension?: string
  }>(e)

  const email = `${body.email || ''}`.trim().toLowerCase()
  const password = `${body.password || ''}`.trim()
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
  record.set('suspension', `${body.suspension || ''}`.trim())
  record.setPassword(password)

  $app.save(record)
  log(`created ${email}`)

  if (!record.getBool('verified')) {
    $mails.sendRecordVerification($app, record)
  }

  return e.json(200, { user: serializeUser(record) })
}

export const HandleOperatorAdminUpdateUser = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
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
  }>(e)

  const record = $app.findRecordById('users', id)

  if (typeof body.email === 'string') {
    const email = body.email.trim().toLowerCase()
    if (!email) throw new BadRequestError("L'email est obligatoire.")
    if (userExists(email, id)) throw new BadRequestError('Cet email est deja utilise.')
    record.set('email', email)
  }

  if (typeof body.password === 'string' && body.password.trim()) {
    const password = body.password.trim()
    if (password.length < 8) throw new BadRequestError('Le mot de passe doit contenir au moins 8 caracteres.')
    record.setPassword(password)
  }

  if (typeof body.verified === 'boolean') record.set('verified', body.verified)
  if (typeof body.subscription === 'string') record.set('subscription', body.subscription)
  if (typeof body.subscription_quantity !== 'undefined') {
    record.set('subscription_quantity', Math.max(0, Math.floor(Number(body.subscription_quantity) || 0)))
  }
  if (typeof body.suspension === 'string') record.set('suspension', body.suspension.trim())
  if (typeof body.superAdmin === 'boolean') {
    if (!body.superAdmin && record.getBool('superAdmin')) ensureAnotherSuperAdminExists(record.id)
    record.set('superAdmin', body.superAdmin)
  }

  $app.save(record)

  return e.json(200, { user: serializeUser(record) })
}

export const HandleOperatorAdminUpdateSettings = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  const current = readOperatorSettings()
  const body = readJsonBody<Partial<OperatorSettings>>(e)
  const nextSettings = mergeOperatorSettingsInput(current, body)
  ensureValidServerTimezone(nextSettings.serverTimezone)
  const settings = writeOperatorSettings(nextSettings)
  ReconcileBackupPolicyCrons()

  return e.json(200, { settings: serializeOperatorSettings(settings) })
}

export const HandleOperatorAdminTestBackupS3 = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  const current = readOperatorSettings()
  const body = readJsonBody<Partial<OperatorSettings>>(e)
  const settings = mergeOperatorSettingsInput(current, body)
  const result = TestBackupS3Config(settings)

  return e.json(200, {
    test: {
      ...result,
      message: `Connexion S3/R2 valide pour ${result.bucket}.`,
    },
  })
}

export const HandleOperatorAdminDiskCleanupPreview = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  return e.json(200, { cleanup: scanOrphanInstanceStorage(false) })
}

export const HandleOperatorAdminDiskCleanupRun = (e: core.RequestEvent) => {
  requireOperatorAdmin(e)
  return e.json(200, { cleanup: scanOrphanInstanceStorage(true) })
}
