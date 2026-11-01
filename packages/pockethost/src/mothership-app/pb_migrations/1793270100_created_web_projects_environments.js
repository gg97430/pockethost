migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false }
    const updated = { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true }
    for (const definition of [
      {
        name: 'web_projects',
        fields: [
          { name: 'name', type: 'text', required: true, max: 80 },
          { name: 'repository', type: 'text', required: true, max: 250 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'previewEnabled', type: 'bool' },
          { name: 'previewSourceAppId', type: 'text', max: 15 },
          { name: 'previewMax', type: 'number', min: 1, max: 5, onlyInt: true },
          { name: 'previewTtlHours', type: 'number', min: 1, max: 72, onlyInt: true },
          created,
          updated,
        ],
      },
      {
        name: 'web_app_environments',
        fields: [
          { name: 'projectId', type: 'text', required: true, max: 15 },
          { name: 'appId', type: 'text', max: 15 },
          { name: 'environment', type: 'text', required: true, max: 20 },
          { name: 'slug', type: 'text', required: true, max: 60 },
          { name: 'sourceAppId', type: 'text', max: 15 },
          { name: 'previewNumber', type: 'number', min: 0, onlyInt: true },
          { name: 'expiresAt', type: 'date' },
          { name: 'state', type: 'text', required: true, max: 20 },
          created,
          updated,
        ],
        indexes: [
          "CREATE UNIQUE INDEX idx_web_environment_app ON web_app_environments (appId) WHERE appId != ''",
          'CREATE UNIQUE INDEX idx_web_project_environment_slug ON web_app_environments (projectId,slug)',
          'CREATE INDEX idx_web_preview_expiry ON web_app_environments (environment,state,expiresAt)',
        ],
      },
      {
        name: 'web_project_variables',
        fields: [
          { name: 'projectId', type: 'text', required: true, max: 15 },
          { name: 'environment', type: 'text', required: true, max: 20 },
          { name: 'valueEncrypted', type: 'text', hidden: true, max: 100000 },
          created,
          updated,
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_project_variable_scope ON web_project_variables (projectId,environment)',
        ],
      },
      {
        name: 'web_project_audit',
        fields: [
          { name: 'projectId', type: 'text', required: true, max: 15 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'action', type: 'text', required: true, max: 60 },
          { name: 'details', type: 'json', maxSize: 20000 },
          created,
        ],
        indexes: ['CREATE INDEX idx_web_project_audit ON web_project_audit (projectId,created DESC)'],
      },
      {
        name: 'web_preview_deliveries',
        fields: [
          { name: 'deliveryId', type: 'text', required: true, max: 100 },
          { name: 'repository', type: 'text', max: 250 },
          { name: 'previewNumber', type: 'number', min: 0, onlyInt: true },
          { name: 'status', type: 'text', max: 30 },
          { name: 'note', type: 'text', max: 500 },
          created,
        ],
        indexes: ['CREATE UNIQUE INDEX idx_web_preview_delivery ON web_preview_deliveries (deliveryId)'],
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
    for (const name of [
      'web_preview_deliveries',
      'web_project_audit',
      'web_project_variables',
      'web_app_environments',
      'web_projects',
    ])
      app.delete(app.findCollectionByNameOrId(name))
  }
)
