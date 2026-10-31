/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_terminal_sessions',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'appId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'actorId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'deploymentId', type: 'text', required: true, min: 15, max: 15 },
          { name: 'containerName', type: 'text', required: true, max: 80 },
          { name: 'containerId', type: 'text', max: 64, hidden: true },
          { name: 'grantHash', type: 'text', hidden: true, max: 64 },
          { name: 'leaseHash', type: 'text', hidden: true, max: 64 },
          { name: 'shell', type: 'text', required: true, max: 20 },
          { name: 'status', type: 'text', required: true, max: 30 },
          { name: 'expiresAt', type: 'date', required: true },
          { name: 'absoluteExpiresAt', type: 'date', required: true },
          { name: 'lastSeenAt', type: 'date' },
          { name: 'openedAt', type: 'date' },
          { name: 'closedAt', type: 'date' },
          { name: 'reason', type: 'text', max: 40 },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: [
          "CREATE UNIQUE INDEX idx_web_terminal_grant ON web_terminal_sessions (grantHash) WHERE grantHash != ''",
          'CREATE INDEX idx_web_terminal_actor ON web_terminal_sessions (actorId,status,absoluteExpiresAt)',
          'CREATE INDEX idx_web_terminal_app ON web_terminal_sessions (appId,created DESC)',
        ],
      })
    )
  },
  (app) => app.delete(app.findCollectionByNameOrId('web_terminal_sessions'))
)
