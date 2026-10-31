/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const collection = new Collection({
      name: 'platform_monitoring_settings',
      type: 'base',
      listRule: null,
      viewRule: null,
      createRule: null,
      updateRule: null,
      deleteRule: null,
      fields: [
        { type: 'text', name: 'key', required: true, max: 40 },
        { type: 'bool', name: 'emailEnabled' },
        { type: 'json', name: 'emailRecipients', maxSize: 6000 },
        { type: 'bool', name: 'discordEnabled' },
        { type: 'text', name: 'discordWebhookEncrypted', max: 6000 },
        { type: 'text', name: 'revision', max: 40 },
        { type: 'text', name: 'updatedBy', max: 15 },
        { type: 'autodate', name: 'updated', onCreate: true, onUpdate: true },
      ],
      indexes: ['CREATE UNIQUE INDEX idx_platform_monitoring_key ON platform_monitoring_settings (key)'],
    })
    app.save(collection)
  },
  (app) => app.delete(app.findCollectionByNameOrId('platform_monitoring_settings'))
)
