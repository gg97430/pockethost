/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('instbackupsv100')

    if (collection.fields.fieldNames().indexOf('name') < 0) {
      collection.fields.add(
        new TextField({
          help: "Nom lisible attribue par l'utilisateur a la sauvegarde.",
          hidden: false,
          system: false,
          id: 'ibname01',
          name: 'name',
          required: false,
          presentable: true,
          min: 0,
          max: 120,
          pattern: '',
        })
      )
    }

    return app.save(collection)
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('instbackupsv100')

    try {
      collection.fields.removeById('ibname01')
    } catch {}

    return app.save(collection)
  }
)
