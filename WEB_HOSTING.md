# Applications web — pilote app.monappli.re

Le module est facultatif et désactivé par défaut. Il est ouvert aux comptes dashboard `superAdmin` non suspendus et aux membres d’un projet web (voir « Droits par projet »). Le pilote a été validé sur app1 (`141.94.92.92`). L’activation de l’hébergement sur app2 et app3 a ensuite été demandée explicitement ; chaque serveur conserve son domaine, ses clés et ses connexions propres.

## Séparation et ressources

`packages/pockethost/scripts/setup-web-hosting.sh` prépare l'utilisateur `pockethost-web` et son moteur Docker rootless indépendant. Il ne met pas à jour Docker et ne redémarre ni le moteur Docker des bases ni les services PM2 existants. Exécuter avec sudo après revue des prérequis. La délégation cgroup et le plafond utilisateur concernent uniquement ce nouvel utilisateur.

Le worker Python utilise explicitement `/run/user/<uid>/docker.sock`, contrôle rootless/cgroupv2/systemd et inspecte les quotas du builder avant de construire. Une seule construction est exécutée à la fois, avec un builder limité à1CPU/3Gio ; une application utilise par défaut0,5CPU/768Mio. Le pilote app1 dispose désormais d’un plafond global de2CPU/6Gio, configuré avec `WEB_APPS_MEMORY_MAX=6G`. Le script d’installation conserve5Gio par défaut. L’admission vérifie les limites cgroup effectives, leur consommation et les quotas déjà réservés, avec512Mio de marge, notamment pendant la coexistence de deux versions. Les constructions sont suspendues sous15Gio d’espace disque libre et limitées à30minutes. Ces limites ne fournissent pas l’isolation matérielle d’une VM.

Les conteneurs n'ont ni socket Docker, ni données PocketBase montées. Leurs ports ne sont publiés que sur127.0.0.1. Les images, caches, fichiers temporaires et historiques web sont distincts ; les nettoyages ne doivent jamais exécuter un `docker system prune` global.

## Configuration

Le contrôleur et le firewall reçoivent `WEB_APPS_ENABLED=true` et `WEB_APPS_ROOT`. Le contrôleur reçoit aussi `WEB_APPS_MASTER_KEY` (exactement32caractères aléatoires), `WEB_APPS_WORKER_SECRET`, `WEB_APPS_PUBLIC_IP` et le chemin absolu `WEB_APPS_DOMAIN_HELPER`. Le dashboard doit être construit avec `PUBLIC_WEB_APPS_ENABLED=true`.

Le worker reçoit uniquement `WEB_APPS_ROOT`, `WEB_APPS_DOCKER_SOCKET`, `WEB_APPS_CONTROL_URL=http://127.0.0.1:8091`, `WEB_APPS_WORKER_SECRET`, `WEB_APPS_PUBLIC_IP` et `WEB_APPS_CERT_EMAIL`. Son fichier d'environnement appartient à l'utilisateur web en0600. Il ne reçoit pas les identifiants superutilisateur de la mothership ni la clé maître. La clé maître et le secret worker sont conservés dans le fichier privé du contrôleur, jamais dans le dépôt ou les logs. Sauvegarder la clé maître séparément des données chiffrées.

Le processus firewall doit appartenir au groupe `pockethost-web` pour lire les routes et certificats (répertoires0750, fichiers0640). Un daemon PM2 lancé avant l'ajout au groupe ne transmet pas automatiquement les nouveaux groupes à ses enfants : réinitialiser les groupes pour le firewall lors de son démarrage, sans redémarrer les instances PocketBase. Le contrôleur ne lit pas les clés TLS et délègue Certbot au worker.

## Limites facultatives par projet

Depuis le 30 octobre 2026, les trois serveurs proposent **Applications web → projet → Configuration** (également dans la configuration du projet depuis les environnements). Les administrateurs et propriétaires du projet, ainsi que les superAdmins, peuvent activer séparément :

- le débit : requêtes par minute et connexions simultanées par IP, compteurs communs à tous les sites et domaines du projet, WebSockets compris ; dépassement → HTTP 429 avec `Retry-After` ;
- les ressources : CPU et RAM réservées cumulées des services actifs du projet, durée maximale de construction et nombre de déploiements en attente. Les déploiements automatiques respectent aussi cette limite de file ; la suppression des previews et des routes reste possible.

Les deux cases sont **désactivées par défaut**, y compris pour les projets existants. Les valeurs proposées avant activation sont 600 requêtes/minute/IP, 50 connexions simultanées/IP, 2 000 millicœurs, 3 072 Mio, 15 minutes de construction et 3 traitements en attente. Les plafonds de sécurité du serveur restent applicables.

Les changements se propagent en quelques secondes. CPU/RAM contrôlent l'admission avant un nouveau démarrage et comptent la version encore publiée : prévoir la place pour les deux versions pendant un déploiement. Réduire un quota n'arrête pas les services existants. Un candidat refusé conserve le site publié.

L'API `PATCH /api/web-projects/{id}` accepte `limits: { traffic: { enabled, requestsPerMinute, concurrentRequests }, resources: { enabled, cpuMillicores, memoryMiB, buildMinutes, queuedBuilds } }` ; la migration additive `1793271600` stocke ce JSON sur `web_projects`. Le worker publie un registre privé atomique, consulté par le pare-feu. Aucun client ne fournit son propre quota ni son identité IP de confiance.

Ces réglages ne modifient pas le limiteur natif global PocketBase : il reste désactivé sur app1/app2/app3. La migration historique `1793271500` préserve désormais son état d'activation afin qu'une prochaine livraison ne l'active pas implicitement.

## Connexion privée aux bases PocketBase

Dans **Bases de données → base → Utilisation → Accès privé depuis l’hébergement**, choisir une application du même serveur, un port local libre (par défaut `4825`), puis cocher **Autoriser cette application à utiliser la connexion privée**. Il faut pouvoir gérer la base et configurer l’application. La fiche affiche l’adresse `http://127.0.0.1:4825`, la variable serveur `KOLAB_DB_4825_URL` et l’état de la liaison. Quatre liaisons au maximum sont conservées par application ; les ports vont de 1024 à 65535 et ne peuvent occuper le port 3000, un port déclaré par un service de l’application ou celui d’une autre liaison.

L’application peut utiliser cette adresse directement dans son code côté serveur, ou lire `process.env.KOLAB_DB_4825_URL`. L’authentification et les règles d’accès PocketBase restent nécessaires : la liaison ne crée aucun compte administrateur. Lors d’une première activation, ou si la variable manque, démarrer à nouveau ou redéployer l’application pour recevoir les montages et la variable. La base ne redémarre pas. Les modifications suivantes des liaisons existantes se propagent en quelques secondes ; désactiver ou retirer une liaison ferme aussi ses connexions déjà ouvertes.

Cette adresse est utilisable **uniquement dans le runtime du site** : elle n’existe ni pendant la construction, ni dans le navigateur, ni sur un autre serveur. Ne pas la publier dans une variable `NEXT_PUBLIC_*` ou un autre paramètre compilé pour le navigateur. Les appels du navigateur conservent l’URL publique habituelle. La connexion privée évite Cloudflare et l’IP publique ; elle ne désactive ni ne ferme l’adresse publique de la base. Une base arrêtée n’est pas réveillée automatiquement et sa liaison reste indisponible jusqu’à son redémarrage volontaire.

Le proxy écoute seulement sur `127.0.0.1` dans l’espace réseau de chaque service de l’application. Un socket Unix propre à l’application et un jeton de liaison le relient au broker hôte. Celui-ci ne reçoit aucun socket Docker : il connecte un second socket Unix vers un auxiliaire limité à `127.0.0.1:8090` dans l’espace réseau de la base, épinglé au conteneur et à son démarrage. Aucun port Docker publié n’est utilisé pour identifier une base. Une révocation, un changement de jeton ou de cible ferme les flux existants ; après dix secondes sans politique valide, le broker refuse et ferme les accès. HTTP et WebSocket sont transportés sans modifier le protocole PocketBase.

Chaque auxiliaire réserve 96 Mio, 50 millicœurs et 64 processus au maximum. Le proxy d’un service web compte dans l’admission des ressources du projet et du worker ; un auxiliaire distinct est partagé par les liaisons vers une même base active. Les préchargements `NODE_OPTIONS`, `NODE_PATH`, `LD_PRELOAD` et `LD_LIBRARY_PATH` de l’application sont neutralisés pour le wrapper de démarrage de confiance, puis restaurés seulement pour la commande d’origine après ouverture des écouteurs et validation de la liaison privée vers les bases actives. Le marqueur de démarrage associe l’espace réseau aux ticks de démarrage du PID1 de confiance, afin qu’un redémarrage Docker réutilisant le même espace réseau n’accepte jamais un ancien marqueur. Cette vérification n’envoie aucune requête métier. Une base explicitement arrêtée ne bloque pas le démarrage du site.

Les contrôles Docker des auxiliaires disposent de cinq secondes pour lancer Node avec le plafond de 50 millicœurs ; un démarrage à froid peut prendre plusieurs secondes sous ce quota. Leur fréquence reste de deux secondes pour le proxy web et cinq secondes pour la cible BDD. Cette marge ne change ni les ressources accordées ni le contrôle de disponibilité exigé avant le démarrage de l’application.

Le worker vérifie le proxy depuis l’hôte : il lit son état JSON avec les outils légers de l’image officielle du helper, en contrôle la fraîcheur et le hash de configuration, puis effectue le handshake sur le socket Unix. La lecture exige l’image immuable exacte, le rôle helper, l’utilisateur 10001 et une racine en lecture seule ; aucun binaire d’une image utilisateur n’est exécuté. Elle refuse les liens et fichiers spéciaux, se limite à 1 Kio et dispose de deux secondes. Aucun processus Node supplémentaire n’est lancé dans l’auxiliaire. Avant de publier le marqueur, le worker vérifie que le socket hôte conserve l’inode monté par le proxy. Une preuve inchangée et un helper Docker sain évitent de répéter ces commandes à chaque passage.

### Installation du relais privé

L’activation requiert les sources backend et worker, la migration additive `1793271900_created_web_private_db_bindings.js`, et le dashboard compilé correspondant. Les scripts `web_private_db_broker.py`, `web_private_db_provider.py`, `web_private_db_proxy.cjs`, `web_private_db_target.cjs`, `web_private_db_wait.cjs`, `install-private-db-bridge.py` et le modèle `web-private-db-broker.service` doivent être installés dans `/opt/pockethost-web`, appartenir à root et ne pas être modifiables par le groupe ou les autres utilisateurs. L’image Node officielle de la cible doit déjà être présente dans le moteur Docker des bases, sous une référence `node@sha256:…` vérifiée ; l’installateur ne télécharge pas d’image.

Après sauvegarde des fichiers de configuration et vidage des opérations de déploiement/stockage en cours, configurer le relais avec la release réelle du contrôleur et le digest vérifié :

```sh
sudo python3 /opt/pockethost-web/install-private-db-bridge.py \
  --controller-root '<RELEASE_MOTHERSHIP>' \
  --target-image 'node@sha256:<DIGEST_VERIFIE>'
```

L’installateur crée ou conserve le secret `/etc/kolab-db-bridge.env` (root, 0600), l’ajoute uniquement au contrôleur, active `WEB_DB_BRIDGE_ENABLED` côté contrôleur/worker, et installe `kolab-db-bridge.service`. Il prépare `/run/kolab-db-targets` en `ubuntu:ubuntu`, 0700, recréé au boot par `tmpfiles.d`. Les dossiers d’instance sont en 0700, les feuilles de cible en 0770 et leurs sockets en `10001:1000`, 0660. Le broker est exécuté par `ubuntu`, groupe `pockethost-web`, avec le groupe supplémentaire `ubuntu` ; les sockets des moteurs Docker lui sont interdits. Le worker et son moteur rootless ne peuvent traverser la racine des cibles.

Le contrôleur charge `.env` ; `.env.local` ne configure pas cette CLI. Si la release contient un lien `.env` vers le fichier partagé `/home/ubuntu/pockethost/.env`, l’installateur conserve ce lien et modifie seulement sa cible canonique. Celle-ci doit être un fichier privé, appartenir à Ubuntu et rester sous son home. La sauvegarde et le retour arrière portent sur cette même cible.

L’installateur ne redémarre aucun service. Une fois les fichiers et sauvegardes prêts, la livraison recharge le contrôleur et le worker, puis vérifie le broker :

```sh
sudo -u ubuntu pm2 restart mothership --update-env
sudo systemctl restart kolab-db-bridge.service
sudo systemctl restart pockethost-web-worker.service
sudo systemctl is-active kolab-db-bridge.service
sudo journalctl -u kolab-db-bridge.service -n 30 --no-pager
```

Ces rechargements ne demandent aucun redémarrage de Docker, des bases ou des sites publiés. Les journaux du broker doivent afficher `bridge: ready`, sans jeton ni contenu de requête. Le répertoire `/var/lib/kolab-db-bridge`, créé par `StateDirectory`, appartient à `ubuntu:pockethost-web` et reste en 0710. Il est visible du moteur rootless existant, contrairement à un nouveau dossier sous `/run`, dont RootlessKit peut conserver une copie privée. Les cibles des bases restent sous `/run/kolab-db-targets`, en 0700. Un socket d’application conserve son inode tant que le broker tourne, même sans liaison. Un redémarrage du broker recrée les inodes et le worker recrée alors les proxies concernés. Pour revenir en arrière, restaurer les fichiers/env sauvegardés et l’état actif/activé antérieur de l’unité ; ne pas restaurer automatiquement une ancienne base de contrôle par-dessus les écritures de production.

## Supervision de la plateforme

La page **Supervision** (`/monitoring`) est réservée aux superAdmins non suspendus. Le collecteur PM2 `health-check` publie environ chaque minute un état privé sous `DATA_ROOT/monitoring/status.json` ; le dashboard le relit toutes les 15 secondes. Un état de plus de trois minutes est périmé. Les arrêts volontaires, bases en veille et fonctions désactivées restent distincts des pannes ; une sonde ne réveille jamais une base inactive.

Le contrôle couvre les services PM2, le contrôleur et l’origine HTTPS, FTP/SFTP, le disque réel des données, la RAM disponible sous Linux, la charge, les descripteurs et le certificat ; chaque base active est sondée sur son port local `/api/health`. L’hébergement ajoute la fraîcheur du worker/rootless et des mesures, la publication et sa route HTTP, la RAM réservée, la file de déploiement et les volumes. Les planifications de sauvegarde BDD/web et l’état local Litestream sont également suivis. Une sonde HTTP ne valide pas les parcours métier ; l’état Litestream n’atteste pas une restauration complète.

Les serveurs peuvent se surveiller mutuellement via `PLATFORM_MONITOR_PEERS`, tableau JSON `{host,address}` fourni au seul collecteur. Les contrôles interrogent l’IP d’origine avec le nom TLS attendu, sans désactiver la validation du certificat ni partager d’identifiants entre serveurs. Chaque page expose les ressources de son propre serveur et les contrôles de disponibilité de ses voisins.

Dans **Supervision → Notifications**, e-mail et Discord sont configurables et activables séparément pour ce serveur. E-mail utilise le SMTP configuré dans Administration et accepte jusqu’à 20 destinataires ; Discord exige un webhook officiel. Aucun canal n’est activé automatiquement. Panne/avertissement et retour à la normale sont confirmés sur deux passages ; les incidents sont dédoublonnés, avec un résumé horaire. Les compteurs sont indépendants par canal pour éviter qu’un échec Discord fasse réémettre les e-mails déjà confirmés. L’acceptation SMTP ne garantit pas la réception en boîte.

La migration additive `1793271700` crée `platform_monitoring_settings`, fermée aux API natives des utilisateurs. Le webhook est chiffré avec `PLATFORM_MONITORING_KEY` (32 caractères), ou la clé privée existante `WEB_APPS_MASTER_KEY`. Le dashboard reçoit uniquement `hasDiscordWebhook` ; une valeur laissée vide conserve le secret. La configuration a une révision pour refuser les écrasements concurrents. La route privée de livraison exige un superutilisateur natif, le pair TCP loopback et aucun en-tête de relais. Le collecteur conserve un cache de livraison en 0600 pour continuer à alerter lorsque le contrôleur ne répond plus. `health check --no-notify` actualise les mesures sans envoi externe.

## Droits par projet

Un projet web regroupe des applications (dev, staging, prod, previews). Les membres sont stockés dans `web_project_members` (migration `1793270700`, règles API nulles) ; toutes les vérifications passent par `requireWebPermission` / `requirePlatformAdmin` (`mothership-app/src/lib/handlers/webApps/access.ts`), avec la table permission → rôle minimal dans `packages/pockethost/src/common/webAccess.ts`, partagée avec le dashboard. Un compte suspendu n’a aucun accès. Un non-membre reçoit le même 404 qu’un identifiant inexistant ; un membre au rôle insuffisant reçoit 403 « Droits insuffisants sur ce projet. ».

| Rôle | Peut faire |
| --- | --- |
| viewer | État du projet et des applications, historique et journaux de build, journaux du site, santé, historique des tâches, sauvegardes et alertes, domaines, liste des membres, **noms** des variables. |
| developer | viewer + déployer, relancer, annuler, rollback, appliquer sans reconstruire, écrire les variables (sans les révéler), lancer une tâche à la main, sauvegarde manuelle, previews. |
| admin | developer + révéler/exporter les variables, terminal, domaines, restauration et téléchargement des sauvegardes, volumes, canaux d’alerte (destinataires inclus), définitions des tâches, santé, jetons CI, configuration (branche, dossier, autoDeploy, moteur), clonage d’environnement, membres de rôle inférieur à admin. |
| owner | admin + gérer les admins et propriétaires, renommer le projet (`project.delete` est prévu, sans route de suppression aujourd’hui). Un projet garde toujours au moins un propriétaire. |

**Réservé au superAdmin de la plateforme** : connexion et diagnostic GitHub App, réglages Applications web, reprise globale (`/applications/recovery`), création d’application et de projet, rattachement/détachement d’une application à un projet, attribution de projets à un utilisateur (`/api/web-access/users/{userId}/projects`), liste des dépôts autorisés et changement de dépôt hors de cette liste. Les applications sans projet (« legacy ») restent visibles du seul superAdmin. Aucun membre n’est créé automatiquement : les superAdmins gardent tout, les équipes sont ajoutées explicitement.

**Dépôts autorisés** : la GitHub App est partagée par tout le serveur ; un admin ne doit pas pouvoir brancher le dépôt privé d’une autre équipe. `web_projects.allowedRepositories` (liste « propriétaire/dépôt », 50 au maximum, modifiable par un superAdmin dans les réglages du projet) indique les dépôts qu’un admin peut attribuer ; liste vide = seul le superAdmin change le dépôt. Changer de branche dans le dépôt actuel reste un droit admin. Une application d’un projet doit de plus garder le dépôt du projet : en pratique la liste sert à rebrancher le dépôt du projet sur une application (par exemple passage d’une image à une construction source) ; changer réellement de dépôt passe par un détachement, réservé au superAdmin. La liste des branches pour un non-superAdmin exige `?appId=` et vérifie que le dépôt renvoyé par GitHub est celui de l’application.

**Identifiants longue durée** : un jeton CI est revérifié à chaque appel contre `ci.manage` de son auteur (rétrogradé sous admin, retiré ou suspendu → 401 « Accès de déploiement révoqué. ») ; une session de terminal est revérifiée à chaque contrôle périodique du broker (`terminal.open`) et se ferme avec `access_revoked`. Les tâches planifiées et les alertes sont des réglages de l’application : elles restent actives tant que l’application appartient à un projet ; les droits sont vérifiés à l’écriture et au lancement manuel. Une restauration de sauvegarde vers un projet de reprise recopie les membres du projet d’origine.

**Inviter quelqu’un** : le compte doit déjà exister sur le dashboard (aucune création de compte, réponse générique si l’adresse est inconnue ou suspendue). Un admin ou un propriétaire ouvre l’onglet « Membres » d’une application du projet, ou le panneau « Membres » d’un projet dans Applications web → Vos projets, saisit l’email et choisit un rôle parmi ceux qu’il peut attribuer (un admin n’attribue que developer ou viewer). Le rôle se change ou se retire dans la même liste ; chacun peut quitter un projet sauf le dernier propriétaire. L’historique des accès (`web_project_audit` : `member-added`, `member-role-changed`, `member-removed`, `member-left`) est consultable dans ce panneau. API : `GET /api/web-access/me` (adhésions + capacités), `GET|POST /api/web-projects/{projectId}/members`, `PATCH|DELETE …/members/{memberId}`, `POST …/members/leave`, `GET …/members/audit`.

Tests : `scripts/test-web-access-api.py` (toutes les routes × rôles, IDOR, liste de dépôts, révocation CI/terminal), `test-web-project-members-api.py`, `test-web-ops-roles-api.py`, `test-web-app-rbac-api.py`, `test-web-project-rbac-api.py`.

## GitHub et publication

Dans Applications web → Connexion GitHub, utiliser le parcours guidé de création et d’installation de la GitHub App, puis sélectionner les seuls dépôts à autoriser. Les droits de base portent sur la lecture du code et des métadonnées, avec les notifications de push. Les previews demandent en plus la lecture des pull requests et leurs événements ; une installation existante doit accepter cette évolution sur GitHub via « Review request ». Le diagnostic distingue cette attente de la connexion utilisée pour les push. Le formulaire manuel App ID/Installation ID/clé PEM reste disponible dans les réglages avancés. Aucun jeton personnel GitHub global ne doit être copié sur le serveur.

### Plusieurs connexions GitHub (implémentées localement, non déployées)

Un serveur accepte jusqu'à 10 connexions GitHub simultanées, par exemple un compte personnel et une organisation. Chaque connexion est une GitHub App installée sur un seul compte ou une seule organisation. Les Apps créées par l'assistant sont privées : chaque organisation demande donc sa propre connexion. Elles se gèrent dans Applications web → Paramètres → « Connexions GitHub », par les seuls administrateurs de la plateforme. Les membres d'un projet voient au plus le nombre de connexions.

- **Connexion principale.** C'est la connexion existante (ligne `github` de `web_app_settings`, inchangée octet pour octet par la migration `1793271300_updated_web_github_connections.js`). Elle garde l'URL de webhook historique `https://<APP_HOST>/api/web-apps/github/webhook`. Sur app1, app2 et app3, aucune action n'est nécessaire côté GitHub. Les applications sans connexion explicite restent sur la principale.
- **Connexions supplémentaires.** Chaque connexion supplémentaire a son propre identifiant de 15 caractères, sa clé privée et son secret webhook chiffrés (ligne `github:<id>`), et sa propre URL `https://<APP_HOST>/api/web-apps/github/webhook/<id>`. `/webhook/default` n'existe pas. L'assistant « Ajouter une connexion GitHub » fixe cette URL lui-même et ne modifie pas les connexions existantes. En saisie manuelle (libellé, App ID, slug, Installation ID, clé PEM, secret), recopier dans la GitHub App l'URL affichée sur la carte. Une App et une installation ne peuvent appartenir qu'à une seule connexion.
- **Routage et sécurité.** Le webhook est routé par son chemin avant le contrôle HMAC. Chemin inconnu, signature absente ou invalide : même réponse 401 « Signature GitHub invalide. ». Une installation qui n'est pas celle de la connexion reçoit 403. Un même dépôt peut être servi par deux connexions : seules les applications rattachées à la connexion qui reçoit le push sont déployées. Le pare-feu (`webhookException.ts`) n'accepte que les chemins exacts (legacy et `/webhook/<[a-z0-9]{15}>`). **Il doit être déployé avant la création de la 2e connexion**, sinon GitHub est bloqué à l'entrée.
- **Applications.** À la création, si plusieurs connexions sont actives, le champ « Connexion GitHub » apparaît avant le sélecteur de dépôt (principale présélectionnée). Les dépôts et les branches sont lus avec la connexion choisie. Changer la connexion d'une application est réservé à la plateforme. Le droit est vérifié avant toute lecture de l'identifiant : un membre de projet reçoit la même 403, que la connexion existe ou non. Le rattachement relit la connexion dans la transaction d'écriture, donc une connexion supprimée en parallèle est refusée (400) au lieu de laisser une application orpheline. Le dépôt doit être autorisé dans l'installation choisie. Le worker reçoit uniquement l'App ID, l'Installation ID et la clé de la connexion de l'application, relus au moment du claim : une rotation s'applique donc aux jobs en file. Une application « Dépôt Git » ne reçoit jamais de clé de GitHub App. La CI, les relances et l'historique des livraisons (`web_webhook_deliveries.connectionId`) suivent la connexion de l'application.
- **Previews.** Une livraison `pull_request` n'agit que sur les previews servies par la connexion qui l'a reçue : celle de l'application modèle, ou, si le modèle est une source « Dépôt Git » ou a disparu, celle de l'application de la preview. Un modèle introuvable n'interrompt plus le traitement des autres projets : la note « Le modèle de preview est introuvable. » est enregistrée.
- **Désactiver.** Effet immédiat :
  - un push ou une pull request signé est accepté puis ignoré (200, livraison `ignored`) ;
  - la lecture des dépôts et des branches, le déploiement manuel et la CI répondent 400 ;
  - un déploiement qui construit des sources échoue au claim avec « Connexion GitHub indisponible : rattachez l'application à une connexion active, puis redéployez. » ;
  - `stop`, `start`, rollback, domaine et restauration ne sont pas affectés ;
  - la santé reste consultable.
- **Supprimer.** La suppression est refusée tant que des applications (y compris celles rattachées implicitement à la principale) ou des déploiements en file utilisent la connexion. Le décompte parcourt toutes les applications et tous les jobs, sans plafond. Elle efface la clé privée et le secret de PocketHost. Il faut ensuite supprimer la GitHub App sur GitHub (lien « Gérer sur GitHub »). Si la principale est supprimée, le chemin legacy répond 401 ; la connexion ajoutée ensuite redevient la principale, sur l'URL legacy.
- **Diagnostic.** Chaque carte indique la santé, le compte, l'App, le nombre d'applications et la dernière livraison reçue. Elle signale aussi le décalage entre l'URL configurée sur GitHub et celle de la connexion (« les pushs ne sont pas reçus »). La comparaison ignore la casse du schéma et de l'hôte, le port par défaut, le `/` final et la requête. Un chemin différent est toujours signalé. Un autre hôte (alias, URL saisie à la main avant l'assistant) ne l'est pas si la connexion a reçu une livraison depuis moins de 24 h.
- **Sessions de l'assistant d'avant la mise à jour.** Une session commencée avant la mise à jour remplace la principale si elle existe. Sinon, elle la crée, comme l'ancien code.
- **Restauration.** Une sauvegarde dont la configuration utilise une autre connexion GitHub ne peut être restaurée que par un administrateur de la plateforme. Si la connexion a été supprimée, la restauration réussit mais le déploiement échoue avec le message clair ; un administrateur de la plateforme rattache alors l'application à une autre connexion.
- **Reprise.** La dé-pause avec `--rotate-webhooks` tourne le secret webhook de chaque connexion. `rotated-webhook-secrets.json` contient `githubWebhookSecret` (principale) et `githubWebhookSecrets` (un secret et le slug d'App par connexion), à recopier dans chaque GitHub App. Les clés privées ne se tournent pas hors ligne.
- **Ordre de déploiement.** Pare-feu, puis mothership (bundle et migration, un redémarrage), dashboard, scripts de reprise. Vérifier ensuite sur chaque serveur :
  - `GET /api/web-apps/github/connections` renvoie une seule « Connexion principale » avec l'URL legacy ;
  - un push réel déploie ;
  - la santé est verte.

  N'ajouter la 2e connexion qu'après ces vérifications.
- **Retour arrière.** Le retour du code seul est sûr tant qu'aucune connexion supplémentaire n'existe et que la principale est active. Les livraisons reçues par l'ancien code pendant ce retour sont rattachées à la principale au redémarrage suivant du nouveau code. Sinon, procéder dans cet ordre :
  1. rattacher les applications à la principale ;
  2. supprimer les autres connexions et leurs Apps sur GitHub ;
  3. si la principale est désactivée, suspendre son App sur GitHub puis la réactiver dans PocketHost : l'ancien code ignore la désactivation ;
  4. lancer `migrate down`. Il refuse tant que la principale est désactivée (vérification avec `WEB_APPS_MASTER_KEY`, simple avertissement sans la clé), qu'il reste des lignes `github:<id>` ou des applications avec `githubConnectionId` ;
  5. redémarrer l'ancien code.

  Sans ces étapes, l'ancien code lirait silencieusement ces applications avec la principale.

Le premier pilote peut être importé depuis une archive du dépôt récupérée en local avec l'accès GitHub de l'opérateur. Cet essai vérifie l'hébergement ; il ne prouve pas une connexion GitHub App ni un véritable déclenchement par push. Un dépôt privé nécessite cette connexion pour ses prochains déploiements automatiques.

La V1 accepte npm/pnpm/Yarn avec un seul lockfile et un dossier relatif de projet. Elle privilégie la sortie Next.js standalone et utilise sinon le serveur `next start`, sans réécrire la configuration Git. Les variables de construction et d'exécution sont séparées ; les variables NEXT_PUBLIC sont publiques dans les fichiers du navigateur. Ne pas placer de données persistantes dans le filesystem éphémère du conteneur.

Les déploiements conservent l'ancienne version jusqu'au contrôle de santé et à la publication confirmée. Un retour arrière réutilise une image précédente et ses variables, sans rollback de base ni migration automatique. Les trois dernières images réussies et leurs fichiers statiques sont conservés. Le rollback peut échouer si une ressource externe ou un ancien secret n'est plus valide ; dans ce cas le site courant reste publié.

L’export des fichiers statiques Next.js est limité à 256 Mio, 20 000 entrées et 120 secondes par déploiement. Le worker conserve 15 Gio de réserve disque, refuse les liens, fichiers spéciaux et chemins sortants, puis publie le répertoire complet par renommage. Un dépassement ou une annulation supprime l’export partiel et conserve le site publié.

BuildKit utilise un réseau bridge dédié et son worker OCI utilise aussi le mode bridge. Les permissions `network.host` et `security.insecure` sont refusées par le daemon. Buildx 0.30 réintroduit automatiquement `network.host` lors de la création : le worker retire donc les permissions dans la définition de son unique builder, sous le verrou du magasin Buildx, avant son premier démarrage. Une définition inconnue bloque le démarrage ; les quotas, le réseau et la commande effective du conteneur sont contrôlés après démarrage. La recréation conserve le volume de cache avec `--keep-state`.

## Dépôt Git avec clé de déploiement

Une application peut être construite depuis un dépôt Git hors de la GitHub App : GitLab, Gitea, Forgejo, Bitbucket ou un serveur SSH auto-hébergé. GitHub fonctionne aussi de cette façon. La source se choisit dans Configuration → Source (« Dépôt Git (clé de déploiement) »), ou à la création d’une application. Ce choix est réservé aux administrateurs de la plateforme (`source.git.configure`).

**Principe.** Le serveur génère une clé Ed25519 par dépôt. GitHub refuse la même clé sur deux dépôts. La clé est partagée par les applications et les environnements de ce dépôt, et un dépôt n’appartient qu’à un seul projet. Ajoutez la clé publique au dépôt **en lecture seule**. La clé privée est chiffrée avec `WEB_APPS_MASTER_KEY` dans `web_git_sources`. Elle n’est jamais renvoyée par l’API et ne quitte le contrôleur que vers le worker local, dans le job de déploiement. Le worker la masque dans les journaux. `web_apps.config` ne contient que `source: "git"`, `gitSourceId` et `repository = "git:" + canonique`. Le serveur réécrit toujours ce dépôt depuis la source.

**Adresse acceptée.** Deux formes seulement : `utilisateur@hôte:chemin` et `ssh://[utilisateur@]hôte[:port]/chemin`. Tout le reste est refusé : https, `git://`, `file:`, `ext::`, IP littérales, noms locaux, `..`, `-` initial. Le serveur ne contacte que des adresses publiques, revérifiées à chaque connexion. La clé canonique est `[utilisateur@]hôte[:port]/chemin` sans `.git` :

- `utilisateur@` n’apparaît que si l’utilisateur n’est pas `git` : `alice@` et `bob@` sont deux dépôts distincts ;
- sur github.com, gitlab.com et bitbucket.org, le chemin est mis en minuscules et perd son `/` initial ;
- ailleurs, la casse est conservée et un chemin absolu garde son `/` : `srv.example.com//srv/app` (absolu) diffère de `srv.example.com/srv/app` (relatif). Les comparaisons `git:` sont donc exactes, alors qu’elles sont insensibles à la casse pour GitHub.

**Empreintes du serveur.**

- Pour github.com, gitlab.com et bitbucket.org sur le port 22, les empreintes publiées par l’hébergeur sont épinglées dès la création. Une analyse qui présente d’autres clés est refusée.
- Pour les autres hôtes, un administrateur du projet lance l’analyse, compare les empreintes à celles que fournit l’hébergeur, puis les confirme (TOFU explicite). La confirmation doit reprendre exactement le jeu analysé, dans les 10 minutes et une seule fois.
- Après la confirmation, seules ces clés sont acceptées (`StrictHostKeyChecking`, `HostKeyAlias` sans port).

**Par hébergeur.**

- **GitHub** : Settings → Deploy keys → Add deploy key, sans « Allow write access ». Webhook : Settings → Webhooks, type `application/json`, événement push. Signature : `X-Hub-Signature-256`.
- **GitLab** : Settings → Repository → Deploy keys, sans droit d’écriture. Webhook : Settings → Webhooks, « Push events », « Secret token ». Le jeton arrive dans `X-Gitlab-Token`.
- **Gitea / Forgejo** : Paramètres → Clés de déploiement, en lecture seule. Webhook Gitea/Forgejo, événement push, avec un secret. Signatures : `X-Gitea-Signature` / `X-Forgejo-Signature` (HMAC hexadécimal).
- **Bitbucket Cloud** : Repository settings → Access keys. Webhook « Repository push », avec un secret. Signature : `X-Hub-Signature: sha256=…`. Seule la première branche d’un push groupé est déployée.
- **Serveur SSH** : ajouter la clé publique dans `authorized_keys`, idéalement avec `git-shell` ou des restrictions `no-pty,no-port-forwarding`. Il n’y a pas de webhook standard : déployez à la main ou envoyez un POST signé façon GitHub.

L’adresse du webhook est `https://<APP_HOST>/api/web-git-sources/webhook/<identifiant>`. Le secret de 48 caractères n’est affiché qu’une fois (« Générer le secret »), puis seul l’état « configuré » reste visible. Une livraison non signée ou mal signée reçoit 401. La déduplication porte sur le corps signé : une relivraison est signalée en doublon et ne crée pas de second déploiement. Les tags, les suppressions de branche, les autres branches et les événements autres que push sont journalisés sans déploiement. La tête de branche est relue par `ls-remote` avant la mise en file, et au plus 20 livraisons par minute et par dépôt atteignent cette relecture : la suivante reçoit 429. Le pare-feu laisse passer uniquement `POST /api/web-git-sources/webhook/<24 caractères>` sur l’hôte du dashboard. L’onglet Webhooks devient « Activité Git ».

**Droits.**

| Action | Rôle requis | Permission |
|---|---|---|
| Lecture | lecteur | `app.read` : clé publique et empreintes seulement |
| Test d’accès, liste des branches | admin | `app.configure` |
| Analyse et confirmation des empreintes | admin | `source.hostKey.scan` |
| Régénération de la clé ou du secret | admin | `source.deployKey.rotate` |
| Création, suppression, rattachement à une application | plateforme | `source.git.configure` |

- L’analyse, le test, les branches et les rotations sont limités à 6 appels par minute et par dépôt : le 7e reçoit 429. Les relectures de tête faites au déploiement (bouton « Déployer », vérification CI) sont limitées à 12 par minute et par dépôt : la 13e reçoit 429.
- Un dépôt sans projet n’est visible que des superAdmins. Un dépôt d’un autre projet répond 404.
- Un dépôt est adopté par le projet de la première application rattachée. Il est refusé pour un autre projet tant qu’une application de son projet l’utilise : « Ce dépôt Git est relié à un autre projet. ».
- Le rattachement suit les applications. Quand la dernière application du projet est détachée, le dépôt n’appartient plus à aucun projet (superAdmin seul) et peut être adopté par un autre. Une restauration qui crée un projet « · récupération » y déplace le dépôt si plus aucune autre application ne l’utilise ailleurs.
- Si une application hors du projet utilise encore la clé (application détachée, sans projet ou restaurée ailleurs), seuls les administrateurs de la plateforme peuvent gérer le dépôt : les membres du projet reçoivent 403 « Ce dépôt Git sert aussi une application hors de ce projet ». Une rotation ou un nouvel épinglage casserait sinon une application dont ils n’ont pas la charge.

**Prérequis.**

- Sur le contrôleur : `python3` (avec `cryptography`, ou `ssh-keygen`), `ssh-keyscan` et `git`.
- Sur le worker : `git` et `openssh-client`.
- `scripts/setup-web-hosting.sh` installe ces paquets. Il est idempotent et ne redémarre pas Docker.
- Déployez `scripts/web_app_git.py` à côté de `web-app-worker.py`.
- Sans git sur le worker, `worker-status.json` indique `"git": false`, l’API expose `capabilities.gitAvailable: false` et le bouton « Déployer » est désactivé avec le motif « Le serveur web ne dispose pas de git/openssh. ».

**Limites.**

- Paquet récupéré : 256 Mio compressés. Arbre extrait : 2 Gio et 100 000 entrées. Le worker garde 15 Gio de réserve disque.
- Si le serveur refuse la récupération par commit (protocole Git v0, `GIT_PROTOCOL` non transmis par sshd), la récupération remonte jusqu’à 50 commits sur la branche. Pour une promotion reconstruite, c’est la branche de l’environnement source, d’où vient le commit. Une relance d’un commit plus ancien que ces 50 commits échoue sur ces serveurs.
- Les sous-modules ne sont pas récupérés : les gitlinks sont ignorés. Les pointeurs LFS restent de simples fichiers.
- **Pas d’aperçus de pull request** : ils exigent la GitHub App. Activer les previews avec un modèle « Dépôt Git » est refusé, et un webhook pull_request ne crée jamais de preview pour cette source.
- **Vérification de tête CI** : `POST /api/web-apps/ci/deploy` relit la branche par `ls-remote` avec la clé de déploiement. Si le dépôt n’est pas prêt (empreintes non confirmées, clé refusée), le déploiement CI est refusé.
- Un push de la GitHub App ne déploie jamais une application « Dépôt Git », même si le dépôt est identique.
- La liste des branches GitHub (`/api/web-apps/repositories/…/branches`) est refusée à ces applications. Le panneau « Dépôt Git » liste les branches par `ls-remote` (5 000 au maximum).

**Rotation.**

- « Régénérer la clé » demande une confirmation explicite tant que des applications l’utilisent (le compteur inclut tous les environnements). Ajoutez la nouvelle clé publique chez l’hébergeur avant le prochain déploiement, puis supprimez l’ancienne.
- Les jobs en file et les relances lisent toujours la clé actuelle au moment du claim.
- Si la source n’est plus prête à ce moment, le job échoue avec « Source Git indisponible : confirmez l’empreinte et la clé de déploiement. ».
- La suppression d’un dépôt est refusée tant qu’une application le référence. Supprimez ensuite aussi la clé chez l’hébergeur.

**Restauration.** Une copie restaurée du contrôleur conserve les secrets de webhook et les clés de déploiement, qui restent valides chez les hébergeurs. Après une reprise ou un clonage :

1. faites tourner les secrets de webhook Git (procédure de dé-pause de [WEB_RECOVERY_RUNBOOK.md](WEB_RECOVERY_RUNBOOK.md)), ou désactivez-les ;
2. régénérez chaque clé de déploiement depuis le dashboard.

Tests :

- `scripts/test-web-git-source-api.py` : PocketBase jetable, couvrant les routes × rôles, le chiffrement au repos, le parcours analyse → confirmation, les signatures GitHub/Gitea/Forgejo/Bitbucket/GitLab, le rejeu, le claim `job.git`, les previews refusées et l’égalité `git:` exacte ;
- `test-web-git-source-transport.py`, `test-web-app-git.py` ;
- vitest `webGit`, `gitSource` et `webhookException` (pare-feu).

## Construction automatique (Railpack)

Le runtime `auto` (« Automatique (Railpack) ») construit les projets qui ne sont pas en Next.js sans Dockerfile : Node, Vite, Astro, sites statiques, Python, Go, PHP, Ruby, etc. Il réutilise l’isolation existante : builder BuildKit `ph-web-builder` (3 Gio, 1 CPU) sur le Docker rootless web, admission de construction de 3072 Mio, garde de 15 Gio, 30 minutes par job.

- **Dossier analysé** : `rootDirectory` du site, et uniquement lui (copie sans `.git`, `node_modules`, `.next`, `.env*` ; liens symboliques hors du dossier refusés). Pour un monorepo dont le lockfile est à la racine, indiquer `rootDirectory='.'` puis ajuster avec `railpack.json` ou les variables `RAILPACK_*`.
- **Profil** : port 3000 par défaut, injecté en `PORT` (l’application doit l’écouter). `startCommand` et `buildCommand` facultatifs (une ligne, 1000 caractères) ; `command`/`entrypoint` sont refusés car l’image lance `bash -c`.
- **Variables** : les variables de construction deviennent des secrets BuildKit (un fichier 0600 par variable, jamais dans l’argv ni le plan ; empreinte HMAC pour le cache). Celles dont le nom commence par `RAILPACK_` sont **en plus** passées en clair à l’analyse : n’y placez pas de secret. Pour ce runtime, leurs valeurs ne sont pas masquées dans le journal (une valeur courte comme `22` ou `dist` rendrait sinon le journal illisible).
- **Exécution** : UID 10001, racine en lecture seule, `/tmp` en tmpfs ; `HOME` et `XDG_*` pointent vers `/tmp`. Une application PHP ou qui écrit sur disque doit déclarer ses dossiers inscriptibles dans Stockage.
- **Analyse conteneurisée** : `railpack prepare` fait confiance au `mise.toml` du dépôt (qui peut exécuter des commandes). Elle tourne donc dans un conteneur jetable : lecture seule, aucune capacité, 512 Mo, 0,5 CPU, 128 processus, 5 minutes, sans jeton GitHub ni secret, binaires vérifiés montés en lecture seule, `/tmp` en tmpfs exécutable de 256 Mio, suppression systématique. Le dossier de sortie `/out` n’est pas monté : seuls `plan.json` et `info.json`, pré-créés côté hôte, sont montés un à un, leur taille est surveillée chaque seconde (2 Mio au maximum, arrêt au-delà) et ils sont relus sans suivre de lien ni ouvrir de FIFO. Le plan est ensuite validé : caches préfixés `ph-web-<app>-`, liste des secrets imposée (toute liste de secrets d’étape est ramenée à `*`), images Railpack connues épinglées par digest (les autres images sont journalisées). L’identifiant de cache BuildKit (`cache-key`) porte en plus un jeton HMAC du secret du worker : un Dockerfile d’un autre client du builder partagé ne peut pas viser ces caches. Une erreur de génération Railpack (code 1 sans `info.json`, par exemple `railpack.json` invalide) donne « Analyse Railpack refusée : consultez le journal de construction. ». L’image produite est ré-appropriée (labels `pockethost.web*`, `User 10001:10001`).
- **Journal** : fournisseur détecté, outils résolus, commande de démarrage et images de base apparaissent dans le journal de construction ; les versions résolues sont réutilisées (`--previous`) au déploiement suivant.
- **PHP non épinglé** : le fournisseur PHP de Railpack construit sur `dunglas/frankenphp:php<version>-trixie` (qui devient aussi l’image d’exécution) et copie Composer depuis `composer:latest`, deux tags mobiles de Docker Hub qui suivent la version PHP résolue. Ils ne sont pas épinglés par la plateforme : le journal les signale (« Image imposée par le fournisseur Railpack, tag mobile non épinglé par la plateforme ») et l’aperçu du profil l’annonce. Pour les épingler, ajouter le tag et son digest à `RAILPACK['images']`.
- **Réseau requis** pendant la construction : ghcr.io, Docker Hub (images PHP ci-dessus ; `alpine:latest` n’est plus chargé par le frontend puisque les listes de secrets d’étape sont ramenées à `*`), github.com, nodejs.org et les registres de paquets. La résolution des versions par mise utilise l’API GitHub anonyme (60 requêtes par heure et par IP) : en cas de limite, l’analyse est retentée une fois puis le déploiement échoue avec un message demandant de relancer.

**Versions épinglées** (seule source : `RAILPACK` en tête de `scripts/web_app_railpack.py`) : Railpack v0.42.0, mise 2026.10.8, frontend `ghcr.io/railwayapp/railpack-frontend:v0.42.0@sha256:a42a1239…`, images builder et runtime `mise-2026.10.8` par digest. Pour monter de version, changer ensemble version et sha256 du binaire, version et sha256 de mise, digest du frontend et digests builder/runtime, conserver l’import unique de ces constantes dans l’installateur versionné, puis respecter un délai de recul d’au moins 14 jours après la publication.

**Installation** : l’installateur versionné `packages/pockethost/scripts/install-railpack.sh --railpack <binaire> --mise <binaire>` accepte les binaires Linux amd64 décompressés. Les versions et empreintes proviennent directement de `web_app_railpack.py`, sans constantes dupliquées. Les deux SHA-256 sont validés avant installation root:root 0755 ; aucun téléchargement implicite ni redémarrage. Voir [la procédure de nouveau serveur et sa réception bloquante](docs/hosting/nouveau-serveur-kolab.md#18-contrôle-automatique-bloquant-dun-nouvel-hôte). Précharger le frontend épinglé selon la procédure du builder. Le worker ne publie `railpack=true` que lorsque les outils et le frontend sont disponibles ; vérifier par une construction jetable.

Tests : `scripts/test-web-app-railpack.py` (sans réseau), ajouts dans `test-web-app-runtime.py`, `test-web-app-worker.py` et `test-web-runtime-api.py` ; `test-web-app-railpack-integration.py` (facultatif, `RAILPACK_INTEGRATION=1`, Docker requis) vérifie le format de `info.json`, le frontend épinglé par digest et l’exécution sous UID 10001 avec racine en lecture seule.

## Domaines

Les adresses générées `web-<id>.monappli.re` utilisent le wildcard existant. Le préfixe `web-` est réservé aux nouveaux sites quand le module est activé. Les domaines personnalisés exigent le TXT de propriété affiché et un pointage A/AAAA uniquement vers le serveur (DNS seul pendant la première validation).

Certbot utilise HTTP01 via un webroot dédié, un compte ACME et des fichiers distincts du certificat historique. Les challenges sont servis avant les restrictions des routes PocketBase ; les autres exceptions ne concernent que les hôtes web enregistrés et l'URL exacte du webhook signé. Les certificats SNI sont relus sans remplacer le wildcard des bases.

## Vérifications et retour arrière du module

Avant activation : sauvegarde cohérente de la mothership, copie des bundles et du dashboard, inventaire des IDs/StartedAt des conteneurs PocketBase, contrôle des ressources et des certificats existants. Tester le module isolément puis un seul site de test. Vérifier les routes PocketBase/dashboard avant et après le rechargement initial du firewall.

En cas de régression : désactiver `WEB_APPS_ENABLED`, restaurer les bundles/dashboard/firewall précédents et arrêter uniquement le worker et les conteneurs du moteur rootless web. Ne pas revenir en arrière sur la base de contrôle en écrasant des écritures intervenues depuis son snapshot. Ne pas supprimer les collections web pendant un simple retour arrière du code.

## Connexion GitHub guidée

Le parcours guidé crée une GitHub App privée avec les permissions de lecture nécessaires, puis conduit à la sélection des dépôts. GitHub demande la confirmation de création et d’installation dans son interface. La clé PEM et le secret du webhook sont récupérés et chiffrés côté serveur ; ils ne sont pas affichés dans le navigateur.

Chaque retour est lié à l’administrateur qui a commencé le parcours et à un état temporaire à usage unique. Par défaut l'assistant ajoute une nouvelle connexion sans toucher aux autres ; « Recréer la GitHub App » d'une carte la remplace, et l'App actuelle continue de servir les déploiements jusqu'à la validation complète de la nouvelle. Les identifiants de la nouvelle installation doivent être vérifiés auprès de GitHub avant activation. Les champs de configuration manuelle restent disponibles dans les réglages avancés.

Les URL de retour du dashboard utilisent une politique sans referrer et ne sont pas mises en cache. Les paramètres temporaires sont retirés de l’adresse dès l’ouverture de la page.

## Contrôle et observation des déploiements

L’annulation est prévue pour les constructions en attente ou en cours avant la publication. Elle arrête le travail du candidat et conserve le site publié. Une relance depuis l’historique reprend le commit et les variables du déploiement sélectionné ; elle ne remplace pas silencieusement son code par la tête actuelle de la branche.

Le suivi distingue les étapes réellement observées et leur durée. Les logs sont chargés par portions, avec pause du défilement, copie et téléchargement de la portion disponible. Les mesures CPU et mémoire concernent les conteneurs web propres à l’application, pas celles du serveur PocketBase entier.

Le test de V2 a aussi annulé une véritable construction BuildKit en cours : le client et le processus de construction se sont arrêtés, tandis que le conteneur publié et sa réponse HTTP200 sont restés inchangés. Le parcours GitHub a été vérifié avec des réponses GitHub simulées et un vrai contrôleur PocketBase jetable ; aucune GitHub App n’a été créée sur le compte de l’opérateur pendant les tests. La confirmation de création et le choix des dépôts se font sur GitHub depuis le bouton du dashboard.

## Variables et export local

L’onglet Variables dispose d’une vue normale et d’une vue développeur `.env`. Une prévisualisation précède chaque écriture ; le mode Fusionner conserve les variables absentes et le mode Remplacer affiche les suppressions. Les valeurs masquées restent conservées, et une valeur vide exige une action explicite. La révision empêche d’écraser une modification concurrente.

Les valeurs sont littérales côté PocketHost, y compris les dollars et les retours à la ligne. Les boutons `.env.local (Next.js)` produisent un export distinct qui échappe les dollars pour le chargeur Next.js ; ce format se réimporte sans modifier les valeurs. Les cas impossibles à représenter fidèlement en dotenv restent éditables dans la vue normale et sont signalés avant export. La révélation est explicite et inscrite dans un journal limité aux noms des variables, sans leurs valeurs.

Après un changement uniquement à l’exécution, « Appliquer sans reconstruire » réutilise l’image publiée et vérifie le nouveau conteneur avant publication. Une modification de construction ou de source exige un nouveau déploiement. Le worker transmet les variables d’exécution dans le tableau JSON `Env` du moteur Docker, ce qui conserve les valeurs multiligne sans les exposer dans ses arguments ou son environnement hôte.

## Terminal interactif

L’onglet Terminal ouvre un véritable PTY dans le conteneur publié du site. Le broker `/opt/pockethost-web/terminal-broker.cjs`, exécuté par `pockethost-web-terminal.service`, écoute seulement sur `127.0.0.1:3192` ; le firewall expose uniquement son chemin WebSocket avec TLS et Origin du dashboard. Le bundle CommonJS inclut `ws` et ne nécessite aucun module PTY natif.

Les sessions exigent le droit `terminal.open` (admin du projet ou superAdmin), un jeton à usage unique de 60 secondes et le conteneur exact publié. Les droits et la publication sont revérifiés périodiquement. Deux sessions par utilisateur, quatre par serveur (tous projets confondus), dix minutes d’inactivité et trente minutes absolues bornent leur durée. Le shell fonctionne sous UID10001, avec les quotas et les montages du site ; les données PocketBase et le socket Docker ne lui sont pas montés. L’historique conserve les métadonnées, sans commandes ni sortie.

Le superviseur dans le conteneur borne la durée même si le broker s’arrête brutalement. Le nettoyage à la déconnexion retrouve les processus par un marqueur ; il ne garantit pas de supprimer un processus délibérément détaché par un administrateur qui efface ce marqueur. Ctrl+C, `stty size`, fermeture, révocation et nettoyage ont été vérifiés sur le conteneur réel du pilote app1.

Le diagnostic GitHub affiche l’installation, les permissions, le nombre de dépôts et le dernier webhook. La recherche reste limitée aux dépôts autorisés à la GitHub App ; la liste et les branches sont paginées. Ces essais V3 utilisaient un transport simulé et des signatures RSA réelles. La connexion effective et les limites de validation actuelles figurent dans l’état V5 ci-dessous.

## Santé continue et tâches planifiées

L’onglet Santé configure une surveillance HTTP continue indépendante du contrôle précédant la publication : chemin local, intervalle de60 à3600secondes, délai de1 à10secondes et seuil de1 à10échecs. Les sondes suivent uniquement la publication courante et son port loopback contrôlé ; elles n’envoient aucun identifiant et ne suivent pas les redirections. Une réponse200–399 est acceptée. Le dashboard distingue état sain, dégradé, périmé, inconnu et surveillance désactivée. L’historique des transitions est paginé, limité à1000événements et30jours. Aucun redémarrage automatique n’est déclenché.

L’onglet Tâches planifiées accepte les expressions cron standard à cinq champs en **UTC**, affiche la prochaine occurrence et permet un lancement manuel. Le worker ne rattrape pas toutes les occurrences manquées pendant une interruption. Une seule tâche s’exécute à la fois sur le serveur et une tâche déjà active ne se superpose pas. Chaque commande s’exécute dans le conteneur publié sous UID10001, avec les quotas du site et une durée limite de1 à900secondes. Le superviseur demeure actif si le client Docker disparaît ; les variables pouvant détourner son chargement sont neutralisées pour ce superviseur et restituées à la commande du site.

L’historique paginé indique déclencheur, début, durée, état et code de sortie ; les journaux sont bornés et les valeurs d’environnement connues sont masquées. Les définitions des commandes et les snapshots sont chiffrés côté contrôle. Les sondes et les tâches tournent dans des threads distincts des constructions.

## Alertes externes (implémentées localement, non déployées)

**État : implémenté et testé sur PocketBase jetable, non déployé sur app1/app2/app3, validation opérateur à faire** ([WEB_ALERTS_RUNBOOK.md](WEB_ALERTS_RUNBOOK.md)). Il faut **toutes** les migrations en attente, au moins `1793271100_updated_web_notification_deliveries_limits.js` (champs `rateLimited`, `requestedBy` lus par les hooks), `1793271200_extended_web_alerts.js` et `1793271300_updated_web_github_connections.js` : déployer le répertoire `pb_migrations` complet, jamais une sélection, avec le bundle `pb_hooks` à jour et la release du pare-feu (`firewall serve`) et du dashboard (`serve-static-build.cjs`) qui refuse les routes `/api/web-apps/_*`. Aucun envoi externe réel n’a eu lieu pendant les tests : SMTP et webhooks en boucle locale seulement.

Les alertes sont des **réglages de l’application**, pas d’un compte. Le rôle admin du projet (`notifications.manage`) les modifie, le test exige `alerts.test` et l’historique des envois est lisible dès le rôle viewer. Le départ ou la rétrogradation de l’auteur ne les désactive pas : les envois d’un auteur legacy rétrogradé sont mis en pause sans rien perdre, et `pausedLegacy` signale les lignes coupées par l’ancien code.

- **Canaux** : email (marque « Kolab Hosting », lien profond vers l’onglet concerné, `?tab=`) et webhook HTTPS. Destinataires email : 10 au plus, chacun doit être l’adresse vérifiée d’un membre du projet ou d’un superAdmin. L’option « prévenir aussi les owners/admins du projet » passe par le même filtre.
- **Formats webhook** : `generic` (JSON v2 avec `event`, `title`, `message`, `application.dashboardUrl`, signé `X-PocketHost-Signature: sha256=HMAC(timestamp.corps)` avec `X-PocketHost-Timestamp`, `-Event`, `-Delivery`), `slack` et `discord` (texte français, sans secret). Le secret HMAC généré ou renouvelé n’est affiché qu’une fois ; ensuite seul `hasSecret` est renvoyé. Le résumé d’erreur est nettoyé des secrets ; le worker refuse les adresses privées et épingle les IP résolues.
- **Ce qui est alerté (application)** : `health_degraded`, `health_recovered` (envoyé aussi après un redéploiement ou un changement de chemin ; désactiver la surveillance l’annule), `deployment_failed`, `backup_failed` (une seule alerte par sauvegarde, échec de planification compris), `runtime_stopped`, `task_failed` (dont la suspension legacy), `certificate_expiring`, `certificate_failed`. `disk_low`/`disk_recovered` seulement pour les applications opérées par la plateforme, avec un état disque partagé par hôte et dédoublonné par destinataire.
- **Envoi** : file `web_alert_events` pour les événements poussés, scan des applications au plus toutes les 15 s, 3 essais avec délai exponentiel. Dans la fenêtre `cooldownSeconds` (300 s par défaut, 60 s minimum), un second événement du même type est regroupé (ligne `rateLimited`, résumée dans `alerts_grouped`), jamais perdu. Plafond automatique : 12 envois par canal et par heure, avec une exception pour le premier événement critique d’un type dans l’heure.
- **Test synchrone par type d’alerte** : l’email part directement ; le webhook attend au plus 12 s le compte rendu réel du worker, sinon 202. Le dashboard affiche le résultat et la classe d’erreur. Plafonds : 10 tests par canal et par heure, 20 par compte et par heure.
- **Canal plateforme** (`/api/web-platform-notifications`, superAdmins seulement, section en bas de `/applications/recovery`) : `worker_unresponsive`/`worker_recovered` (worker muet plus de 3 min), `controller_unreachable` (envoyé par le worker lui-même vers le webhook plateforme reçu au claim, quand il ne joint plus le contrôleur), `recovery_failed`, `recovery_service_offline`, `recovery_capture_stale` (plus de 36 h sans capture avec la planification active), `runtime_stopped`, disque, et copies facultatives `app_deployment_failed`, `app_backup_failed`, `app_health_degraded`. Il accepte toute adresse saisie par un superAdmin. Un watchdog tourne chaque minute (`web-alert-watchdog`), il détecte aussi les emails envoyés sans le worker et contrôle le service de reprise toutes les 15 min.
- **Routes du worker et des services locaux** (`/api/web-apps/_worker`, `_notifications`, `_platform`, `_terminal`, `_health`, `_tasks`, `_observability`) : secret `X-Web-Worker` **et** connexion directe en boucle locale. Le pare-feu et le proxy du dashboard tournent sur le même hôte (pair TCP 127.0.0.1) : ils répondent 404 à ces chemins avant tout relais (chemin décodé et normalisé), et marquent ce qu’ils relaient (`X-Forwarded-For`). Le contrôleur refuse (403) toute requête de ces routes qui porte un en-tête de relais (`X-Forwarded-*`, `X-Real-IP`, `Forwarded`, `Via`). Tant que le pare-feu n’est pas à jour, seul le secret protège ces routes depuis Internet.
- **Changement d’équipe** : rattacher une application à un projet hors de la lignée du dernier projet quitté (ou rattacher une application legacy) efface ses canaux d’alerte (URL, secret, format du webhook, destinataires), coupe les envois et annule les livraisons en file. La nouvelle équipe enregistre ses propres canaux ; l’audit du projet note `notificationsReset`.
- **Suppression de sauvegarde bloquée** (`backup_cleanup_failed`, canal plateforme) : une suppression (rétention ou manuelle) en échec signalé reste en file sans épuiser son budget ; seule une boucle de crash sans compte rendu l’abandonne, alerte la plateforme (jamais le client) et la retente le lendemain. Les canaux plateforme enregistrés avant ce correctif doivent cocher cet événement.
- **Poussée de la reprise** : `POST /api/web-apps/_platform/recovery-events` (en-tête `X-Web-Worker`, connexion directe en boucle locale, voir ci-dessus), événement `recovery_failed`, clé `platform-recovery:<runId>` partagée avec le watchdog, donc une seule alerte par échec.
- **Liens profonds** : `APP_URL`, sinon `https://$APP_HOST`, sinon `https://app.<APEX_DOMAIN>`.

## Sauvegardes web locales et S3

L’onglet Sauvegardes propose une sauvegarde manuelle, une quotidienne avec horaire UTC, une rétention de1 à14copies, le téléchargement de l’archive chiffrée et une restauration précédée d’un aperçu. Le périmètre est celui du site : nom, source, réglages et variables enregistrés, ainsi que la configuration réellement publiée et son image. Les bases PocketBase, services externes, définitions des tâches/surveillance et fichiers persistants utilisateur ne sont pas inclus. Le cache `.next` est recréé lors d’une restauration. Les modifications enregistrées mais non publiées sont conservées distinctement de la version effectivement redémarrée.

Les petites archives `.phwb` sont stockées dans `WEB_APPS_ROOT/backups/<appId>/`. Les images locales sont épinglées avant nettoyage, y compris pendant la préparation de la sauvegarde ; un inventaire indisponible suspend leur suppression. Le budget partagé des images épinglées est6Gio, celui des archives64Mio, avec une réserve libre de15Gio. La rétention supprime seulement les objets et images propres aux sauvegardes concernées, sans toucher au moteur Docker PocketBase.

Le worker reçoit les paramètres `WEB_BACKUPS_S3_*` dans son fichier privé, jamais dans les variables du site ou celles du dashboard. La copie externe comprend l’archive de configuration chiffrée et les images Docker immuables gzip/AES-GCM du site, y compris les services auxiliaires Compose. Chaque image est envoyée en multipart sans fichier temporaire, avec association cryptographique à l’application et à son identifiant SHA. Une image identique est réutilisée entre sauvegardes. L’interface indique séparément le résultat local, l’état S3, la progression et les erreurs ; un échec S3 conserve la copie locale.

Les sauvegardes web du pilote app1 utilisent le bucket OVH déjà autorisé, exclusivement sous `app1/web-app-backups/`. Leurs opérations ne listent ni ne nettoient les autres préfixes. La reprise globale utilise son propre préfixe, configuré par `WEB_PLATFORM_RECOVERY_PREFIX`. Une archive locale absente ou corrompue peut être rapatriée par « Récupérer depuis S3 ». Une image absente est récupérée automatiquement lors de la restauration : une première lecture authentifie intégralement le contenu avant tout import Docker ; la seconde est liée au même ETag. L’import exige au préalable15Gio libres plus deux fois la taille du tar décompressé, puis maintient la réserve15Gio pendant son exécution.

La clé maître du contrôle et la clé AES des images doivent être sauvegardées **hors du serveur et du bucket**. Une copie de récupération privée du pilote est conservée sur le poste opérateur dans `.secret/web-backups-app1-recovery.json`. Une perte totale du serveur exige de reprovisionner le moteur/contrôle et de rétablir ces clés et les métadonnées ; le rapatriement du dashboard suppose que la base de contrôle existe encore. Ce module complète les sauvegardes complètes de la plateforme et les sauvegardes PocketBase.


## État V5 du pilote — 29 octobre 2026

La V5 est déployée uniquement sur app1. Le démarrage avec les192routes complètes a été testé dans un vrai contrôleur PocketBase jetable : les routes globales des projets utilisent `/api/web-projects`, et les opérations de stockage passent par une route dont le paramètre `operation` est réellement transmis au gestionnaire. Les conteneurs PocketBase existants ont été préservés lors du déploiement.

Les images immuables Alpine, les workers sans domaine public, les constructions Dockerfile et Compose à deux images ont été vérifiés sur des conteneurs réels. Le PTY et les tâches cron ont également passé leurs essais réels. La sauvegarde S3 de Compose et la restauration de ses deux images sont passées ; les fichiers persistants ont fait l’objet d’un essai distinct. Les nouveaux moteurs conservent UID10001, les quotas et la racine en lecture seule. Les volumes déclarés implicitement par une image sont refusés s’ils ne correspondent pas exactement à un montage géré ; ils ne doivent pas créer de stockage anonyme hors suivi.

Le collecteur de logs tourne séparément des builds. Les essais réels ont confirmé le masquage d’une valeur d’environnement factice, la recherche, trois pages anciennes puis deux pages récentes sans doublon et les filtres stdout/stderr. Le curseur Docker est exprimé en secondes Unix avec chevauchement et déduplication ; réutiliser directement le timestamp RFC3339Nano des lignes empêchait les collectes suivant la première sortie. Les canaux d’alerte restent désactivés tant qu’un administrateur ne les configure pas explicitement. Aucun message n’a été envoyé à une destination externe pendant ces essais.

La connexion GitHub et les déploiements automatiques après push sont validés en réel après correction du budget mémoire. Les droits de lecture des pull requests ont été acceptés dans GitHub. Le commit `1200d39370d34d74c6daa6abbabc1e9ce26d6e46` a été synchronisé automatiquement sur la source et sa preview, toutes deux en `ready` et HTTP200 ; le secret de production était absent de la preview. La PR a ensuite été fermée sans fusion : les métadonnées ont été fermées, l’application temporaire et ses conteneurs supprimés, tandis que la source restait HTTP200. La canary temporaire de production a été retirée. Une promotion d’image source→staging a également répondu HTTP200 avec des variables isolées ; staging a ensuite été arrêté. Le workflow CI externe n’est pas validé de bout en bout, GitHub Actions étant bloqué par sa facturation ; les jetons limités au dépôt d’images, l’idempotence et le nettoyage des identifiants éphémères ont été testés sur le contrôleur natif.

Le HTTPS et le certificat du domaine pilote `testgg` ont été vérifiés en réel, ainsi que le renouvellement Certbot à blanc. Ce dernier vérifie le parcours de renouvellement sans attendre l’échéance du certificat de production. Pour un alias sous le suffixe de la plateforme, le contrôleur, le worker et le firewall doivent recevoir la même liste `WEB_APPS_PLATFORM_ALIASES`. Un alias doit être explicitement listé, limité à un seul label et hors des noms réservés/protégés ; le préfixe `web-` conserve son rôle d’adresse générée. Le challenge HTTP01 est disponible avant l’activation du site, puis le proxy suit sa publication sans invalider les autres routes web.

Les snapshots/restaurations de volumes sont validés en réel : sauvegarde S3, reprise du site sur le même port avec HTTPS200, modification de la donnée, puis restauration dans un nouveau volume et publication retrouvant la bonne donnée avec HTTPS200. La capture et la restauration complètes de la plateforme restent en attente ; elles ne sont pas déclarées validées par ces seuls essais de sites. Le service de reprise utilise un plan et une clé privés hors du processus HTTP ; le dashboard expose uniquement la planification, la couverture et l’historique filtré. Le test isolé ne démarre aucun service et ne remplace aucune donnée de production. Les previews temporaires sont exclues de la capture des ressources persistantes et leur nombre est affiché.

Les copies purgées par la rétention, ou dont la purge S3 est restée partielle, ne peuvent plus servir de source de test de reprise. Leur historique reste visible. Les clés de reprise doivent être conservées séparément du serveur et du bucket.

## Volumes : pause et reprise du site

Un snapshot arrête temporairement les services du site pour obtenir une copie cohérente, puis les redémarre avec une vérification de santé sur le port publié. Les nouveaux conteneurs conservent un port loopback explicite à travers un arrêt/redémarrage. Lors d’une restauration, les données sont reconstruites dans un nouveau volume ; un nouveau déploiement publie sa route après contrôle de santé, sans écraser l’ancien volume.

Les conteneurs hérités dont la liaison Docker était dynamique sont refusés avant toute pause : le préflight valide tous les services avant d’en arrêter un. Pour utiliser les snapshots sur ces anciens sites, l’opérateur doit d’abord effectuer un arrêt explicite, puis un nouveau déploiement avec les liaisons fixes. Ce refus préventif conserve le site courant ; il ne réalise pas une migration automatique de ses ports ou données.

Les snapshots des volumes constituent un parcours séparé des archives de configuration et d’images du site. La restauration d’une ancienne image ne rembobine pas automatiquement les données d’un volume ou celles d’une base externe.

## Reprise globale : procédures opérateur V5

**État actuel : implémenté — à tester par l’opérateur.** La capture et la restauration complètes des sept bases réelles ne sont pas validées. Le plan couvre le contrôleur et ses six bases PocketBase, leurs fichiers locaux et les fichiers du S3 natif. Les sept clés MFA `.pockethost-totp.key` sont prises en charge automatiquement et restaurées dans le `pb_data` correspondant, en mode0600, même sans `--with-host-files`. Ce correctif est installé et les tests sur bases jetables sont passés ; cela ne constitue pas une preuve de reprise complète du serveur.

### Prise en main pour ce soir

1. Ouvrir `/applications/settings`, puis **Reprise du serveur** (`/applications/recovery`).
2. Cliquer sur **Capturer maintenant**. Attendre la fin de l’opération en `ready`, puis vérifier la couverture affichée et l’absence de dépendances manquantes. Une capture en échec ne peut pas servir de preuve de sauvegarde complète.
3. Choisir cette capture et lancer **Vérifier cette capture**. Le test du dashboard reconstruit seulement **deux bases : le contrôleur et une petite base de test prédéfinie**. Les copies restent en pause, sans démarrer les services ni remplacer la production.
4. Consulter le résultat et l’historique. Pour vérifier les sept bases, utiliser la procédure complète en ligne de commande ci-dessous ; le test partiel du dashboard ne la remplace pas.

**La planification automatique reste désactivée jusqu’à la validation par l’opérateur.** Commencer par une capture manuelle et son test, puis activer l’horaire souhaité après contrôle des résultats.

Le plan privé décrit chaque base (`database`, `storage`, identifiant d’instance), les fichiers de configuration et binaires nécessaires via `hostPaths`, et les dépendances S3 à conserver. Les sept bases doivent apparaître dans le résultat d’inspection attendu, avec leur date de snapshot. Les lectures SQLite sont cohérentes par base ; les fichiers sont vérifiés séparément et ne forment pas une transaction atomique commune aux sept bases. Une capture partielle ou une dépendance absente ne constitue pas une sauvegarde complète.

Le service prépare les sauvegardes des images web et des volumes, puis construit le plan couvert avant de lancer la capture. Le fichier de configuration du service, le plan, le mot de passe technique et la clé sont privés, sans lien symbolique, en mode0600 ; le répertoire d’état partagé avec le contrôleur est en0700. La clé de reprise est une valeur aléatoire de64caractères hexadécimaux, distincte des clés du contrôleur et des images. Elle ne doit pas entrer dans son propre plan de capture. Conserver une copie de récupération **hors du serveur et hors du bucket**, avec les informations permettant de retrouver les objets S3 ; un bucket chiffré sans sa clé ne suffit pas à reprendre le service.

Le pilote dispose de l’unité `pockethost-platform-recovery.service`, indépendante du worker Docker rootless. Son exécution correspond à :

```sh
python3 /opt/pockethost-web/web_platform_recovery_service.py serve \
  --config '<CHEMIN_PRIVE/config.json>'
```

La configuration privée fixe notamment `stateDir`, `planFile`, `keyFile`, `drillRoot`, le contrôleur loopback et les limites du test. Le verrou privé `executionEnabled` permet de suspendre les exécutions pendant les validations. Le processus HTTP n’ouvre pas ces fichiers secrets : il ne lit et n’enfile que les métadonnées filtrées du répertoire d’état.

Dans `/applications/recovery`, un administrateur configure l’horaire quotidien **UTC** et une rétention de1 à30captures, déclenche une capture manuelle et consulte service, couverture, progression et historique paginé. Le daemon traite cette planification ; le dashboard ne réalise pas la capture dans une requête HTTP. Une seule opération peut être active. Les chemins, clés, commandes et destinations de restauration ne sont pas saisis depuis le navigateur.

L’horaire prévu pour app1 est21h30UTC, avec une rétention de3captures ; la planification est actuellement désactivée. La capture utilisée par le dernier test de reprise réussi reste protégée, même si elle dépasse cette rétention. Les exécutions manuelles et leur couverture se suivent depuis le dashboard.

Le bouton de test du pilote récupère uniquement le contrôleur et une petite base de test prédéfinie. Sa sélection est fixée par `drillSelect`, avec un budget matérialisé de2Gio et une réserve de15Gio. Il conserve les bases en pause et ne démarre aucun service. Ce résultat ne démontre ni la restauration des sept bases, ni la remise en route des images, volumes et applications de toute la plateforme.

Pour une récupération manuelle complète, charger la configuration S3 privée dans une session d’administration dédiée, puis inspecter l’objet choisi. Les exemples suivants utilisent des espaces réservés, jamais une clé inscrite dans la ligne de commande : seul son chemin privé est transmis.

```sh
python3 /opt/pockethost-web/web_platform_recovery.py inspect \
  --object '<OBJET_S3_DE_CAPTURE>' \
  --key-file '<CHEMIN_PRIVE/cle-reprise.hex>'

python3 /opt/pockethost-web/web_platform_recovery.py restore \
  --object '<OBJET_S3_DE_CAPTURE>' \
  --key-file '<CHEMIN_PRIVE/cle-reprise.hex>' \
  --target '<NOUVEAU_DOSSIER_VIDE>' \
  --with-host-files --with-native-archives
```

L’absence de `--select` demande toutes les bases du manifeste. La destination doit être nouvelle ou vide, canonique, distincte des répertoires de production. Le moteur authentifie intégralement l’archive avant reconstruction et garde par défaut15Gio libres en plus du volume à matérialiser. Une réserve inférieure exige explicitement `--offline` et ne doit pas être utilisée pour les tests du serveur actif.

La reconstruction produit `RECOVERY_PAUSED.json`, le manifeste et les éventuelles substitutions de préfixes S3 dans `recovery-env-overrides.json`. Vérifier `paused`, `noServicesStarted` et la liste réelle `databasesRestored` avant toute étape ultérieure. Les tâches, notifications, SMTP, S3 natif et sauvegardes automatiques de la copie sont neutralisés. Les hooks et migrations capturés ne sont pas exécutés automatiquement. Aucune donnée de production n’est écrasée et aucun service n’est publié par cette commande.

Un premier contrôle des fichiers et de SQLite peut être effectué sans démarrer PocketBase :

```sh
python3 /opt/pockethost-web/web_platform_recovery_probe.py \
  --root '<DOSSIER_RESTAURE>' --expected-databases 7 \
  --report '<CHEMIN_PRIVE/rapport-reprise.json>' --verify-only
```

Ce contrôle ne prouve pas les sept démarrages natifs. Leur épreuve distincte exige une unité Linux isolée avec `PrivateNetwork=true`, des hooks/migrations vides et les binaires correspondant aux versions capturées, dont les empreintes sont vérifiées. Les processus de test sont arrêtés après leur contrôle de santé. La réactivation des dépendances, secrets, domaines et services après une perte réelle du serveur reste une décision explicite de l’opérateur, séparée du test isolé.


### Outils de reprise et planification (implémentés localement)

**État : implémenté localement, non déployé ; validation opérateur à faire.** Le détail des commandes est dans [WEB_RECOVERY_RUNBOOK.md](WEB_RECOVERY_RUNBOOK.md).

- **Plan** : `web_platform_recovery_plan.py check` signale une dérive entre le plan et les instances réelles (code 3) et `generate` écrit un nouveau plan en gardant `hostPaths`, `nativeBackups` et les `s3Refs` statiques. Le dashboard affiche la dérive (`planCheck`) : la capture suivante échouerait.
- **Preuve de démarrage natif** : `web_platform_recovery_probe.py native-proof` (root, unité `PrivateNetwork=yes`) démarre chaque base restaurée sous l’utilisateur propriétaire de la copie, sur `127.0.0.1`, sans hooks ni réseau, puis l’arrête. Essai à blanc sans `--execute`.
- **Levée de pause** : `web_platform_recovery_unpause.py` agit sur la copie restaurée, catégorie par catégorie, à blanc sans `--apply`. Durcissement : `--revoke-ci-tokens`, `--rotate-webhooks` (secrets GitHub, alertes et `gitWebhookSecrets` dans `--secrets-out`), `--disable-git-sources`. Les clés de déploiement Git ne sont jamais renouvelées hors ligne. La pause liste les identifiants conservés (`credentialsKept`, hôtes seulement).
- **Balayage** : `web_platform_recovery_service.py sweep` liste puis supprime les objets S3 partiels d’une capture tuée ; le balayage automatique dépend de `sweepEnabled`. `captures`, `hold`, `release` et `record-drill` réservent une capture et enregistrent un exercice pour que la rétention la protège.
- **Archives des sites** : avec `protectCustomerBackups` (vrai par défaut), le service ne crée une archive de site que si elle n’évince que ses propres archives ; sinon il réutilise la plus récente (`webBackups: {created, reused, staleReused}`). Côté contrôleur, le déclencheur `platform` est désormais hors rétention client, réservé aux superAdmins, et seule la plus récente est gardée par site ; l’historique du site l’affiche « Plateforme (reprise) ».
- **Planification** : l’heure est en UTC (21:30 UTC recommandé). Armer la planification après l’heure du jour attend le lendemain, sauf l’option « Lancer aussi aujourd’hui » (`runToday`). Un jour où l’exécution était verrouillée à l’heure prévue est consommé. « Prochaine capture » n’est affichée qu’avec `status: active` ; sinon le dashboard montre la raison (`executionDisabled`, `serviceOffline`, `notConfigured`) et l’heure prévue.
- **Alertes** : `failureEvents.enabled=true` (avec un fichier secret 0600) n’est à activer qu’après le déploiement de la route `recovery-events`, puis redémarrage du service.

### Interface unifiée — 29 octobre 2026

Le nouveau style inspiré de PgBase est publié sur les trois installations : navigation latérale commune, panneaux compacts, accents bleus et thèmes sombre/clair. Les pages d’hébergement utilisent les mêmes composants visuels ; les onglets reviennent à la ligne sur ordinateur et défilent sur mobile. Le menu Applications web est activé sur les trois builds, y compris app3. Cette publication concerne uniquement le dashboard ; les moteurs web et les bases n’ont pas été redémarrés. Les anciens fichiers compilés et les sources modifiées sont conservés dans les releases `pgbase-ui-app1-20261029T131641Z`, `pgbase-ui-app2-20261029T131641Z` et `pgbase-ui-app3-20261029T131641Z` sous `/home/ubuntu/pockethost-releases`.


2026-10-29 — Nom officiel du projet confirmé par l’utilisateur : **Kolab Hosting**. Nom appliqué au logo textuel, à la navigation, aux titres de pages et métadonnées, au manifeste installable et aux textes de marque. Les mentions techniques de PocketBase restent inchangées. Publication UI uniquement sur app/app2/app3, builds dédiés depuis les sources déployées.

### Navigation et suppression

La fiche d’une application utilise un menu à gauche (Publication, Exploitation, Configuration). Sur mobile, le bouton « Section » ouvre ce menu vertical.

Un propriétaire de projet ou un administrateur de la plateforme peut supprimer une application depuis « Supprimer l’application », ou un projet et tous ses sites depuis « Toutes les applications » → « Supprimer le projet ». Le nom exact et une confirmation des pertes de données sont demandés. Le dépôt Git et les bases de données externes sont conservés.

La suppression est asynchrone : les nouvelles opérations sont bloquées, les tâches et déploiements automatiques désactivés, puis les sauvegardes d’application et snapshots de volumes sont supprimés via leurs workers habituels (copies locales et S3). Le worker web retire ensuite les routes et les ressources Docker identifiées par application, ainsi que les fichiers locaux. Les métadonnées ne sont retirées qu’après confirmation du nettoyage ; un reçu privé conserve l’auteur et le périmètre de l’action. Les projets incluent tous leurs environnements et previews. Un déploiement ou une opération de stockage en cours doit d’abord se terminer ; un journal de restauration en attente bloque l’admission. Un échec reste visible dans la liste et peut être réessayé avec le même périmètre. Une suppression déjà confirmée ne peut pas être annulée.


### Cloudflare — DNS avec aperçu et confirmation (31 octobre 2026)

Dans Applications web → application → Domaines, ajouter le nom souhaité, puis choisir **Préparer les DNS dans Cloudflare**. Le serveur recherche la zone active exacte ou parente accessible au jeton, affiche les enregistrements existants des deux noms concernés et les créations/modifications/suppressions proposées. Le nom doit être saisi pour confirmer. L’aperçu expire après dix minutes, appartient à son auteur et ne peut être consommé qu’une fois. Le serveur relit Cloudflare avant d’écrire et refuse un aperçu devenu obsolète.

Le lot Cloudflare configure A vers l’IPv4 du serveur, proxy désactivé, TTL automatique, et TXT de propriété. Les AAAA/CNAME incompatibles ainsi que les doublons A et anciens TXT Kolab sont montrés comme suppressions avant confirmation. Les TXT sans rapport, les autres types compatibles et les autres noms sont conservés. Une délégation NS bloque le parcours. L’API [DNS batch Cloudflare](https://developers.cloudflare.com/api/resources/dns/) applique le lot ; la propagation DNS publique reste asynchrone. Après application, lancer **Vérifier DNS et HTTPS**. Aucun DNS n’est modifié en préparant un aperçu.

Cette connexion globale est réservée aux administrateurs de plateforme, y compris si un membre possède le droit de gérer les domaines de son projet. Le jeton existant du serveur est utilisé à défaut ; **Connexion Cloudflare** permet un jeton dédié, enregistré chiffré, jamais retourné au navigateur. Permissions nécessaires : Zone:Read et DNS:Edit sur les zones à gérer. Le jeton hérité testé sur app3 accède à monappli.re mais ne donne pas accès à lapub.re ; autoriser cette dernière zone via un jeton dédié pour utiliser l’exemple 1.lapub.re. Ne pas partager de jeton dans une conversation.

La vérification HTTPS contrôle désormais la disponibilité de la configuration du worker avant de créer un job de certificat. Le worker annonce si le contact ACME et Certbot sont prêts. Les contacts manquants sont configurés à partir du support existant sur chaque serveur ; une absence ultérieure produit un message explicite. Publication sur les trois serveurs avec sauvegarde sous `/home/ubuntu/cloudflare-dns-20261031/rollback`, aucun conteneur client redémarré.


## Allocation adaptative CPU / RAM

Le [mode adaptatif](WEB_RESOURCE_ALLOCATION.md) distingue mémoire habituelle et plafond de pointe, partage le CPU par priorité et diffère les constructions sous pression. Les plafonds globaux et les quotas optionnels des projets restent des protections. Les conteneurs existants adoptent leurs nouveaux poids et réservations lors de leur prochain déploiement.
