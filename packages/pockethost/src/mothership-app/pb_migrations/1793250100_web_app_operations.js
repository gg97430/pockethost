/// <reference path="../src/types/types.d.ts" />
migrate(
  (app) => {
    const jobs = app.findCollectionByNameOrId('web_deployments')
    for (const field of [
      { name: 'cancelRequested', type: 'bool' },
      { name: 'cancelRequestedAt', type: 'date' },
      { name: 'publicationStartedAt', type: 'date' },
      { name: 'heartbeatAt', type: 'date' },
      { name: 'stageKey', type: 'text', max: 40 },
      { name: 'phases', type: 'json', maxSize: 20000 },
      { name: 'retriedFrom', type: 'text', max: 15 },
    ])
      jobs.fields.add(new Field(field))
    app.save(jobs)
    const deliveries = app.findCollectionByNameOrId('web_webhook_deliveries')
    for (const field of [
      { name: 'event', type: 'text', max: 60 },
      { name: 'repository', type: 'text', max: 250 },
      { name: 'branch', type: 'text', max: 200 },
      { name: 'payloadCommit', type: 'text', max: 40 },
      { name: 'resolvedCommit', type: 'text', max: 40 },
      { name: 'status', type: 'text', max: 30 },
      { name: 'queued', type: 'number', min: 0 },
      { name: 'duplicateCount', type: 'number', min: 0 },
      { name: 'lastReceivedAt', type: 'date' },
      { name: 'note', type: 'text', max: 250 },
    ])
      deliveries.fields.add(new Field(field))
    deliveries.indexes = [
      ...deliveries.indexes,
      'CREATE INDEX idx_web_delivery_repository ON web_webhook_deliveries (repository,branch,created DESC)',
    ]
    app.save(deliveries)
  },
  (app) => {
    const jobs = app.findCollectionByNameOrId('web_deployments')
    for (const name of [
      'cancelRequested',
      'cancelRequestedAt',
      'publicationStartedAt',
      'heartbeatAt',
      'stageKey',
      'phases',
      'retriedFrom',
    ])
      jobs.fields.removeByName(name)
    app.save(jobs)
    const deliveries = app.findCollectionByNameOrId('web_webhook_deliveries')
    deliveries.indexes = deliveries.indexes.filter((v) => !v.includes('idx_web_delivery_repository'))
    for (const name of [
      'event',
      'repository',
      'branch',
      'payloadCommit',
      'resolvedCommit',
      'status',
      'queued',
      'duplicateCount',
      'lastReceivedAt',
      'note',
    ])
      deliveries.fields.removeByName(name)
    app.save(deliveries)
  }
)
