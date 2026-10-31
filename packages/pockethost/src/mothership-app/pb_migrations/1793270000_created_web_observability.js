/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
      updated = { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true }
    for (const definition of [
      {
        name: 'web_notification_settings',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'actorId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'configEncrypted', type: 'text', required: true, hidden: true, max: 30000 },
          { name: 'revision', type: 'text', required: true, min: 24, max: 24 },
          { name: 'enabled', type: 'bool' },
          { name: 'activatedAt', type: 'date' },
          { name: 'scanAfter', type: 'date' },
          { name: 'sourceCursors', type: 'json', hidden: true, maxSize: 3000 },
          { name: 'diskLow', type: 'bool' },
          created,
          updated,
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_notification_app ON web_notification_settings (appId)',
          'CREATE INDEX idx_web_notification_enabled ON web_notification_settings (enabled,scanAfter)',
        ],
      },
      {
        name: 'web_notification_deliveries',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'settingsId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'settingsRevision', type: 'text', required: true, min: 24, max: 24 },
          { name: 'actorId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'channel', type: 'text', required: true, max: 10 },
          { name: 'event', type: 'text', required: true, max: 40 },
          { name: 'eventKey', type: 'text', required: true, min: 64, max: 64 },
          { name: 'payload', type: 'json', maxSize: 12000 },
          { name: 'trigger', type: 'text', required: true, max: 10 },
          { name: 'status', type: 'text', required: true, max: 20 },
          { name: 'attempts', type: 'number', min: 0, max: 3, onlyInt: true },
          { name: 'nextAttemptAt', type: 'date' },
          { name: 'leaseAt', type: 'date' },
          { name: 'leaseHash', type: 'text', hidden: true, max: 64 },
          { name: 'sentAt', type: 'date' },
          { name: 'emailSentAt', type: 'date' },
          { name: 'httpStatus', type: 'number', min: 0, max: 599, onlyInt: true },
          { name: 'lastError', type: 'text', max: 300 },
          created,
          updated,
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_notification_dedupe ON web_notification_deliveries (appId,channel,eventKey)',
          'CREATE INDEX idx_web_notification_queue ON web_notification_deliveries (status,nextAttemptAt)',
          'CREATE INDEX idx_web_notification_history ON web_notification_deliveries (appId,created DESC)',
          'CREATE INDEX idx_web_notification_retention ON web_notification_deliveries (created)',
          'CREATE INDEX idx_web_notification_rate ON web_notification_deliveries (settingsId,channel,event,created DESC)',
        ],
      },
    ])
      app.save(
        new Collection({
          ...definition,
          type: 'base',
          listRule: null,
          viewRule: null,
          createRule: null,
          updateRule: null,
          deleteRule: null,
        })
      )
  },
  (app) => {
    for (const name of ['web_notification_deliveries', 'web_notification_settings'])
      app.delete(app.findCollectionByNameOrId(name))
  }
)
