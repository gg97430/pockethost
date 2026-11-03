# Assistant de migration de projets

L’assistant prépare une copie d’un projet entre app3, app4 et app5. Il inventorie les applications publiées, leurs images, les bases PocketBase liées, fichiers et volumes, variables, liaisons privées, workers, tâches et domaines. La cible reçoit de **nouveaux identifiants** et appartient à un superadmin déjà présent sur cette cible. Aucun compte de la plateforme source n’est cloné.

L’import crée des ressources **suspendues**. Il ne démarre aucune application ou base cible et ne change aucun DNS. Les tâches, envois de mails et déploiements automatiques restent désactivés. Il faut approuver séparément l’import, le déverrouillage, puis un aperçu DNS exact. Un opérateur exécute les opérations locales depuis une session d’administration ; l’interface ne lance pas de commande SSH arbitraire.

## Installation

Installer d’abord les modules et le moteur de [reprise des projets](project-recovery.md). Le moteur de recette est un Docker rootless **UID 1002**, socket privé `/run/user/1002/docker.sock`, distinct du moteur d’hébergement UID 1001. L’utilisateur 1001 ne doit pas accéder au socket 1002. Le helper est une image locale approuvée, référencée par son SHA256 immuable.

```sh
sudo python3 packages/pockethost/scripts/install-project-migration.py \
  --data-root /chemin/data \
  --binary-root /chemin/pocketbase \
  --web-root /chemin/web-apps \
  --instance-app-root /chemin/instance-app \
  --helper-image sha256:IMAGE_APPROUVEE
```

L’installateur ne redémarre aucun service. Il crée la clé locale dédiée `/etc/kolab/project-migration-control.key` (root:ubuntu, 0440), la configuration root privée `/etc/kolab/project-migration.json` et le journal `/var/lib/kolab-project-migration/` (0700). Il installe `/opt/pockethost-web/web_project_migration.py`. Le contrôleur doit pouvoir lire sa seule clé locale, jamais les clés des autres serveurs. Livrer aussi les migrations PocketBase et les handlers/gardes du lot plateforme.

Le CLI s’exécute avec Python 3 en root. Ses dépendances installées sont `web_project_recovery.py`, `web_platform_recovery.py`, `database_memory_management.py`, `database_memory_reservations.py` et `platform_memory_allocator.py`. L’admission native lit également le helper root `platform_maintenance.py` ; elle prend le verrou d’écriture SQLite avant de relire la pause d’admission. Une publication de plateforme attend les imports et retours arrière en cours.

## Préparer une copie

1. Dans **Hébergements → Reprise → Migrations**, sélectionner le projet source et examiner sa révision. Choisir le serveur cible et un propriétaire superadmin existant. Ce formulaire est une préparation locale : il ne copie rien.
2. Sur la source, exporter la révision exacte affichée. Une clé aléatoire nouvelle est créée pour cette seule capsule.

```sh
sudo python3 /opt/pockethost-web/web_project_migration.py export \
  --project-id ID_PROJET_SOURCE --revision SHA_REVISION_SOURCE \
  --config /etc/kolab/project-migration.json \
  --output /root/migration-projet-source
```

3. Transférer **uniquement** `project.phpr`, `capsule.key` et `transfer.json`, via le poste opérateur et SSH vers la cible. Conserver les fichiers en 0600 et leur répertoire en 0700. Aucune clé SSH d’administration, de contrôleur, de service, Cloudflare ou GitHub ne doit traverser les serveurs. La clé de capsule ne déchiffre que le projet choisi.
4. Sur la cible, restaurer et authentifier la capsule avec son SHA256 de transfert. Cette étape n’exécute aucun code métier.

```sh
sudo python3 /opt/pockethost-web/web_project_recovery.py restore \
  --archive /root/migration-projet/project.phpr \
  --key-file /root/migration-projet/capsule.key \
  --destination /root/migration-projet/restored \
  --sha256 SHA256_DE_TRANSFER_JSON
sudo python3 /opt/pockethost-web/web_project_migration.py plan \
  --directory /root/migration-projet/restored --target app5 \
  --owner-id ID_SUPERADMIN_CIBLE --archive-sha256 SHA256_DE_TRANSFER_JSON \
  --output /root/migration-projet/plan.json --submit
```

5. Examiner dans Kolab les nouveaux IDs, sous-domaines, budgets RAM/disque, liaisons, noms/scopes des variables, tâches, volumes, workers et domaines. Les valeurs des variables et commandes planifiées ne sont jamais renvoyées dans le plan public. Confirmer le nom exact du projet pour approuver cette révision. Le plan d’import expire après 30 minutes ; en créer un nouveau s’il a expiré.
6. Exécuter l’import approuvé sur la cible.

```sh
sudo python3 /opt/pockethost-web/web_project_migration.py import \
  --plan /root/migration-projet/plan.json \
  --directory /root/migration-projet/restored \
  --config /etc/kolab/project-migration.json
```

Avant de matérialiser les ressources de la cible, le moteur exécute une recette dans Docker 1002 : bases copiées, réseau externe absent, front contrôlé par HTTP, workers conservés inertes, nettoyage vérifié. La preuve correspond aux révisions exactes de la capsule et du manifeste. Un échec garde le journal privé et le plan échoué ; aucune application importée n’est démarrée.

Les réservations RAM natives explicites sont conservées sur les nouveaux IDs, sous le verrou commun de politique mémoire. La capacité tient compte des engagements déjà présents. La réservation de la source reste intacte. Une réservation de 32 Gio ne devient pas implicitement une limite automatique de 4 Gio.

## Blocages et déverrouillage

Les variables contenant une URL de domaine généré ou de base publique déclarée sont réécrites sur leurs frontières de nom d’hôte. Les URLs de liaisons localhost gardent leur port. Une URL utilisée au build qui change bloque la mise en service : l’image doit être reconstruite avec les valeurs cibles. Un fichier privé `--overrides` peut remplacer explicitement des valeurs existantes, commandes planifiées et sous-domaines ; il ne permet pas d’ajouter des ressources étrangères au projet.

Les hooks/migrations natifs personnalisés sont copiés dans des répertoires désactivés et bloquent le déverrouillage en attente d’une revue. Une base partagée avec des applications hors du projet et une dépendance externe non couverte bloquent également. Une simple copie des données ne garantit pas la cohérence entre des écritures distribuées.

Après recette et résolution des blocages, le bouton de déverrouillage enlève les verrous du plan sans démarrer les services. Les workers source doivent avoir été rendus inertes avant de rendre leur copie utilisable. Les bases/apps doivent ensuite être démarrées explicitement et leur fonctionnement vérifié. Les mails, crons et auto-déploiements restent désactivés ; réactiver chaque fonctionnalité seulement au moment de la bascule. Reconnecter les sources Git et services de registre propres à la cible avant tout nouveau build. Les variables privées du projet sont conservées, mais les identifiants d’envoi connus sont neutralisés dans l’environnement exécutable de la copie.

## Bascule réelle et Cloudflare

Une copie faite pendant que la source écrit est une **copie de recette**. Elle ne peut pas servir à la bascule DNS. Pour une migration réelle, préparer le gel métier des écritures ainsi que l’arrêt logique des crons et workers **avant la capture**. Kolab vérifie que ses tâches source sont désactivées et enregistre une attestation opérateur dans la capsule :

```sh
sudo python3 /opt/pockethost-web/web_project_migration.py export \
  --project-id ID_PROJET_SOURCE --revision SHA_REVISION_SOURCE \
  --config /etc/kolab/project-migration.json \
  --output /root/migration-projet-fige \
  --source-writes-paused-confirmation ID_PROJET_SOURCE
```

Cette attestation est déclarative pour les écritures métier et les workers : l’outil ne peut pas imposer un mode lecture seule universel à une application inconnue. Elle ne doit être donnée qu’après le gel effectif. Une base partagée avec d’autres écrivains demande un plan spécifique ; aucune bascule automatique n’est autorisée dans ce cas.

Sur la cible, créer un aperçu avec un jeton Cloudflare privé **local à la cible**. La lecture n’écrit aucun DNS. Les enregistrements A/CNAME existants et AAAA sont affichés face à la nouvelle adresse. Le proxy Cloudflare reste activé. Les zones à origines multiples sont refusées pour revue manuelle.

```sh
sudo python3 /opt/pockethost-web/web_project_migration.py dns-preview \
  --plan /root/migration-projet/plan.json \
  --cloudflare-token-file /root/cloudflare-token \
  --output /root/migration-projet/dns-preview.json --submit
```

L’aperçu a sa propre validité de 15 minutes, indépendante de l’expiration du plan déjà importé. Kolab exige son SHA exact, la confirmation du gel source, de la vérification cible et de la limite de retour arrière. Un projet Compose avec worker exige aussi son front en service.

```sh
sudo python3 /opt/pockethost-web/web_project_migration.py dns-cutover \
  --preview /root/migration-projet/dns-preview.json \
  --approval SHA_APERCU_APPROUVE \
  --journal /root/migration-projet/dns-journal.json \
  --cloudflare-token-file /root/cloudflare-token
```

Chaque intention DNS et ID connu sont journalisés et synchronisés sur disque avant l’appel externe. Un POST à réponse perdue est reconnu grâce à un marqueur propre à la migration. Un échec tente le retour arrière CAS ; des modifications DNS effectuées entre-temps par un autre opérateur ne sont jamais écrasées.

## Retour arrière

Avant déverrouillage, le retrait automatique ne concerne que les copies suspendues et intactes. Les fichiers, volumes et engagements RAM sont contrôlés **avant** toute suppression. Le verrou local empêche deux CLI de manipuler le même plan. Après interruption du processus, relancer le retour arrière réconcilie un import orphelin ; un processus encore actif garde son verrou.

```sh
sudo python3 /opt/pockethost-web/web_project_migration.py rollback \
  --plan /root/migration-projet/plan.json \
  --config /etc/kolab/project-migration.json
sudo python3 /opt/pockethost-web/web_project_migration.py dns-rollback \
  --journal /root/migration-projet/dns-journal.json \
  --cloudflare-token-file /root/cloudflare-token
```

Le retour DNS rétablit le routage. Il **ne réconcilie pas** les nouvelles écritures faites sur la cible. Une copie déjà déverrouillée ou modifiée nécessite une décision métier et une procédure de retour spécifique. Les données et processus de la source ne sont jamais supprimés par cet assistant.

## Vérifications

- `python3 packages/pockethost/scripts/test-web-project-migration.py` : mappings exacts, budgets préservés, secrets absents des vues, journaux atomiques, modes sûrs, CAS et réponses DNS perdues.
- `test-web-project-migration-api.py` : PocketBase jetable, TOTP, clés locales/droits, chiffrement, admission, verrous, consentements et rollback des métadonnées.
- `test-web-project-migration-docker.py` : import et rollback sur Docker réel à partir d’une capsule **jetable** app4→app5, drill 1002 et vérification des volumes/bases. Aucun vrai projet ni DNS ne doit être choisi pour cette recette.
