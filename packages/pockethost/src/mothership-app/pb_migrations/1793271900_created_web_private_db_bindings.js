/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_private_db_bindings',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'appId', type: 'text', required: true, max: 15 },
          { name: 'instanceId', type: 'text', required: true, max: 15 },
          { name: 'actorId', type: 'text', required: true, max: 15 },
          { name: 'port', type: 'number', required: true, min: 1024, max: 65535, onlyInt: true },
          { name: 'enabled', type: 'bool' },
          { name: 'tokenEncrypted', type: 'text', max: 500, hidden: true },
          { name: 'revision', type: 'text', required: true, max: 24, hidden: true },
          { name: 'status', type: 'select', maxSelect: 1, values: ['pending', 'ready', 'unavailable'] },
          { name: 'errorCode', type: 'text', max: 40 },
          { name: 'lastSeenAt', type: 'date' },
          { name: 'serviceCount', type: 'number', min: 0, max: 8, onlyInt: true },
          { name: 'needsRestart', type: 'bool' },
          { name: 'created', type: 'autodate', onCreate: true },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_private_db_port ON web_private_db_bindings (appId,port)',
          'CREATE UNIQUE INDEX idx_web_private_db_target ON web_private_db_bindings (appId,instanceId)',
          'CREATE INDEX idx_web_private_db_active ON web_private_db_bindings (enabled,appId)',
          'CREATE INDEX idx_web_private_db_instance ON web_private_db_bindings (instanceId)',
        ],
      })
    )
  },
  (app) => app.delete(app.findCollectionByNameOrId('web_private_db_bindings'))
)
