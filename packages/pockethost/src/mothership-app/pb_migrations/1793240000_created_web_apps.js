/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false }
    const updated = { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true }
    for (const definition of [
      {
        name: 'web_apps',
        fields: [
          { name: 'name', type: 'text', required: true, max: 80 },
          { name: 'uid', type: 'text', required: true, max: 15 },
          { name: 'config', type: 'json', maxSize: 10000 },
          { name: 'envEncrypted', type: 'text', hidden: true, max: 100000 },
          { name: 'activeDeployment', type: 'text', max: 15 },
          { name: 'desiredCommit', type: 'text', max: 64 },
          { name: 'status', type: 'text', max: 30 },
          { name: 'lastError', type: 'text', max: 1000 },
          created,
          updated,
        ],
      },
      {
        name: 'web_deployments',
        fields: [
          { name: 'appId', type: 'text', required: true, max: 15 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'action', type: 'text', required: true, max: 30 },
          { name: 'commit', type: 'text', max: 64 },
          { name: 'status', type: 'text', required: true, max: 30 },
          { name: 'stage', type: 'text', max: 100 },
          { name: 'error', type: 'text', max: 1000 },
          { name: 'snapshotEncrypted', type: 'text', hidden: true, max: 250000 },
          { name: 'result', type: 'json', maxSize: 10000 },
          { name: 'startedAt', type: 'date' },
          { name: 'finishedAt', type: 'date' },
          created,
          updated,
        ],
        indexes: [
          'CREATE INDEX idx_web_deployments_app ON web_deployments (appId,created DESC)',
          'CREATE INDEX idx_web_deployments_status ON web_deployments (status,created)',
        ],
      },
      {
        name: 'web_app_settings',
        fields: [
          { name: 'key', type: 'text', required: true, max: 100 },
          { name: 'valueEncrypted', type: 'text', hidden: true, max: 100000 },
          created,
          updated,
        ],
        indexes: ['CREATE UNIQUE INDEX idx_web_app_settings_key ON web_app_settings (key)'],
      },
      {
        name: 'web_webhook_deliveries',
        fields: [{ name: 'deliveryId', type: 'text', required: true, max: 100 }, created],
        indexes: ['CREATE UNIQUE INDEX idx_web_webhook_delivery ON web_webhook_deliveries (deliveryId)'],
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
    for (const n of ['web_webhook_deliveries', 'web_app_settings', 'web_deployments', 'web_apps'])
      app.delete(app.findCollectionByNameOrId(n))
  }
)
