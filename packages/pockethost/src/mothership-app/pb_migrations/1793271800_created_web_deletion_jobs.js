/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    for (const name of ['web_apps', 'web_projects']) {
      const collection = app.findCollectionByNameOrId(name)
      collection.fields.add(new TextField({ name: 'deletionId', max: 15 }))
      app.save(collection)
    }
    app.save(
      new Collection({
        name: 'web_deletion_jobs',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'kind', type: 'select', required: true, maxSelect: 1, values: ['app', 'project'] },
          { name: 'targetId', type: 'text', required: true, max: 15 },
          { name: 'actorId', type: 'text', required: true, max: 15 },
          {
            name: 'status',
            type: 'select',
            required: true,
            maxSelect: 1,
            values: ['queued', 'running', 'failed', 'ready'],
          },
          { name: 'snapshot', type: 'json', maxSize: 4 * 1024 * 1024, hidden: true },
          { name: 'leaseHash', type: 'text', max: 64, hidden: true },
          { name: 'heartbeatAt', type: 'date' },
          { name: 'error', type: 'text', max: 1000 },
          { name: 'stage', type: 'text', max: 150 },
          { name: 'created', type: 'autodate', onCreate: true },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_deletion_target ON web_deletion_jobs (kind,targetId)',
          'CREATE INDEX idx_web_deletion_status ON web_deletion_jobs (status,created)',
        ],
      })
    )
  },
  (app) => {
    app.delete(app.findCollectionByNameOrId('web_deletion_jobs'))
    for (const name of ['web_apps', 'web_projects']) {
      const collection = app.findCollectionByNameOrId(name)
      collection.fields.removeByName('deletionId')
      app.save(collection)
    }
  }
)
