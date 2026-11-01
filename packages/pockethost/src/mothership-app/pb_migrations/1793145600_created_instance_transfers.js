/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const backups = app.findCollectionByNameOrId('instance_backups')
    const kind = backups.fields.getByName('kind')
    if (!kind.values.includes('transfer')) kind.values = [...kind.values, 'transfer']
    app.save(backups)

    const collection = new Collection({
      id: 'insttransferv10',
      name: 'instance_transfers',
      type: 'base',
      listRule: null,
      viewRule: null,
      createRule: null,
      updateRule: null,
      deleteRule: null,
      fields: [
        { name: 'direction', type: 'select', required: true, maxSelect: 1, values: ['export', 'import'] },
        {
          name: 'status',
          type: 'select',
          required: true,
          maxSelect: 1,
          values: ['queued', 'preparing', 'ready', 'downloading', 'restoring', 'complete', 'failed', 'revoked'],
        },
        { name: 'user', type: 'relation', required: true, maxSelect: 1, collectionId: 'systemprofiles0' },
        {
          name: 'instance',
          type: 'relation',
          required: true,
          maxSelect: 1,
          collectionId: 'etae8tuiaxl6xfv',
          cascadeDelete: true,
        },
        { name: 'backup', type: 'relation', maxSelect: 1, collectionId: 'instbackupsv100' },
        { name: 'expiresAt', type: 'date', required: true },
        { name: 'codeHash', type: 'text', hidden: true, max: 64 },
        { name: 'claimHash', type: 'text', hidden: true, max: 64 },
        { name: 'sourceUrl', type: 'text', max: 1000 },
        { name: 'remoteId', type: 'text', max: 15 },
        { name: 'metadata', type: 'json', hidden: true, maxSize: 1048576 },
        { name: 'error', type: 'text', max: 2000 },
        { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
        { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true },
      ],
      indexes: [
        'CREATE INDEX idx_instance_transfers_queue ON instance_transfers (status, created)',
        'CREATE INDEX idx_instance_transfers_instance ON instance_transfers (instance, direction, status)',
      ],
    })
    app.save(collection)
  },
  (app) => {
    app.delete(app.findCollectionByNameOrId('instance_transfers'))
    const backups = app.findCollectionByNameOrId('instance_backups')
    const kind = backups.fields.getByName('kind')
    kind.values = kind.values.filter((value) => value !== 'transfer')
    app.save(backups)
  }
)
