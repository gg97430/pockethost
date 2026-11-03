/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_project_recovery_runs',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'projectId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'actorId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'operation', type: 'text', required: true, max: 20 },
          { name: 'status', type: 'text', required: true, max: 20 },
          { name: 'stage', type: 'text', max: 30 },
          { name: 'revision', type: 'text', required: true, max: 64 },
          { name: 'snapshotEncrypted', type: 'text', hidden: true, max: 4000000 },
          { name: 'leaseHash', type: 'text', hidden: true, max: 64 },
          { name: 'deadline', type: 'date' },
          { name: 'startedAt', type: 'date' },
          { name: 'finishedAt', type: 'date' },
          { name: 'result', type: 'json', maxSize: 100000 },
          { name: 'errorCode', type: 'text', max: 60 },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: [
          'CREATE INDEX idx_project_recovery_active ON web_project_recovery_runs(status,deadline)',
          'CREATE INDEX idx_project_recovery_project ON web_project_recovery_runs(projectId,created DESC)',
        ],
      })
    )
  },
  () => {
    /* Keep additive history on code rollback. */
  }
)
