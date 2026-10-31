/// <reference path="../src/types/types.d.ts" />
// Applications web : source « Dépôt Git (clé de déploiement) ». Une ligne par dépôt (canonical unique) : GitHub
// refuse la même clé de déploiement sur deux dépôts, les applications et environnements du même dépôt la partagent.
// Accès uniquement via les routes personnalisées (règles nulles) ; clé privée, secret de webhook et scan en attente
// sont chiffrés avec WEB_APPS_MASTER_KEY et masqués. web_apps.config reçoit seulement source/gitSourceId/repository.
migrate(
  (app) => {
    app.save(
      new Collection({
        name: 'web_git_sources',
        type: 'base',
        listRule: null,
        viewRule: null,
        createRule: null,
        updateRule: null,
        deleteRule: null,
        fields: [
          { name: 'host', type: 'text', required: true, max: 253 },
          { name: 'port', type: 'number', required: true, min: 1, max: 65535, onlyInt: true },
          { name: 'sshUser', type: 'text', required: true, max: 32 },
          { name: 'path', type: 'text', required: true, max: 250 },
          { name: 'canonical', type: 'text', required: true, max: 250 },
          { name: 'projectId', type: 'text', max: 15 },
          { name: 'publicKey', type: 'text', max: 200 },
          { name: 'fingerprint', type: 'text', max: 64 },
          { name: 'privateKeyEncrypted', type: 'text', hidden: true, max: 20000 },
          { name: 'hostKeys', type: 'json', maxSize: 20000 },
          { name: 'hostKeysConfirmedAt', type: 'date' },
          { name: 'hostKeysConfirmedBy', type: 'text', max: 15 },
          { name: 'pendingHostKeysEncrypted', type: 'text', hidden: true, max: 40000 },
          { name: 'webhookId', type: 'text', required: true, max: 24 },
          { name: 'webhookSecretEncrypted', type: 'text', hidden: true, max: 1000 },
          { name: 'lastCheckAt', type: 'date' },
          { name: 'lastCheckStatus', type: 'text', max: 30 },
          { name: 'lastError', type: 'text', max: 500 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'rotatedAt', type: 'date' },
          { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
          { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_git_source_canonical ON web_git_sources (canonical)',
          'CREATE UNIQUE INDEX idx_web_git_source_webhook ON web_git_sources (webhookId)',
          'CREATE INDEX idx_web_git_source_project ON web_git_sources (projectId)',
        ],
      })
    )
  },
  (app) => app.delete(app.findCollectionByNameOrId('web_git_sources'))
)
