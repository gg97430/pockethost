/// <reference path="../pb_data/types.d.ts" />
migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('etae8tuiaxl6xfv')

    collection.fields.add(
      new BoolField({
        help: '',
        hidden: false,
        system: false,
        id: 'k8m2vacu',
        name: 'autoVacuum',
        required: false,
        presentable: false,
      })
    )

    app.save(collection)

    app.db().newQuery('UPDATE instances SET autoVacuum = {:v}').bind({ v: true }).execute()
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('etae8tuiaxl6xfv')

    collection.fields.removeById('k8m2vacu')

    return app.save(collection)
  }
)
