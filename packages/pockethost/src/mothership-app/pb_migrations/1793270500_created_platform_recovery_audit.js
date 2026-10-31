migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_platform_recovery_audit',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'actorId', type: 'text', required: true, max: 15 },
          { name: 'action', type: 'select', required: true, values: ['settings', 'capture', 'drill'], maxSelect: 1 },
          { name: 'jobId', type: 'text', max: 128 },
          { name: 'details', type: 'json', maxSize: 5000 },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
        ],
        indexes: ['CREATE INDEX idx_web_platform_recovery_audit_created ON web_platform_recovery_audit (created DESC)'],
      })
    )
  },
  (app) => app.delete(app.findCollectionByNameOrId('web_platform_recovery_audit'))
)
