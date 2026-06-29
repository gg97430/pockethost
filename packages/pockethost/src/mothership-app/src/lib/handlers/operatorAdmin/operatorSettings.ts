export type OperatorSettings = {
  publicSignupEnabled: boolean
  autoVerifyUsers: boolean
  defaultUserQuota: number
  defaultSubscription: 'free' | 'premium' | 'founder' | 'flounder' | 'legacy'
  serverTimezone: string
  backupS3: OperatorBackupS3Settings
  smtp: OperatorSMTPSettings
  defaultInstancePower: boolean
  defaultInstanceDevMode: boolean
  defaultSyncAdmin: boolean
  defaultAutoVacuum: boolean
  supportEmail: string
  maintenanceMessage: string
  notes: string
}

export type OperatorBackupS3Settings = {
  enabled: boolean
  endpoint: string
  bucket: string
  prefix: string
  region: string
  accessKeyId: string
  secretAccessKey: string
}

export type PublicOperatorBackupS3Settings = Omit<OperatorBackupS3Settings, 'secretAccessKey'> & {
  hasSecretAccessKey: boolean
}

export type OperatorSMTPSettings = {
  enabled: boolean
  host: string
  port: number
  username: string
  password: string
  authMethod: 'PLAIN' | 'LOGIN'
  tls: boolean
  localName: string
  senderName: string
  senderAddress: string
}

export type PublicOperatorSMTPSettings = Omit<OperatorSMTPSettings, 'password'> & {
  hasPassword: boolean
}

export type PublicOperatorSettings = Omit<OperatorSettings, 'backupS3' | 'smtp'> & {
  backupS3: PublicOperatorBackupS3Settings
  smtp: PublicOperatorSMTPSettings
}

export const OPERATOR_SETTINGS_NAME = 'operator_settings'
export const DEFAULT_SERVER_TIMEZONE = 'Indian/Reunion'
export const DEFAULT_BACKUP_S3_PREFIX = 'instances'
export const DEFAULT_BACKUP_S3_REGION = 'auto'

const envBoolean = (name: string, fallback: boolean) => {
  const raw = envString(name).trim().toLowerCase()
  if (!raw) return fallback
  return ['1', 'true', 'yes', 'on'].includes(raw)
}

const envNumber = (name: string, fallback: number) => {
  const value = Number(envString(name))
  if (!Number.isFinite(value) || value < 0) return fallback
  return value
}

const envString = (name: string, fallback = '') => {
  const osValue = typeof $os === 'undefined' ? '' : $os.getenv(name)
  return `${process.env[name] || osValue || fallback}`
}

const currentPocketBaseSettings = () => {
  try {
    if (typeof $app === 'undefined') return null
    return $app.settings()
  } catch {
    return null
  }
}

export const normalizeServerTimezone = (value: unknown, fallback = DEFAULT_SERVER_TIMEZONE) => {
  const raw = `${value || ''}`.trim()
  if (!raw) return fallback
  if (['UTC', 'Local'].includes(raw)) return raw
  if (/^[A-Za-z_]+(?:\/[A-Za-z0-9._+-]+)+$/.test(raw)) return raw
  return fallback
}

const normalizeText = (value: unknown, fallback = '', max = 500) => {
  const raw = value === undefined || value === null ? fallback : value
  return `${raw || ''}`.trim().slice(0, max)
}

const normalizeBackupS3Prefix = (value: unknown, fallback = DEFAULT_BACKUP_S3_PREFIX) => {
  const raw = normalizeText(value, fallback, 500)
    .replace(/^\/+|\/+$/g, '')
    .replace(/\/{2,}/g, '/')
  return raw || DEFAULT_BACKUP_S3_PREFIX
}

const normalizeSMTPAuthMethod = (value: unknown): OperatorSMTPSettings['authMethod'] => {
  const raw = `${value || ''}`.trim().toUpperCase()
  return raw === 'LOGIN' ? 'LOGIN' : 'PLAIN'
}

const normalizeSMTPPort = (value: unknown, fallback = 587) => {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return fallback
  return Math.max(1, Math.min(65535, Math.floor(numeric)))
}

const defaultBackupS3Settings = (): OperatorBackupS3Settings => ({
  enabled: envBoolean('INSTANCE_BACKUP_S3_ENABLED', false),
  endpoint: normalizeText(envString('INSTANCE_BACKUP_S3_ENDPOINT'), '', 500),
  bucket: normalizeText(envString('INSTANCE_BACKUP_S3_BUCKET'), '', 255),
  prefix: normalizeBackupS3Prefix(envString('INSTANCE_BACKUP_S3_PREFIX'), DEFAULT_BACKUP_S3_PREFIX),
  region: normalizeText(envString('AWS_DEFAULT_REGION'), DEFAULT_BACKUP_S3_REGION, 64) || DEFAULT_BACKUP_S3_REGION,
  accessKeyId: normalizeText(envString('AWS_ACCESS_KEY_ID'), '', 255),
  secretAccessKey: normalizeText(envString('AWS_SECRET_ACCESS_KEY'), '', 1024),
})

export const normalizeBackupS3Settings = (value?: Partial<OperatorBackupS3Settings>): OperatorBackupS3Settings => {
  const defaults = defaultBackupS3Settings()

  return {
    enabled: !!value?.enabled,
    endpoint: normalizeText(value?.endpoint, defaults.endpoint, 500),
    bucket: normalizeText(value?.bucket, defaults.bucket, 255),
    prefix: normalizeBackupS3Prefix(value?.prefix, defaults.prefix),
    region: normalizeText(value?.region, defaults.region, 64) || DEFAULT_BACKUP_S3_REGION,
    accessKeyId: normalizeText(value?.accessKeyId, defaults.accessKeyId, 255),
    secretAccessKey: normalizeText(value?.secretAccessKey, defaults.secretAccessKey, 1024),
  }
}

const defaultSMTPSettings = (): OperatorSMTPSettings => {
  const pocketBaseSettings = currentPocketBaseSettings()
  const smtp = pocketBaseSettings?.smtp
  const meta = pocketBaseSettings?.meta
  const senderAddress = envString('SMTP_SENDER_ADDRESS', meta?.senderAddress || envString('PH_SUPPORT_EMAIL'))

  return {
    enabled: envBoolean('SMTP_ENABLED', smtp?.enabled ?? false),
    host: normalizeText(envString('SMTP_HOST', smtp?.host || ''), '', 255),
    port: normalizeSMTPPort(envString('SMTP_PORT'), smtp?.port || 587),
    username: normalizeText(envString('SMTP_USERNAME', smtp?.username || ''), '', 255),
    password: normalizeText(envString('SMTP_PASSWORD', smtp?.password || ''), '', 1024),
    authMethod: normalizeSMTPAuthMethod(envString('SMTP_AUTH_METHOD', smtp?.authMethod || 'PLAIN')),
    tls: envBoolean('SMTP_TLS', smtp?.tls ?? false),
    localName: normalizeText(envString('SMTP_LOCAL_NAME', smtp?.localName || ''), '', 255),
    senderName: normalizeText(envString('SMTP_SENDER_NAME', meta?.senderName || 'Gestion PocketBase'), '', 255),
    senderAddress: normalizeText(senderAddress, '', 255),
  }
}

export const normalizeSMTPSettings = (value?: Partial<OperatorSMTPSettings>): OperatorSMTPSettings => {
  const defaults = defaultSMTPSettings()

  return {
    enabled: !!value?.enabled,
    host: normalizeText(value?.host, defaults.host, 255),
    port: normalizeSMTPPort(value?.port, defaults.port),
    username: normalizeText(value?.username, defaults.username, 255),
    password: normalizeText(value?.password, defaults.password, 1024),
    authMethod: normalizeSMTPAuthMethod(value?.authMethod || defaults.authMethod),
    tls: !!(value?.tls ?? defaults.tls),
    localName: normalizeText(value?.localName, defaults.localName, 255),
    senderName: normalizeText(value?.senderName, defaults.senderName, 255),
    senderAddress: normalizeText(value?.senderAddress, defaults.senderAddress, 255),
  }
}

export const serializeOperatorSettings = (settings: OperatorSettings): PublicOperatorSettings => {
  const { secretAccessKey, ...backupS3 } = settings.backupS3
  const { password, ...smtp } = settings.smtp
  return {
    ...settings,
    backupS3: {
      ...backupS3,
      hasSecretAccessKey: !!secretAccessKey,
    },
    smtp: {
      ...smtp,
      hasPassword: !!password,
    },
  }
}

export const defaultOperatorSettings = (): OperatorSettings => {
  const autoVerifyUsers = envBoolean('PH_AUTO_VERIFY_SIGNUPS', true)
  return {
    publicSignupEnabled: envBoolean('PH_PUBLIC_SIGNUP_ENABLED', false),
    autoVerifyUsers,
    defaultUserQuota: envNumber('PH_SIGNUP_SUBSCRIPTION_QUANTITY', autoVerifyUsers ? 250 : 0),
    defaultSubscription: 'free',
    serverTimezone: normalizeServerTimezone(envString('PH_SERVER_TIMEZONE', DEFAULT_SERVER_TIMEZONE)),
    backupS3: defaultBackupS3Settings(),
    smtp: defaultSMTPSettings(),
    defaultInstancePower: true,
    defaultInstanceDevMode: false,
    defaultSyncAdmin: true,
    defaultAutoVacuum: true,
    supportEmail: envString('PH_SUPPORT_EMAIL'),
    maintenanceMessage: '',
    notes: '',
  }
}

const parseSettingsValue = (raw: unknown): Partial<OperatorSettings> => {
  if (!raw) return {}
  let parsed = raw
  if (typeof raw === 'string') {
    try {
      parsed = JSON.parse(raw)
    } catch {
      return {}
    }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
  return parsed as Partial<OperatorSettings>
}

export const readOperatorSettings = (app: core.App = $app): OperatorSettings => {
  const defaults = defaultOperatorSettings()
  try {
    const record = app.findFirstRecordByData('settings', 'name', OPERATOR_SETTINGS_NAME)
    return normalizeOperatorSettings({
      ...defaults,
      ...parseSettingsValue(record.getString('value') || record.get('value')),
    })
  } catch {
    return defaults
  }
}

export const writeOperatorSettings = (settings: OperatorSettings, app: core.App = $app) => {
  const collection = app.findCollectionByNameOrId('settings')
  const normalized = normalizeOperatorSettings(settings)
  const record = (() => {
    try {
      return app.findFirstRecordByData('settings', 'name', OPERATOR_SETTINGS_NAME)
    } catch {
      const newRecord = new Record(collection)
      newRecord.set('name', OPERATOR_SETTINGS_NAME)
      return newRecord
    }
  })()

  record.set('value', JSON.stringify(normalized))
  app.save(record)
  applyOperatorMailSettings(normalized, app)
  return normalized
}

export const applyOperatorMailSettings = (settings: OperatorSettings, app: core.App = $app) => {
  const normalized = normalizeOperatorSettings(settings)
  const appSettings = app.settings()

  appSettings.smtp = {
    ...appSettings.smtp,
    enabled: normalized.smtp.enabled,
    host: normalized.smtp.host,
    port: normalized.smtp.port,
    username: normalized.smtp.username,
    password: normalized.smtp.password,
    authMethod: normalized.smtp.authMethod,
    tls: normalized.smtp.tls,
    localName: normalized.smtp.localName,
  }
  appSettings.meta = {
    ...appSettings.meta,
    senderName: normalized.smtp.senderName || appSettings.meta.senderName,
    senderAddress: normalized.smtp.senderAddress || appSettings.meta.senderAddress,
  }

  app.save(appSettings)
}

export const normalizeOperatorSettings = (value: Partial<OperatorSettings>): OperatorSettings => {
  const defaults = defaultOperatorSettings()
  const defaultSubscription = `${value.defaultSubscription || defaults.defaultSubscription}`

  return {
    publicSignupEnabled: !!value.publicSignupEnabled,
    autoVerifyUsers: !!value.autoVerifyUsers,
    defaultUserQuota: Math.max(0, Math.floor(Number(value.defaultUserQuota ?? defaults.defaultUserQuota) || 0)),
    defaultSubscription: ['free', 'premium', 'founder', 'flounder', 'legacy'].includes(defaultSubscription)
      ? (defaultSubscription as OperatorSettings['defaultSubscription'])
      : defaults.defaultSubscription,
    serverTimezone: normalizeServerTimezone(value.serverTimezone, defaults.serverTimezone),
    backupS3: normalizeBackupS3Settings(value.backupS3 || defaults.backupS3),
    smtp: normalizeSMTPSettings(value.smtp || defaults.smtp),
    defaultInstancePower: value.defaultInstancePower ?? defaults.defaultInstancePower,
    defaultInstanceDevMode: value.defaultInstanceDevMode ?? defaults.defaultInstanceDevMode,
    defaultSyncAdmin: value.defaultSyncAdmin ?? defaults.defaultSyncAdmin,
    defaultAutoVacuum: value.defaultAutoVacuum ?? defaults.defaultAutoVacuum,
    supportEmail: `${value.supportEmail || ''}`.trim(),
    maintenanceMessage: `${value.maintenanceMessage || ''}`.trim(),
    notes: `${value.notes || ''}`.trim(),
  }
}
