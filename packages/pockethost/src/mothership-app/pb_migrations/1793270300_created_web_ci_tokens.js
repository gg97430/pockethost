migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_ci_tokens',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'appId', type: 'text', required: true, max: 15 },
          { name: 'actorId', type: 'text', required: true, max: 15 },
          { name: 'name', type: 'text', required: true, max: 80 },
          { name: 'tokenHash', type: 'text', hidden: true, required: true, max: 64 },
          { name: 'imageRepository', type: 'text', required: true, max: 250 },
          { name: 'expiresAt', type: 'date', required: true },
          { name: 'revokedAt', type: 'date' },
          { name: 'lastUsedAt', type: 'date' },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
        ],
        indexes: ['CREATE INDEX idx_web_ci_tokens_app ON web_ci_tokens (appId,created DESC)'],
      })
    )
    app.save(
      new Collection({
        name: 'web_ci_deliveries',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'tokenId', type: 'text', required: true, max: 15 },
          { name: 'requestId', type: 'text', required: true, max: 100 },
          { name: 'payloadHash', type: 'text', required: true, max: 64 },
          { name: 'deploymentId', type: 'text', required: true, max: 15 },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
        ],
        indexes: ['CREATE UNIQUE INDEX idx_web_ci_delivery ON web_ci_deliveries (tokenId,requestId)'],
      })
    )
  },
  (app) => {
    app.delete(app.findCollectionByNameOrId('web_ci_deliveries'))
    app.delete(app.findCollectionByNameOrId('web_ci_tokens'))
  }
)
