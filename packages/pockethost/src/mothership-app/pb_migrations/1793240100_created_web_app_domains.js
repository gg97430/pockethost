/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_app_domains',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'host', type: 'text', required: true, max: 253 },
          { name: 'status', type: 'text', required: true, max: 40 },
          { name: 'ownershipToken', type: 'text', required: true, max: 100, hidden: true },
          { name: 'lastError', type: 'text', max: 1000 },
          { name: 'certificateExpiresAt', type: 'date' },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_app_domains_host ON web_app_domains (host)',
          'CREATE INDEX idx_web_app_domains_app ON web_app_domains (appId)',
        ],
      })
    )
  },
  (app) => {
    app.delete(app.findCollectionByNameOrId('web_app_domains'))
  }
)
