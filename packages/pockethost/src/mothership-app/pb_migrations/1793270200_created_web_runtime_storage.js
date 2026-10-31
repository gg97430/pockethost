/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
      updated = { name: 'updated', type: 'autodate', onCreate: false, onUpdate: true }
    for (const definition of [
      {
        name: 'web_runtime_profiles',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'profile', type: 'json', maxSize: 100000 },
          { name: 'registryEncrypted', type: 'text', hidden: true, max: 20000 },
          { name: 'revision', type: 'text', max: 24 },
          created,
          updated,
        ],
        indexes: ['CREATE UNIQUE INDEX idx_web_runtime_app ON web_runtime_profiles (appId)'],
      },
      {
        name: 'web_storage_settings',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'volumes', type: 'json', maxSize: 10000 },
          { name: 'retention', type: 'number', min: 1, max: 14, onlyInt: true },
          { name: 'revision', type: 'text', max: 24 },
          created,
          updated,
        ],
        indexes: ['CREATE UNIQUE INDEX idx_web_storage_app ON web_storage_settings (appId)'],
      },
      {
        name: 'web_storage_jobs',
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'name', type: 'text', required: true, max: 32 },
          { name: 'operation', type: 'text', max: 30 },
          { name: 'status', type: 'text', max: 30 },
          { name: 'snapshotEncrypted', type: 'text', hidden: true, max: 100000 },
          { name: 'leaseHash', type: 'text', hidden: true, max: 64 },
          { name: 'leaseAt', type: 'date' },
          { name: 'startedAt', type: 'date' },
          { name: 'finishedAt', type: 'date' },
          { name: 'error', type: 'text', max: 500 },
          { name: 'result', type: 'json', maxSize: 10000 },
          created,
          updated,
        ],
        indexes: [
          'CREATE INDEX idx_web_storage_jobs_app ON web_storage_jobs (appId,created DESC)',
          'CREATE INDEX idx_web_storage_jobs_status ON web_storage_jobs (status,created)',
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
    for (const name of ['web_storage_jobs', 'web_storage_settings', 'web_runtime_profiles'])
      app.delete(app.findCollectionByNameOrId(name))
  }
)
