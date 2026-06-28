/// <reference path="../pb_data/types.d.ts" />
migrate(
  (app) => {
    const collection = app.findCollectionByNameOrId('ilstrreplv100')

    if (collection.fields.fieldNames().indexOf('s3Endpoint') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Endpoint S3/R2 utilise par Litestream pour cette instance.',
          hidden: true,
          system: false,
          id: 'ilss3end',
          name: 's3Endpoint',
          required: false,
          presentable: false,
          min: 0,
          max: 500,
          pattern: '',
        })
      )
    }

    if (collection.fields.fieldNames().indexOf('s3Bucket') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Bucket S3/R2 utilise par Litestream pour cette instance.',
          hidden: true,
          system: false,
          id: 'ilss3bkt',
          name: 's3Bucket',
          required: false,
          presentable: false,
          min: 0,
          max: 255,
          pattern: '',
        })
      )
    }

    if (collection.fields.fieldNames().indexOf('s3Prefix') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Prefixe S3/R2 optionnel utilise par Litestream pour cette instance.',
          hidden: true,
          system: false,
          id: 'ilss3pre',
          name: 's3Prefix',
          required: false,
          presentable: false,
          min: 0,
          max: 500,
          pattern: '',
        })
      )
    }

    if (collection.fields.fieldNames().indexOf('s3Region') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Region S3/R2 utilisee par Litestream pour cette instance.',
          hidden: true,
          system: false,
          id: 'ilss3reg',
          name: 's3Region',
          required: false,
          presentable: false,
          min: 0,
          max: 64,
          pattern: '',
        })
      )
    }

    if (collection.fields.fieldNames().indexOf('s3AccessKeyId') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Access key S3/R2 utilisee par Litestream pour cette instance.',
          hidden: true,
          system: false,
          id: 'ilss3aki',
          name: 's3AccessKeyId',
          required: false,
          presentable: false,
          min: 0,
          max: 255,
          pattern: '',
        })
      )
    }

    if (collection.fields.fieldNames().indexOf('s3SecretAccessKey') < 0) {
      collection.fields.add(
        new TextField({
          help: 'Secret key S3/R2 utilisee par Litestream pour cette instance.',
          hidden: true,
          system: false,
          id: 'ilss3sak',
          name: 's3SecretAccessKey',
          required: false,
          presentable: false,
          min: 0,
          max: 1024,
          pattern: '',
        })
      )
    }

    if (collection.fields.fieldNames().indexOf('s3ForcePathStyle') < 0) {
      collection.fields.add(
        new BoolField({
          help: 'Force le path-style S3 pour les endpoints compatibles type MinIO.',
          hidden: true,
          system: false,
          id: 'ilss3fps',
          name: 's3ForcePathStyle',
          required: false,
          presentable: false,
        })
      )
    }

    return app.save(collection)
  },
  (app) => {
    const collection = app.findCollectionByNameOrId('ilstrreplv100')

    for (const id of ['ilss3end', 'ilss3bkt', 'ilss3pre', 'ilss3reg', 'ilss3aki', 'ilss3sak', 'ilss3fps']) {
      try {
        collection.fields.removeById(id)
      } catch {}
    }

    return app.save(collection)
  }
)
