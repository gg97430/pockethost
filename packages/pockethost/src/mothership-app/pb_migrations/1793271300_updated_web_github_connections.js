/// <reference path="../src/types/types.d.ts" />
// Applications web : plusieurs connexions GitHub App. Les connexions restent des lignes de web_app_settings (« github »
// = connexion principale, intacte octet pour octet ; « github:<id> » = connexions supplémentaires) : aucune donnée
// secrète n'est lue ni déplacée ici, aucune clé maître n'est requise. Les livraisons portent la connexion qui les a
// reçues ('default', un id de 15 caractères, '' pour une livraison « Dépôt Git »).
const INDEX = 'CREATE INDEX idx_web_webhook_delivery_connection ON web_webhook_deliveries (connectionId, created DESC)'
migrate(
  (app) => {
    const deliveries = app.findCollectionByNameOrId('web_webhook_deliveries')
    deliveries.fields.add(new TextField({ name: 'connectionId', max: 15 }))
    deliveries.indexes = [...deliveries.indexes, INDEX]
    app.save(deliveries)
    const previews = app.findCollectionByNameOrId('web_preview_deliveries')
    previews.fields.add(new TextField({ name: 'connectionId', max: 15 }))
    app.save(previews)
    // Livraisons GitHub existantes = connexion principale (aucun secret lu). Les livraisons « Dépôt Git » gardent ''.
    app
      .db()
      .newQuery("UPDATE web_webhook_deliveries SET connectionId = 'default' WHERE deliveryId NOT LIKE 'git-%'")
      .execute()
  },
  (app) => {
    // Principale désactivée (meta.disabled) : l'ancien code ignore `meta` et la réactiverait sans avertissement.
    // Vérifiée avec la clé maître si elle est présente ; sinon un avertissement (rien n'est déchiffré).
    let principal = null
    try {
      principal = app.findFirstRecordByFilter('web_app_settings', "key = 'github'")
    } catch {}
    if (principal) {
      const key = $os.getenv('WEB_APPS_MASTER_KEY')
      let value = null
      if (/^[\x21-\x7e]{32}$/.test(key || ''))
        try {
          value = JSON.parse(toString($security.decrypt(principal.getString('valueEncrypted'), key)))
        } catch {}
      if (!value)
        console.warn(
          'web_github_connections down : clé maître absente ou invalide, état de la connexion principale non vérifié. ' +
            'Si elle est désactivée, suspendez son App sur GitHub : l’ancien code la réactive.'
        )
      else if (value.meta && value.meta.disabled === true)
        throw new Error(
          'La connexion GitHub principale est désactivée : l’ancien code la réactiverait. Pour qu’elle reste inactive, suspendez son App sur GitHub, puis réactivez-la dans PocketHost avant de revenir en arrière.'
        )
    }
    // Refus explicites : l'ancien code utiliserait silencieusement les identifiants de la connexion principale.
    const extra = app
      .findRecordsByFilter('web_app_settings', "key ~ 'github:'", '', 1000, 0)
      .filter((row) => /^github:[a-z0-9]{15}$/.test(row.getString('key')))
    if (extra.length)
      throw new Error('Des connexions GitHub supplémentaires existent : supprimez-les avant de revenir en arrière.')
    for (const row of app.findRecordsByFilter('web_apps', '', '', 100000, 0)) {
      let config = {}
      try {
        config = JSON.parse(row.getString('config') || '{}') || {}
      } catch {}
      if (config.githubConnectionId && config.githubConnectionId !== 'default')
        throw new Error(
          'Des applications sont rattachées à une connexion GitHub supplémentaire : rattachez-les à la connexion principale.'
        )
    }
    // La ligne « github » n'est jamais touchée ; son champ meta éventuel est ignoré par l'ancien code.
    const deliveries = app.findCollectionByNameOrId('web_webhook_deliveries')
    deliveries.indexes = deliveries.indexes.filter((index) => !index.includes('idx_web_webhook_delivery_connection'))
    deliveries.fields.removeByName('connectionId')
    app.save(deliveries)
    const previews = app.findCollectionByNameOrId('web_preview_deliveries')
    previews.fields.removeByName('connectionId')
    app.save(previews)
  }
)
