/// <reference path="../src/types/types.d.ts" />
// Applications web : une ligne pending_dns (propriété non prouvée) ne réserve plus un nom d'hôte. Plusieurs projets
// peuvent attendre le même nom ; le premier qui prouve la propriété (TXT, ou alias attribué par la plateforme) le
// prend. L'unicité ne porte donc que sur les lignes prouvées.
const FULL = 'CREATE UNIQUE INDEX idx_web_app_domains_host ON web_app_domains (host)'
const CLAIMED = "CREATE UNIQUE INDEX idx_web_app_domains_host ON web_app_domains (host) WHERE status != 'pending_dns'"
const swap = (app, to) => {
  const domains = app.findCollectionByNameOrId('web_app_domains')
  if (!domains.indexes.some((index) => index.includes('idx_web_app_domains_host')))
    throw new Error('Index idx_web_app_domains_host introuvable.')
  domains.indexes = domains.indexes.map((index) => (index.includes('idx_web_app_domains_host') ? to : index))
  app.save(domains)
}
migrate(
  (app) => swap(app, CLAIMED),
  (app) => {
    // Retour à l'unicité stricte : seules des demandes non prouvées en double peuvent bloquer ; elles sont retirées.
    for (const row of app.findRecordsByFilter('web_app_domains', 'status = "pending_dns"', 'created', 100000, 0)) {
      const others = app.findRecordsByFilter('web_app_domains', 'host = {:host} && id != {:id}', '', 1, 0, {
        host: row.getString('host'),
        id: row.id,
      })
      if (others.length) app.delete(row)
    }
    swap(app, FULL)
  }
)
