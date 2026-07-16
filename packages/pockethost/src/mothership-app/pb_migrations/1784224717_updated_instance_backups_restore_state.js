/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('instbackupsv100')
    const fields = collection.fields.fieldNames()

    if (fields.indexOf('restoreState') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Etat persistant de la derniere restauration lancee depuis cette archive.',
          hidden: false,
          system: false,
          id: 'ibrstate1',
          name: 'restoreState',
          required: false,
          presentable: false,
          min: 0,
          max: 20,
          pattern: '^(|running|ready|failed)$',
        })
      )
    }

    if (fields.indexOf('restoreUpdatedAt') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Date ISO de la derniere mise a jour de la restauration.',
          hidden: false,
          system: false,
          id: 'ibrupdated1',
          name: 'restoreUpdatedAt',
          required: false,
          presentable: false,
          min: 0,
          max: 64,
          pattern: '',
        })
      )
    }

    if (fields.indexOf('restoreError') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Derniere erreur de restauration, vide apres succes.',
          hidden: false,
          system: false,
          id: 'ibrerror1',
          name: 'restoreError',
          required: false,
          presentable: false,
          min: 0,
          max: 2000,
          pattern: '',
        })
      )
    }

    return app.save(collection)
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('instbackupsv100')

    for (const id of ['ibrerror1', 'ibrupdated1', 'ibrstate1']) {
      try {
        collection.fields.removeById(id)
      } catch {}
    }

    return app.save(collection)
  }
)
