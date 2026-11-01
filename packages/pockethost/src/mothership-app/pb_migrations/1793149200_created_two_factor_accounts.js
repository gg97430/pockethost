/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    // Console/dashboard bootstrap can initialize this table before application migrations run.
    try {
      app.findCollectionByNameOrId('ph_two_factor')
      return
    } catch {}
    const collection = new Collection({
      name: 'ph_two_factor',
      type: 'base',
      listRule: null,
      viewRule: null,
      createRule: null,
      updateRule: null,
      deleteRule: null,
      fields: [
        { name: 'authCollection', type: 'text', required: true, max: 100 },
        { name: 'authRecord', type: 'text', required: true, max: 100 },
        { name: 'enabled', type: 'bool' },
        { name: 'secret', type: 'text', hidden: true, max: 1000 },
        { name: 'pendingSecret', type: 'text', hidden: true, max: 1000 },
        { name: 'nonce', type: 'text', hidden: true, max: 100 },
        { name: 'recoveryHashes', type: 'json', hidden: true, maxSize: 10000 },
        { name: 'lastCounter', type: 'number', onlyInt: true },
        { name: 'failures', type: 'number', onlyInt: true },
        { name: 'lockedUntil', type: 'date' },
        { name: 'pendingUntil', type: 'date' },
      ],
      indexes: ['CREATE UNIQUE INDEX idx_ph_two_factor_account ON ph_two_factor (authCollection, authRecord)'],
    })
    app.save(collection)
  },
  (app) => app.delete(app.findCollectionByNameOrId('ph_two_factor'))
)
