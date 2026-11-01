migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false }
    const updated = { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true }
    for (const definition of [
      {
        name: 'web_app_backups',
        fields: [
          { name: 'appId', type: 'text', required: true, max: 15 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'status', type: 'text', required: true, max: 20 },
          { name: 'trigger', type: 'text', max: 20 },
          { name: 'operation', type: 'text', max: 20 },
          { name: 'payloadEncrypted', type: 'text', hidden: true, max: 400000 },
          { name: 'sourceDeploymentId', type: 'text', max: 15 },
          { name: 'commit', type: 'text', max: 40 },
          { name: 'imageRef', type: 'text', max: 150 },
          { name: 'imageId', type: 'text', max: 80 },
          { name: 'imageBytes', type: 'number', min: 0 },
          { name: 'archiveBytes', type: 'number', min: 0 },
          { name: 'checksum', type: 'text', max: 64 },
          { name: 's3State', type: 'text', max: 30 },
          { name: 's3Object', type: 'text', max: 500 },
          { name: 's3ImageObject', type: 'text', max: 500 },
          { name: 's3Error', type: 'text', max: 500 },
          { name: 's3CheckedAt', type: 'date' },
          { name: 's3Bytes', type: 'number', min: 0 },
          { name: 's3TotalBytes', type: 'number', min: 0 },
          { name: 'error', type: 'text', max: 500 },
          { name: 'startedAt', type: 'date' },
          { name: 'finishedAt', type: 'date' },
          { name: 'leaseAt', type: 'date' },
          { name: 'leaseHash', type: 'text', hidden: true, max: 64 },
          created,
          updated,
        ],
        indexes: [
          'CREATE INDEX idx_web_backups_app ON web_app_backups (appId,created DESC)',
          'CREATE INDEX idx_web_backups_status ON web_app_backups (status,created)',
        ],
      },
      {
        name: 'web_app_backup_settings',
        fields: [
          { name: 'appId', type: 'text', required: true, max: 15 },
          { name: 'enabled', type: 'bool' },
          { name: 'hourUTC', type: 'number', min: 0, max: 23, onlyInt: true },
          { name: 'minuteUTC', type: 'number', min: 0, max: 59, onlyInt: true },
          { name: 'retention', type: 'number', min: 1, max: 14, onlyInt: true },
          { name: 'nextRunAt', type: 'date' },
          { name: 'lastRunAt', type: 'date' },
          { name: 'lastError', type: 'text', max: 500 },
          created,
          updated,
        ],
        indexes: ['CREATE UNIQUE INDEX idx_web_backup_settings_app ON web_app_backup_settings (appId)'],
      },
    ])
      app.save(
        new Collection({
          ...definition,
          type: 'base',
          listRule: null,
          viewRule: null,
          createRule: null,
          updateRule: null,
          deleteRule: null,
        })
      )
  },
  (app) => {
    for (const name of ['web_app_backup_settings', 'web_app_backups']) app.delete(app.findCollectionByNameOrId(name))
  }
)
