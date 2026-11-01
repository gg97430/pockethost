/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('web_projects')
    collection.fields.add(new JSONField({ name: 'limits', maxSize: 4096 }))
    app.save(collection)
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('web_projects')
    collection.fields.removeByName('limits')
    app.save(collection)
  }
)
