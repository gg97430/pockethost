export type OperatorSettings = {
  publicSignupEnabled: boolean
  autoVerifyUsers: boolean
  defaultUserQuota: number
  defaultSubscription: 'free' | 'premium' | 'founder' | 'flounder' | 'legacy'
  serverTimezone: string
  defaultInstancePower: boolean
  defaultInstanceDevMode: boolean
  defaultSyncAdmin: boolean
  defaultAutoVacuum: boolean
  supportEmail: string
  maintenanceMessage: string
  notes: string
}

export const OPERATOR_SETTINGS_NAME = 'operator_settings'
export const DEFAULT_SERVER_TIMEZONE = 'Indian/Reunion'

const envBoolean = (name: string, fallback: boolean) => {
  const raw = `${process.env[name] || ''}`.trim().toLowerCase()
  if (!raw) return fallback
  return ['1', 'true', 'yes', 'on'].includes(raw)
}

const envNumber = (name: string, fallback: number) => {
  const value = Number(process.env[name] || '')
  if (!Number.isFinite(value) || value < 0) return fallback
  return value
}

export const normalizeServerTimezone = (value: unknown, fallback = DEFAULT_SERVER_TIMEZONE) => {
  const raw = `${value || ''}`.trim()
  if (!raw) return fallback
  if (['UTC', 'Local'].includes(raw)) return raw
  if (/^[A-Za-z_]+(?:\/[A-Za-z0-9._+-]+)+$/.test(raw)) return raw
  return fallback
}

export const defaultOperatorSettings = (): OperatorSettings => {
  const autoVerifyUsers = envBoolean('PH_AUTO_VERIFY_SIGNUPS', true)
  return {
    publicSignupEnabled: envBoolean('PH_PUBLIC_SIGNUP_ENABLED', false),
    autoVerifyUsers,
    defaultUserQuota: envNumber('PH_SIGNUP_SUBSCRIPTION_QUANTITY', autoVerifyUsers ? 250 : 0),
    defaultSubscription: 'free',
    serverTimezone: normalizeServerTimezone(process.env.PH_SERVER_TIMEZONE || DEFAULT_SERVER_TIMEZONE),
    defaultInstancePower: true,
    defaultInstanceDevMode: true,
    defaultSyncAdmin: true,
    defaultAutoVacuum: true,
    supportEmail: process.env.PH_SUPPORT_EMAIL || '',
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
    defaultInstancePower: value.defaultInstancePower ?? defaults.defaultInstancePower,
    defaultInstanceDevMode: value.defaultInstanceDevMode ?? defaults.defaultInstanceDevMode,
    defaultSyncAdmin: value.defaultSyncAdmin ?? defaults.defaultSyncAdmin,
    defaultAutoVacuum: value.defaultAutoVacuum ?? defaults.defaultAutoVacuum,
    supportEmail: `${value.supportEmail || ''}`.trim(),
    maintenanceMessage: `${value.maintenanceMessage || ''}`.trim(),
    notes: `${value.notes || ''}`.trim(),
  }
}
