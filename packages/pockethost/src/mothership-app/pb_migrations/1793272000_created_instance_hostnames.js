/// <reference path="../src/types/types.d.ts" />
// Platform-assigned addresses survive a change of host without renaming the client-facing database URLs.
migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('instances')
    if (!collection.fields.getByName('publicHostname'))
      collection.fields.add(new TextField({ name: 'publicHostname', max: 253 }))
    if (!collection.fields.getByName('legacyHostnames'))
      collection.fields.add(new JSONField({ name: 'legacyHostnames', maxSize: 8192 }))
    app.save(collection)
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('instances')
    collection.fields.removeByName('publicHostname')
    collection.fields.removeByName('legacyHostnames')
    app.save(collection)
  }
)
