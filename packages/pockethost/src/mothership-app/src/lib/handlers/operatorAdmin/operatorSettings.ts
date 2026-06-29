export type OperatorSettings = {
  publicSignupEnabled: boolean
  autoVerifyUsers: boolean
  defaultUserQuota: number
  defaultSubscription: 'free' | 'premium' | 'founder' | 'flounder' | 'legacy'
  serverTimezone: string
  backupS3: OperatorBackupS3Settings
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

export type PublicOperatorSettings = Omit<OperatorSettings, 'backupS3'> & {
  backupS3: PublicOperatorBackupS3Settings
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

export const serializeOperatorSettings = (settings: OperatorSettings): PublicOperatorSettings => {
  const { secretAccessKey, ...backupS3 } = settings.backupS3
  return {
    ...settings,
    backupS3: {
      ...backupS3,
      hasSecretAccessKey: !!secretAccessKey,
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
    return normalizeOperatorSettings({ ...defaults, ...parseSettingsValue(record.getString('value') || record.get('value')) })
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
  return normalized
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
    defaultInstancePower: value.defaultInstancePower ?? defaults.defaultInstancePower,
    defaultInstanceDevMode: value.defaultInstanceDevMode ?? defaults.defaultInstanceDevMode,
    defaultSyncAdmin: value.defaultSyncAdmin ?? defaults.defaultSyncAdmin,
    defaultAutoVacuum: value.defaultAutoVacuum ?? defaults.defaultAutoVacuum,
    supportEmail: `${value.supportEmail || ''}`.trim(),
    maintenanceMessage: `${value.maintenanceMessage || ''}`.trim(),
    notes: `${value.notes || ''}`.trim(),
  }
}
