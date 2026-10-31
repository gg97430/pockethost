/// <reference path="../src/types/types.d.ts" />
// Alertes : outbox d'événements poussés (sans scan de source), canal plateforme (superAdmins), suivi du rétablissement
// santé dû après un redéploiement, compteur de baux de sauvegarde (boucle de crash → échec), empreinte du destinataire
// (dédoublonnage disque par hôte).
const DEDUPE_OLD =
  'CREATE UNIQUE INDEX idx_web_notification_dedupe ON web_notification_deliveries (appId,channel,eventKey)'
const DEDUPE_NEW =
  'CREATE UNIQUE INDEX idx_web_notification_dedupe ON web_notification_deliveries (scope,appId,channel,eventKey)'
const FINGERPRINT =
  'CREATE INDEX idx_web_notification_fingerprint ON web_notification_deliveries (event,eventKey,fingerprint)'
migrate(
  (app) => {
    const created = { name: 'created', type: 'autodate', onCreate: true, onUpdate: false },
      updated = { name: 'updated', type: 'autodate', onCreate: true, onUpdate: true }
    const closed = {
      type: 'base',
      listRule: null,
      viewRule: null,
      createRule: null,
      updateRule: null,
      deleteRule: null,
    }
    app.save(
      new Collection({
        ...closed,
        name: 'web_alert_events',
        fields: [
          { name: 'scope', type: 'text', required: true, max: 10 },
          { name: 'appId', type: 'text', max: 15 },
          { name: 'event', type: 'text', required: true, max: 40 },
          { name: 'eventKey', type: 'text', required: true, min: 64, max: 64 },
          { name: 'details', type: 'json', maxSize: 3000 },
          created,
        ],
        indexes: [
          'CREATE UNIQUE INDEX idx_web_alert_events_key ON web_alert_events (scope,appId,eventKey)',
          'CREATE INDEX idx_web_alert_events_scan ON web_alert_events (scope,appId,created)',
        ],
      })
    )
    app.save(
      new Collection({
        ...closed,
        name: 'web_platform_notification_settings',
        fields: [
          { name: 'key', type: 'text', required: true, max: 20 },
          { name: 'actorId', type: 'text', max: 15 },
          { name: 'configEncrypted', type: 'text', hidden: true, max: 30000 },
          { name: 'revision', type: 'text', max: 24 },
          { name: 'enabled', type: 'bool' },
          { name: 'activatedAt', type: 'date' },
          { name: 'scanAfter', type: 'date' },
          { name: 'sourceCursors', type: 'json', hidden: true, maxSize: 3000 },
          { name: 'diskLow', type: 'bool' },
          { name: 'diskChangedAt', type: 'date' },
          { name: 'lastClaimAt', type: 'date' },
          { name: 'workerDown', type: 'bool' },
          { name: 'recoveryCheckedAt', type: 'date' },
          created,
          updated,
        ],
        indexes: ['CREATE UNIQUE INDEX idx_web_platform_notification_key ON web_platform_notification_settings (key)'],
      })
    )
    const deliveries = app.findCollectionByNameOrId('web_notification_deliveries')
    for (const name of ['appId', 'actorId']) {
      const field = deliveries.fields.getByName(name)
      field.required = false
      field.min = 0
    }
    deliveries.fields.add(new TextField({ name: 'scope', max: 10 }))
    deliveries.fields.add(new TextField({ name: 'fingerprint', max: 64 }))
    deliveries.indexes = [
      ...deliveries.indexes.filter((index) => !index.includes('idx_web_notification_dedupe')),
      DEDUPE_NEW,
      FINGERPRINT,
    ]
    app.save(deliveries)
    const health = app.findCollectionByNameOrId('web_app_health')
    health.fields.add(new TextField({ name: 'alertState', max: 20 }))
    app.save(health)
    const backups = app.findCollectionByNameOrId('web_app_backups')
    backups.fields.add(new NumberField({ name: 'leaseAttempts', min: 0, max: 100, onlyInt: true }))
    app.save(backups)
  },
  (app) => {
    for (const name of ['web_alert_events', 'web_platform_notification_settings'])
      app.delete(app.findCollectionByNameOrId(name))
    for (const row of app.findRecordsByFilter('web_notification_deliveries', 'scope="platform"', '', 100000, 0))
      app.delete(row)
    const deliveries = app.findCollectionByNameOrId('web_notification_deliveries')
    deliveries.indexes = [
      ...deliveries.indexes.filter((index) => !/idx_web_notification_(dedupe|fingerprint)/.test(index)),
      DEDUPE_OLD,
    ]
    deliveries.fields.removeByName('scope')
    deliveries.fields.removeByName('fingerprint')
    for (const name of ['appId', 'actorId']) {
      const field = deliveries.fields.getByName(name)
      field.required = true
      field.min = 15
    }
    app.save(deliveries)
    for (const [collection, field] of [
      ['web_app_health', 'alertState'],
      ['web_app_backups', 'leaseAttempts'],
    ]) {
      const value = app.findCollectionByNameOrId(collection)
      value.fields.removeByName(field)
      app.save(value)
    }
  }
)
