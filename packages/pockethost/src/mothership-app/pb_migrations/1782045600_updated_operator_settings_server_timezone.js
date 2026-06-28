/// <reference path="../pb_data/types.d.ts" />

const DEFAULT_SERVER_TIMEZONE = 'Indian/Reunion'

const parseSettingsValue = (raw) => {
  if (!raw) return {}
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw)
    } catch {
      return {}
    }
  }
  return raw
}

migrate(
  (app) => {
    const record = (() => {
      try {
        return app.findFirstRecordByData('settings', 'name', 'operator_settings')
      } catch {
        return null
      }
    })()

    if (!record) return

    const settings = parseSettingsValue(record.get('value'))
    if (settings.serverTimezone) return

    record.set('value', {
      ...settings,
      serverTimezone: DEFAULT_SERVER_TIMEZONE,
    })
    app.save(record)
  },
  (app) => {
    const record = (() => {
      try {
        return app.findFirstRecordByData('settings', 'name', 'operator_settings')
      } catch {
        return null
      }
    })()

    if (!record) return

    const settings = parseSettingsValue(record.get('value'))
    delete settings.serverTimezone
    record.set('value', settings)
    app.save(record)
  }
)
