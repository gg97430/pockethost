/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
      updated = { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true }
    for (const definition of [
      {
        name: 'web_app_checks',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'actorId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'revision', type: 'text', required: true, max: 32 },
          { name: 'config', type: 'json', required: true, maxSize: 100000 },
          { name: 'secretsEncrypted', type: 'text', hidden: true, max: 100000 },
          { name: 'nextRunAt', type: 'date' },
          { name: 'summary', type: 'json', maxSize: 100000 },
          created,
          updated,
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_app_checks_app ON web_app_checks (appId)',
          'CREATE INDEX idx_web_app_checks_due ON web_app_checks (nextRunAt)',
        ],
      },
      {
        name: 'web_app_check_runs',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'settingsId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'journeyId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'journeyName', type: 'text', required: true, max: 80 },
          { name: 'actorId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'trigger', type: 'text', required: true, max: 10 },
          { name: 'status', type: 'text', required: true, max: 20 },
          { name: 'scheduledAt', type: 'date' },
          { name: 'startedAt', type: 'date' },
          { name: 'finishedAt', type: 'date' },
          { name: 'deadline', type: 'date' },
          { name: 'leaseHash', type: 'text', hidden: true, max: 64 },
          { name: 'snapshotEncrypted', type: 'text', hidden: true, max: 150000 },
          { name: 'result', type: 'json', maxSize: 65536 },
          created,
          updated,
        ],
        indexes: [
          'CREATE INDEX idx_web_check_active ON web_app_check_runs (status,deadline)',
          'CREATE INDEX idx_web_check_app ON web_app_check_runs (appId,created DESC)',
          'CREATE INDEX idx_web_check_journey ON web_app_check_runs (journeyId,status)',
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
  () => {
    /* Additive settings/history are retained on code rollback. */
  }
)
