/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false }
    const updated = { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true }
    for (const definition of [
      {
        name: 'web_scheduled_tasks',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'actorId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'name', type: 'text', required: true, max: 80 },
          { name: 'commandEncrypted', type: 'text', required: true, hidden: true, max: 20000 },
          { name: 'schedule', type: 'text', required: true, max: 100 },
          { name: 'enabled', type: 'bool' },
          { name: 'timeoutSeconds', type: 'number', required: true, min: 1, max: 900, onlyInt: true },
          { name: 'nextRunAt', type: 'date' },
          { name: 'lastScheduledAt', type: 'date' },
          created,
          updated,
        ],
        indexes: [
          'CREATE INDEX idx_web_scheduled_app ON web_scheduled_tasks (appId,created)',
          'CREATE INDEX idx_web_scheduled_due ON web_scheduled_tasks (enabled,nextRunAt)',
        ],
      },
      {
        name: 'web_task_runs',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'taskId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'actorId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'taskName', type: 'text', required: true, max: 80 },
          { name: 'occurrence', type: 'text', required: true, max: 80 },
          { name: 'trigger', type: 'text', required: true, max: 10 },
          { name: 'status', type: 'text', required: true, max: 20 },
          { name: 'deploymentId', type: 'text', max: 15 },
          { name: 'snapshotEncrypted', type: 'text', hidden: true, max: 250000 },
          { name: 'leaseHash', type: 'text', hidden: true, max: 64 },
          { name: 'timeoutSeconds', type: 'number', required: true, min: 1, max: 900, onlyInt: true },
          { name: 'scheduledAt', type: 'date' },
          { name: 'startedAt', type: 'date' },
          { name: 'deadline', type: 'date' },
          { name: 'finishedAt', type: 'date' },
          { name: 'durationMs', type: 'number', min: 0 },
          { name: 'exitCode', type: 'number', onlyInt: true, min: -1, max: 255 },
          { name: 'log', type: 'text', max: 200000 },
          { name: 'truncated', type: 'bool' },
          { name: 'error', type: 'text', max: 300 },
          created,
          updated,
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_task_occurrence ON web_task_runs (taskId,occurrence)',
          'CREATE INDEX idx_web_task_history ON web_task_runs (appId,created DESC)',
          'CREATE INDEX idx_web_task_active ON web_task_runs (status,deadline)',
          'CREATE INDEX idx_web_task_overlap ON web_task_runs (taskId,status)',
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
    for (const name of ['web_task_runs', 'web_scheduled_tasks']) app.delete(app.findCollectionByNameOrId(name))
  }
)
