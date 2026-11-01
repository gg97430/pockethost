/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false }
    const updated = { name: 'updated', type: 'autodate', onCreate: false, onUpdate: true }
    for (const definition of [
      {
        name: 'web_app_health',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'enabled', type: 'bool' },
          { name: 'path', type: 'text', required: true, max: 512 },
          { name: 'intervalSeconds', type: 'number', required: true, min: 60, max: 3600, onlyInt: true },
          { name: 'timeoutSeconds', type: 'number', required: true, min: 1, max: 10, onlyInt: true },
          { name: 'failureThreshold', type: 'number', required: true, min: 1, max: 10, onlyInt: true },
          { name: 'revision', type: 'text', required: true, min: 24, max: 24 },
          { name: 'deploymentId', type: 'text', max: 15 },
          { name: 'containerId', type: 'text', max: 64, hidden: true },
          { name: 'status', type: 'text', max: 30 },
          { name: 'consecutiveFailures', type: 'number', min: 0, max: 100000, onlyInt: true },
          { name: 'lastCheckedAt', type: 'date' },
          { name: 'statusSince', type: 'date' },
          { name: 'httpStatus', type: 'number', min: 0, max: 599, onlyInt: true },
          { name: 'durationMs', type: 'number', min: 0, max: 120000, onlyInt: true },
          { name: 'lastResult', type: 'text', max: 40 },
          created,
          updated,
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_health_app ON web_app_health (appId)',
          'CREATE INDEX idx_web_health_enabled ON web_app_health (enabled,appId)',
        ],
      },
      {
        name: 'web_app_health_events',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'deploymentId', type: 'text', max: 15 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'fromStatus', type: 'text', max: 30 },
          { name: 'status', type: 'text', required: true, max: 30 },
          { name: 'reason', type: 'text', required: true, max: 40 },
          { name: 'checkedAt', type: 'date', required: true },
          { name: 'httpStatus', type: 'number', min: 0, max: 599, onlyInt: true },
          { name: 'durationMs', type: 'number', min: 0, max: 120000, onlyInt: true },
          { name: 'consecutiveFailures', type: 'number', min: 0, max: 100000, onlyInt: true },
          created,
        ],
        indexes: [
          'CREATE INDEX idx_web_health_events_app ON web_app_health_events (appId,created DESC)',
          'CREATE INDEX idx_web_health_events_retention ON web_app_health_events (checkedAt)',
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
    for (const name of ['web_app_health_events', 'web_app_health']) app.delete(app.findCollectionByNameOrId(name))
  }
)
