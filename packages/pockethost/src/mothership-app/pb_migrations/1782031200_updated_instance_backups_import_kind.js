/// <reference path="../pb_data/types.d.ts" />
migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('instbackupsv100')
    const kind = collection.fields.getByName('kind')

    kind.values = ['manual', 'pre-restore', 'import']

    return app.save(collection)
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('instbackupsv100')
    const kind = collection.fields.getByName('kind')

    kind.values = ['manual', 'pre-restore']

    return app.save(collection)
  }
)
