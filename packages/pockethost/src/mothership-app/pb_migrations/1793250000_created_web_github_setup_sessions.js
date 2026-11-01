/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_github_setup_sessions',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'actorId', type: 'text', required: true, max: 15 },
          { name: 'manifestStateHash', type: 'text', required: true, hidden: true, max: 64 },
          { name: 'installStateHash', type: 'text', hidden: true, max: 64 },
          { name: 'baselineDigest', type: 'text', required: true, hidden: true, max: 64 },
          { name: 'status', type: 'text', required: true, max: 40 },
          { name: 'expiresAt', type: 'date', required: true },
          { name: 'pendingEncrypted', type: 'text', hidden: true, max: 100000 },
          { name: 'publicInfo', type: 'json', maxSize: 5000 },
          { name: 'lastError', type: 'text', max: 500 },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_github_setup_manifest ON web_github_setup_sessions (manifestStateHash)',
          "CREATE UNIQUE INDEX idx_web_github_setup_install ON web_github_setup_sessions (installStateHash) WHERE installStateHash != ''",
          'CREATE INDEX idx_web_github_setup_actor ON web_github_setup_sessions (actorId,expiresAt,status)',
        ],
      })
    )
  },
  (app) => app.delete(app.findCollectionByNameOrId('web_github_setup_sessions'))
)
