migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_environment_audit',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'appId', type: 'text', required: true, max: 15 },
          { name: 'actorId', type: 'text', required: true, max: 15 },
          { name: 'action', type: 'text', required: true, max: 30 },
          { name: 'details', type: 'json', maxSize: 10000 },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
        ],
        indexes: ['CREATE INDEX idx_web_environment_audit_app ON web_environment_audit (appId,created DESC)'],
      })
    )
  },
  (app) => app.delete(app.findCollectionByNameOrId('web_environment_audit'))
)
