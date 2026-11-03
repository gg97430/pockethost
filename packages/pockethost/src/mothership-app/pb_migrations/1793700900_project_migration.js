/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    for (const name of ['instances', 'web_apps', 'web_projects']) {
      const collection = app.findCollectionByNameOrId(name)
      collection.fields.add(new TextField({ name: 'migrationLock', max: 15, hidden: true }))
      app.save(collection)
    }
    app.save(
      new Collection({
        name: 'web_project_migrations',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'projectName', type: 'text', required: true, max: 80 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'ownerId', type: 'text', required: true, max: 15 },
          { name: 'source', type: 'text', required: true, max: 4 },
          { name: 'target', type: 'text', required: true, max: 4 },
          { name: 'revision', type: 'text', required: true, max: 64 },
          { name: 'status', type: 'text', required: true, max: 30 },
          { name: 'planEncrypted', type: 'text', required: true, hidden: true, max: 6000000 },
          { name: 'publicPlan', type: 'json', maxSize: 1000000 },
          { name: 'importedRecords', type: 'json', hidden: true, maxSize: 300000 },
          { name: 'expiresAt', type: 'date' },
          { name: 'approvedAt', type: 'date' },
          { name: 'importedAt', type: 'date' },
          { name: 'releasedAt', type: 'date' },
          { name: 'dnsRevision', type: 'text', max: 64 },
          { name: 'dnsPreview', type: 'json', maxSize: 500000 },
          { name: 'dnsApprovedAt', type: 'date' },
          { name: 'created', type: 'autodate', onCreate: true },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: ['CREATE INDEX idx_project_migration_status ON web_project_migrations(status,created DESC)'],
      })
    )
  },
  () => {
    /* additive locks and audited plans remain on code rollback */
  }
)
