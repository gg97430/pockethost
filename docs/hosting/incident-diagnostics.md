# Diagnostics de supervision

L’écran **Supervision → Contrôles et alertes** conserve les sondes détaillées et ajoute des explications regroupées. La lecture n’exécute aucune commande Docker, ne réveille aucune base, ne modifie aucune configuration et n’envoie aucun message. Cette vue est réservée aux superadmins non suspendus, derrière les protections d’authentification et de TOTP existantes.

## Lire un diagnostic

- **Constats observés** : nom, résultat exact de la sonde, date et lien vers la ressource. Un relevé de plus de trois minutes ou daté dans le futur ne décrit pas l’état actuel. Le navigateur retire également les anciens constats si les actualisations cessent.
- **Hypothèse à confirmer** : lien possible entre une base en défaut et une application qui déclare une liaison privée vers elle. Des échecs simultanés ne démontrent pas une cause commune.
- **Prochaine vérification** : contrôle en lecture avant toute intervention sur un service. Le diagnostic ne déclenche aucun redémarrage ou nettoyage.

Un battement worker absent et des mesures web périmées sont regroupés comme difficulté de pilotage et de collecte. Cela ne prouve pas l’arrêt des sites publiés. Une réponse HTTP indépendante du site en erreur reste présentée séparément.

L’inventaire Docker des bases, la santé locale des bases, les cibles locales des publications et l’origine HTTPS sont des observations différentes. Un défaut du certificat ou du proxy public ne démontre pas une panne de PocketBase. Un pourcentage de RAM élevé ne prouve ni une fuite ni un arrêt de processus.

Une liaison privée ne produit un constat propre que si son état est récent (45 secondes au maximum) et son code d’erreur appartient aux codes connus du relais. La configuration déclarée seule ne prouve pas une erreur de connexion.

## Événements de déploiement

Les vingt dernières tentatives échouées ou annulées dans l’heure sont présentées avec date, application et lien vers les déploiements. Une classification des messages fixes distingue notamment attente de capacité, construction, contrôle de santé, interruption et remplacement par un commit plus récent. Aucun contenu d’erreur ou de journal de projet n’est retourné par ce diagnostic ; seul le code de catégorie traverse l’API.

Une tentative non publiée ne détermine pas la disponibilité de la version déjà en service. Les sondes de publication restent la source de ce constat technique.

## Limites et vérification

Les inventaires sont bornés et leur indisponibilité est indiquée comme diagnostic partiel. Cent points regroupés au maximum sont affichables ; les éventuels points supplémentaires restent dans le tableau complet. Les vingt-cinq premiers constats de chaque regroupement sont détaillés. Aucun regroupement ne supprime une sonde ou ne change les compteurs, seuils, délais de confirmation et envois d’alertes existants.

Sources : `common/platformDiagnostics.ts`, adaptateur `operatorAdmin/diagnostics.ts`, composant `IncidentDiagnostics.svelte`. Tests ciblés : `platformDiagnostics.test.ts` et `operatorAdmin/diagnostics.test.ts` (dépendances, fraîcheur, absence de causalité inventée, événements masqués, bornes et format natif des dates PocketBase). La publication suit les vagues app4/app5 puis app3 décrites dans `productivity-rollout.md`.
