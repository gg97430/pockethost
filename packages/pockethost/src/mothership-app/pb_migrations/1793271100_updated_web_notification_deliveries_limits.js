/// <reference path="../src/types/types.d.ts" />
// Alertes web : les tests manuels ont leur propre plafond par utilisateur (requestedBy), hors du budget horaire des
// alertes automatiques ; une alerte regroupée par la limite de fréquence (rateLimited) est résumée dans une
// notification « alerts_grouped » à la fin de l'heure au lieu d'être perdue.
const RATE =
  'CREATE INDEX idx_web_notification_rate_limited ON web_notification_deliveries (settingsId,channel,rateLimited,created)'
const TESTS = 'CREATE INDEX idx_web_notification_tests ON web_notification_deliveries (requestedBy,trigger,created)'
migrate(
  (app) => {
    const deliveries = app.findCollectionByNameOrId('web_notification_deliveries')
    deliveries.fields.add(new TextField({ name: 'requestedBy', max: 15 }))
    deliveries.fields.add(new BoolField({ name: 'rateLimited' }))
    deliveries.indexes = [...deliveries.indexes, RATE, TESTS]
    app.save(deliveries)
  },
  (app) => {
    const deliveries = app.findCollectionByNameOrId('web_notification_deliveries')
    deliveries.indexes = deliveries.indexes.filter(
      (index) => !index.includes('idx_web_notification_rate_limited') && !index.includes('idx_web_notification_tests')
    )
    deliveries.fields.removeByName('requestedBy')
    deliveries.fields.removeByName('rateLimited')
    app.save(deliveries)
  }
)
