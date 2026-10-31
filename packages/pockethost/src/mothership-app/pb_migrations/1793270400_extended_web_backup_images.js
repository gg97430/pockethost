migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('web_app_backups')
    collection.fields.add(new JSONField({ name: 'runtimeImages', maxSize: 5000 }))
    collection.fields.add(new JSONField({ name: 'runtimeImageSizes', maxSize: 3000 }))
    collection.fields.add(new JSONField({ name: 's3ImageObjects', maxSize: 5000 }))
    collection.fields.add(new NumberField({ name: 'totalImageBytes', min: 0 }))
    app.save(collection)
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('web_app_backups')
    for (const name of ['runtimeImages', 'runtimeImageSizes', 's3ImageObjects', 'totalImageBytes'])
      collection.fields.removeByName(name)
    app.save(collection)
  }
)
