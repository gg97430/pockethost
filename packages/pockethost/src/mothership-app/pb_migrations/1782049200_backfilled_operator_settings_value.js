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
    const collection = app.findCollectionByNameOrId('settings')
    const record = (() => {
      try {
        return app.findFirstRecordByData('settings', 'name', 'operator_settings')
      } catch {
        const next = new Record(collection)
        next.set('name', 'operator_settings')
        return next
      }
    })()

    const settings = parseSettingsValue(record.getString('value') || record.get('value'))
    record.set(
      'value',
      JSON.stringify({
        ...settings,
        serverTimezone: settings.serverTimezone || DEFAULT_SERVER_TIMEZONE,
      })
    )
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

    const settings = parseSettingsValue(record.getString('value') || record.get('value'))
    delete settings.serverTimezone
    record.set('value', JSON.stringify(settings))
    app.save(record)
  }
)
