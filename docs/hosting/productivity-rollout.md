# Améliorations de productivité — intervention du 3 novembre 2026

Périmètre accepté : diagnostic d'incidents, contrôles avant déploiement, vue commune des trois serveurs, maintenance des déploiements, réglages de RAM, suivi des crons, essais complets de reprise, assistant de migration et parcours utilisateur configurables.

## Contraintes de livraison

- Valider app4 et app5 avant d'activer le même lot sur app3. Un échec des pilotes interdit l'activation sur app3.
- Aucun redémarrage de machine ni arrêt/recréation de base, site, worker métier ou tâche active pour publier le logiciel de plateforme.
- Les modifications des services de contrôle attendent la fin des opérations actives ; les nouveaux push restent reçus et conservés en file. Les boucles de liaison BDD, sondes et notifications restent actives pendant l'attente.
- Les données, secrets, domaines et budgets restent propres à chaque serveur. TOTP et droits existants demeurent obligatoires.
- Les essais utilisent des copies isolées et des ressources jetables. Ils ne déplacent pas les vrais projets et n'activent pas leurs mails/crons.
- Les migrations de schéma sont additives. Le retour arrière automatique concerne le code, jamais les écritures métier intervenues depuis une capture.

## Lots et critères de réception

1. **Fondations de livraison.** Vagues app4/app5 → app3, suspension bornée des nouvelles opérations, journal de maintenance, contrôles continus des sites/bases et reprise automatique des admissions. Tester toutes les branches de retour arrière, l'expiration de la suspension et la réception de push pendant celle-ci.
2. **Crons et ressources.** Heure de La Réunion, dernier succès/retard/occurrence manquée, absence de chevauchement et reprise bornée ; lecture/édition des engagements mémoire avec révision, validation de capacité et mise à jour sans redémarrage des processus clients.
3. **Incidents et contrôles avant publication.** Causes étayées par les événements, regroupement des dépendances, diagnostics distincts des hypothèses ; contrat de variables obligatoires, accès et authentification BDD, cohérence des URL et contrôles configurables en lecture seule.
4. **Vue de flotte.** Inventaire et état des trois hôtes dans un seul écran, données horodatées et indisponibilités explicites. Connexions serveur à serveur limitées, sans secrets dans le navigateur ni délégation implicite des droits d'un hôte à un autre.
5. **Reprise de projet.** Capture cohérente et essai isolé BDD/fichiers/images/volumes, démarrage des copies sans effets externes, vérification des dépendances et nettoyage. Un résultat partiel n'est jamais présenté comme une reprise complète.
6. **Migration de projet.** Plan révisé source/cible, contrôles de capacité et compatibilité, transfert chiffré et vérifié, remappage explicite des liens/variables/domaines, workers et crons, aperçu de bascule et retour arrière. Aucune migration de projet réel sans sélection explicite et validation du plan concret.
7. **Parcours utilisateur.** Scénarios configurables et bornés, comptes dédiés aux lectures, secrets chiffrés, sorties masquées et protection contre les cibles réseau non autorisées. Le statut technique et le résultat fonctionnel restent distincts.

## État

- [x] Fondation de livraison et réception app4/app5, puis app3 : `kolab-20261103-admission-e8463583ccf4`.
- [x] Crons et ressources livrés et vérifiés sur les trois hôtes : `kolab-20261103-controls-ab4748864125`.
- [x] Incidents, diagnostics et contrôles avant publication livrés : `kolab-20261103-checks-e0052c7f7903`.
- [x] Vue de flotte et autorisations interserveurs livrées : `kolab-20261103-fleet-4e93765bf65b`.
- [x] Essais complets de reprise disponibles et vérifiés sur un projet jetable : `kolab-20261103-project-tools-6026f83b439c`.
- [x] Assistant de migration vérifié entre app4 et app5 avec un projet jetable : même lot reprise/migration.
- [x] Parcours utilisateur configurables et vérifiés : même lot contrôles/diagnostics.
- [x] Réception finale complémentaire : accès aux réglages RAM et à la pause/reprise corrigé et vérifié sur app4/app5, puis app3 : `kolab-20261103-operator-access-a485e90fc37e`.

La réception complémentaire a confirmé puis corrigé l’appel à `sudo` incompatible avec `NoNewPrivileges` sur app4/app5. Les commandes d’administration passent désormais par un socket local dédié, avec vérification des identités système et quatre actions bornées. Les protections et le TOTP sont conservés. Sur les deux pilotes, un vrai contrôleur jetable sous `NoNewPrivileges`, avec TOTP, a vérifié réservation fixe puis automatique sans changement de PID/date de démarrage, verrou de migration, révision, audit et pause/reprise isolée. Sur les trois hôtes, le même parcours authentifié a vérifié le helper installé en lecture seule et le refus des écritures invalides. Aucune politique cliente ni pause réelle n’a été modifiée par ces essais. Publication app4/app5 validée avant app3, sondes continues sans échec (200/40, 33/3, 144/16), conteneurs clients inchangés, admission reprise et contrôle de flotte commun sans dérive. Voir [les contrôles opérateur](operator-controls.md). Reprise et migration sont livrées ; aucune migration de projet réel n’a été lancée.

### Réception de la fondation

Publication dans l’ordre app4/app5 (activation et vérification des deux), puis app3. Identités et dates de démarrage des conteneurs clients inchangées. Sondes HTTPS continues : app4 5 130 mesures / 38 cibles, app5 27 / 3, app3 165 / 15 ; aucun échec ni cible exclue. Les nouvelles admissions ont repris sur les trois hôtes. Contrôle commun de flotte réussi, sans dérive de fichiers. Validation ciblée : 62 tests worker, 16 tests tâches (dont un facultatif ignoré), 16 tests flotte, 7 tests maintenance, 7 tests garde ; remplacement réel d’un service systemd jetable sur app5.

Le lot crons et les autres fonctionnalités restent en cours ; cette réception ne couvre pas encore les écrans ni les fonctions de reprise/migration.

### Lot crons / RAM en préparation

Code et migration additive prêts pour les fuseaux UTC/La Réunion, dernier succès, occurrence manquée et rattrapage unique borné. Les tâches existantes sans fuseau restent UTC, y compris après une simple modification du nom ou de l’activation. Recette sur PocketBase 0.39.11 jetable réussie (droits/TOTP, commandes chiffrées, déduplication, expiration et historique masqué).

Réglages superadmin de RAM / pause d’admission dans la supervision. Les commandes locales sont limitées aux politiques de mémoire, sans commande métier ni opération de cycle de vie Docker. Contrôles de révision, capacité, sauvegarde active, identité/processus et marge au-dessus de la mémoire vive actuelle. Approbations explicites persistantes pour repasser une base en automatique. Test réel sur un conteneur jetable d’app5 : réservation fixe puis automatique, même PID / date de démarrage, prise en compte par l’allocateur ; aucun conteneur client modifié. Particularité Docker : une mise à jour à zéro conserve l’ancienne réservation système ; la transition utilise son minimum de 6 Mio, sans engagement de capacité Kolab, puis zéro lors du prochain démarrage naturel.

**Livré** : `kolab-20261103-controls-ab4748864125`, empreinte commune `ab4748864125cc363e845a0288ad3843e19191a8baa982daeb942df3ceabae41`. Recette Chrome isolée : thèmes clair/sombre, 390 px sans débordement, modification RAM et pause/reprise. Code/typechecks valides ; 7 tests opérateur et 32 tests allocateur. Recette native crons réussie avec TOTP et droits. Test Docker réel de réservation fixe puis automatique validé sans nouveau PID ni nouvelle date de démarrage.

Publication des deux pilotes puis app3 ; sonde continue app4 520 / 40 cibles (dont deux connexions privées), app5 60 / 3, app3 288 / 16 (dont une connexion privée). Aucune cible exclue ni échec. Conteneurs clients inchangés ; Avenirassur conserve 32 Gio réservés. Nouvelles colonnes crons vérifiées, API locales contrôlées et accès non authentifié refusé, supervision d’allocation fraîche. Contrôle final de flotte commun réussi. Le plafond du mode automatique reste celui déjà pris en charge au démarrage (16 Gio maximum / moitié de la RAM de l’hôte), indiqué dans le formulaire ; un budget différent utilise une réservation fixe. Aucune modification des processus de routage ou des services clients n’était nécessaire.

### Réception diagnostics / contrôles / parcours

Livré sur app4 et app5, tous deux validés avant activation d’app3 : `kolab-20261103-checks-e0052c7f7903`, source `e0052c7f7903d4d474b11007ea78a3b918ff492dad1204ca4a12afc0c8077231`. Aucun échec ni cible exclue : app4 120 mesures / 40 cibles, app5 60 / 3, app3 1040 / 16. Identités/démarrages des sites et bases inchangés ; Avenirassur reste à 32 Gio réservés. Admission reprise, files de contrôle et leurs marqueurs surveillés par la garde. Contrôle commun sans dérive, contrôleurs/services/origines sains.

L’onglet Santé propose le contrat de variables, les contrôles d’URL et les vérifications de liaisons/identifiants BDD avant construction. Désactivés par défaut sur les applications existantes. Les parcours bornés utilisent un domaine appartenant à l’app, uniquement des lectures après une éventuelle connexion autorisée ; secrets chiffrés, stdin privé, TLS/IP publique épinglée, aucune redirection ou commande projet. Une révocation de droits suspend les parcours et invite un administrateur actuel à réenregistrer. Les résultats techniques et fonctionnels restent distincts. Aucun nouveau mail automatique ajouté.

Validation : 8 tests préflight, 6 exécuteur HTTP, 5 contrat TS, 14 diagnostics, 8 garde, 7 maintenance ; 62 tests worker et 23 notifications/observabilité passent. Recette native jetable avec TOTP, vrais échanges santé/auth PocketBase par socket Unix, chiffrement, baux à usage unique et expiration, résultat réussi incomplet refusé, retrait d’accès projet, nettoyage pendant maintenance et preflight exécuté avant toute opération Docker. Chrome : sauvegarde/effacement du mot de passe, lancement manuel, clair/sombre, 390 px ; diagnostics périmés retirés des constats actuels.

### Réception de la vue des serveurs

Version `kolab-20261103-fleet-4e93765bf65b`, source `4e93765bf65bade46c9130cd6374d8c4b568a2e75b6c9cbabb415017083da61f`, livrée dans les deux vagues prévues. Lecture app4↔app5 testée avant toute activation d’app3, puis toutes les paires testées avec le même superadmin vérifié existant, sans créer ni copier de compte. Identité absente refusée, aucune permission distante implicite. SSH avec clés dédiées par paire, commande forcée, IP source contrainte et clés d’hôte relevées par session administrative authentifiée. Aucun accès aux environnements, configurations ou données métier dans ce lecteur.

11 tests lecteur Python, 9 tests TS (DTO et handler), 7 tests d’orchestration et 4 tests de scellement de récupération passent. Recette native TOTP/droits sur contrôleur jetable réussie ; Chrome clair/sombre, tri/recherche, accès refusé/indisponibilité/fraîcheur et 390 px sans débordement. Les clés actives restent root ; un export authentifié chiffré est conservé séparément par hôte pour la reprise, sans accorder leur lecture au service de sauvegarde.

Continuité : app4 520 mesures / 40 cibles, app5 60 / 3, app3 96 / 16 ; aucun échec ni cible exclue. Identités et démarrages des conteneurs clients inchangés ; Avenirassur 32 Gio conservés. Admission reprise sur les trois hôtes et contrôle de flotte commun sans dérive. La reprise de projet et l’assistant migration restent en recette, non publiés à ce stade.

### Préparation de la reprise et de la migration — réception en cours

Les critères ci-dessous décrivent le lot à recevoir. Ils ne constituent ni une preuve de publication, ni une autorisation de migrer un client. Les cases reprise, migration et réception finale restent ouvertes. La procédure d’installation est ajoutée au [guide nouveau serveur](nouveau-serveur-kolab.md) ; les détails sont dans [la reprise de projet](project-recovery.md) et [la migration](project-migration.md).

**Isolation et amorçage.** Le moteur Docker rootless UID 1002 (`kolab-recovery`) est distinct de l’hébergement UID 1001. Son socket `/run/user/1002/docker.sock` et sa home sont privés ; l’accès depuis UID 1001 doit échouer. La slice complète impose 4 Gio RAM, swap 0, CPU 100 % (un cœur), 512 tâches. Le coordinateur est un nouveau service root limité séparément à 1 Gio, avec une image auxiliaire épinglée par ID SHA256. L’admission conserve la réserve hôte, les engagements des BDD et la pointe des copies ; la garde disque reste active pendant toutes les phases. Aucun code client ne s’exécute sur un moteur rootful ou avec un socket de production monté.

L’installateur moteur crée seulement le moteur 1002 et préserve les moteurs/services existants. Le coordinateur possède deux clés locales distinctes (capsule et accès contrôleur), et la CLI migration une troisième capacité locale. Elles ne viennent pas du worker 1001 et ne sont pas transférées à un autre serveur. L’installation de migration n’ajoute aucun service. Toute API humaine exige une session autorisée avec TOTP validé, superadmins compris.

**Preuves de reprise attendues.** Sur un projet jetable, capturer BDD, fichiers, images, volumes, configurations, liaisons et tâches ; restaurer les copies dans le moteur 1002, tester le démarrage HTTP et nettoyer toutes les ressources journalisées. Les workers restent arrêtés, les mails/crons désactivés et le réseau extérieur absent. Les hooks/migrations natifs archivés ne sont pas exécutés. Distinguer couverture complète des artefacts et validation métier : un HTTP sain ne prouve pas tous les parcours. Bloquer une couverture manquante, les dépendances externes non prises en charge, les fichiers changeants ou un nettoyage incomplet.

**Preuves de migration attendues.** Utiliser uniquement un projet jetable app4→app5, une capsule chiffrée et une clé éphémère de projet transportées par l’opérateur. Pas de connexion SSH serveur à serveur ni de copie de comptes, clés globales ou données hors projet. Le plan doit montrer les nouveaux IDs, le propriétaire cible existant, les liens/URL, la compatibilité exacte des binaires/images, les capacités RAM/disque et les engagements dédiés. Une BDD partagée hors périmètre, des hooks non revus, une URL compilée à reconstruire ou une dépendance externe bloquent la suite.

Vérifier les approbations par nom exact et révision CAS, l’expiration du plan avant import, le refus d’une ancienne révision et la reprise après interruption. L’import exige une recette dans le moteur 1002 complète et nettoyée, puis crée des ressources cibles suspendues dans leur moteur normal. Le déverrouillage ne les démarre pas et n’active pas les crons/workers ni les envois. Le rollback se limite aux copies journalisées intactes ; une modification concurrente interdit leur suppression automatique.

La bascule DNS reste une opération séparée : barrière de gel des écritures/crons/workers attestée avant la capture finale, aperçu avant/après lié au plan et valable 15 minutes, confirmations explicites puis exécution par la CLI opérateur. La barrière est une déclaration de l’opérateur, pas une observation automatique du trafic. Sans elle, recette autorisée mais DNS refusé. Tester les réponses Cloudflare ambiguës, la reprise du journal et les conflits CAS sans altérer les DNS de clients. Après des écritures sur la cible, un retour DNS exige une stratégie de réconciliation des données.

**Exploitation et publication attendues.** Compter la slice 1002 et le coordinateur dans la catégorie de ressources « Sauvegardes », sans double comptage de conteneurs, et surveiller la fraîcheur de leur santé. Sauvegarder les configurations/clés root par un canal privé vérifié sans relâcher leurs permissions ; les copies temporaires du moteur 1002 sont recréables et exclues des données clientes. Les publications attendent la fin des recettes/finalisations et ne suspendent que les nouvelles admissions. Activer et vérifier app4/app5 avant app3, maintenir les sondes continues et comparer IDs/dates de démarrage des clients, puis terminer par `python3 packages/pockethost/scripts/platform-fleet.py check`. Aucun arrêt client, worker métier ou cron actif, ni redémarrage d’hôte, n’est admis pour livrer ce lot.

### Réception reprise / migration

Version commune `kolab-20261103-project-tools-6026f83b439c`, source `6026f83b439c68e852badfb513a4f4e5b849df8f2958788ad241fd3043917786`. Activation et réception complètes d’app4 et app5 avant toute activation d’app3. Sondes continues de la publication finale : app4 200 mesures / 40 cibles, app5 69 / 3, app3 735 / 15 ; aucun échec ni exclusion. Les identités et dates de démarrage des conteneurs clients sont restées identiques. Avenirassur reste à 32 Gio réservés. Admission reprise et contrôle de flotte commun sans dérive ; contrôleurs, services et origines sains.

Moteur Docker rootless1002 indépendant, inaccessible au worker1001 ; coordinateur root neuf, capacités bornées, lectures SQLite dans un enfant UID1000 sans capacités. Copie sur disque `/var/tmp` (les `/tmp` de la flotte sont en RAM), limite32Gio et réserve15Gio contrôlées pendant le snapshot. Les chemins de volumes fournis par le moteur1001 doivent correspondre exactement à sa racine système approuvée. Les baux orphelins sont réconciliés après nettoyage prouvé ; une pause d’admission est relue après verrou d’écriture natif.

Validation : 12 tests moteur/service, 6 installateur, 9 groupes API natifs ; recette complète sous unité durcie avec WAL/source UID1000 ; transport app4→app5, reconstruction des fichiers/volumes/images, front et base copiée testés, worker inerte, nettoyage puis rollback des seules copies. Migration : 12 tests Python et API TOTP/droits, plan exact, capacité, import suspendu, verrous, consentements DNS distincts, journaux durables et reprise idempotente. Chrome : clair/sombre,390px sans débordement, source/aperçu, refus d’une révision périmée, accords et blocages de déverrouillage/DNS. Aucun vrai projet ni DNS déplacé.

Une première tentative avait été intégralement annulée après l’échec d’amorçage1002 sur app3 : l’outil système y réside dans `/usr/local/bin`, et non `/usr/bin`. Le retour arrière des trois hôtes a été vérifié sans dérive ni arrêt client. L’installateur résout désormais un chemin système root protégé, sans faire confiance au PATH ; le correctif a suivi à nouveau les vagues4/5→3. La correction du lanceur de crons `0b4263d02baa`, intervenue pendant le chantier, a été conservée.

Les contrôles techniques et la validation métier restent distincts. Une dépendance externe non couverte empêche de déclarer une reprise complète. Une vraie bascule exige un projet source/cible et un plan approuvé, une capture après gel déclaré des écritures/crons/workers, puis un accord séparé sur l’aperçu DNS ; le retour DNS ne fusionne pas les écritures de la cible.

Configuration des outils scellée par hôte dans `configuration.json.enc`, avec copie opérateur privée ; seules les données chiffrées deviennent lisibles par le compte de sauvegarde. Plans globaux d’app4 et d’app5 actualisés ; archive de code d’app5 vérifiée sur 2 772 fichiers. App3 ne dispose pas de plan global : son export chiffré est conservé sur le poste opérateur avec sa clé externe distincte, sans prétendre à une sauvegarde globale distante configurée. Les copies et clés de transfert de recette ont été retirées des hôtes.
