/// <reference path="../src/types/types.d.ts" />
// Optional classification only: no ownership, API rules or runtime changes.
migrate(
  (app) => {
    const instances = app.findCollectionByNameOrId('instances')
    if (!instances.fields.getByName('projectLabel')) {
      instances.fields.add(new TextField({ name: 'projectLabel', max: 60 }))
      app.save(instances)
    }
  },
  (app) => {
    const instances = app.findCollectionByNameOrId('instances')
    instances.fields.removeByName('projectLabel')
    app.save(instances)
  }
)
