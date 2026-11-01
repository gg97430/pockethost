# Validation des alertes web : procédure opérateur

Ce document décrit la validation, sur un vrai serveur, des alertes des applications web (onglet **Alertes**) : envoi par email et par webhook sur app1, app2 et app3.

Chaque serveur est validé séparément. SMTP, GitHub, S3, la clé maître et le secret worker sont propres à chaque hôte.

Aucune étape ne touche une application cliente. Tous les essais se font sur une application de test jetable, supprimée à la fin.

| Serveur | Dashboard | IP | Particularités connues (29/10/2026) |
| --- | --- | --- | --- |
| app1 | `https://app.monappli.re` | 141.94.92.92 | V5 complète : GitHub connecté, S3 des sauvegardes configuré |
| app2 | `https://app2.monappli.re` | 91.134.89.55 | Hébergement web actif ; **GitHub et S3 non configurés** |
| app3 | `https://app3.monappli.re` | 135.125.67.39 | Hébergement web actif ; **GitHub et S3 non configurés** |

Conventions :
- `<APP_ID>` : identifiant de 15 caractères de l’application de test, visible dans l’URL `/applications/<APP_ID>`.
- `<DEP_ID>` : identifiant du déploiement publié, visible dans l’onglet Déploiements.
- Commandes serveur : en `ubuntu`, avec `sudo` uniquement quand il est indiqué.
- Ne jamais mettre un mot de passe, un secret HMAC ou une clé S3 sur une ligne de commande, dans un ticket ou dans `.secret/` en clair. N’y noter que les noms et les résultats.

Raccourcis à définir dans la session SSH :

```bash
DB=/home/ubuntu/.local/share/pockethost/data/mothership/pb_data/data.db
WEB_UID=$(id -u pockethost-web)
wdocker() { sudo -u pockethost-web XDG_RUNTIME_DIR=/run/user/$WEB_UID DOCKER_HOST=unix:///run/user/$WEB_UID/docker.sock docker "$@"; }
deliveries() { sqlite3 -readonly -header -column "$DB" "select created,event,channel,status,attempts,httpStatus,trigger,substr(lastError,1,60) lastError from web_notification_deliveries where appId='$1' order by created desc limit 20"; }
```

`deliveries` ne lit ni le `payload`, ni le `leaseHash`, ni la configuration chiffrée.

---

## 0. Avant de commencer

1. **Version du code.** Relever la release active de l’hôte (`/home/ubuntu/pockethost-releases/…`). Certaines vérifications dépendent des correctifs d’alertes décrits dans le plan de câblage. Elles sont marquées **[après correctifs]** :
   - format Slack/Discord ;
   - secret affiché une seule fois ;
   - test synchrone par type d’événement ;
   - `health_recovered` après redéploiement ;
   - canal plateforme.

   Sur une release antérieure, les ignorer et noter « non applicable ».
2. **Fenêtre.** Prévoir une fenêtre de 1 h 30 par serveur, hors des heures de sauvegarde planifiée (§4.3).
   - Ne redémarrer aucun service PM2.
   - Ne jamais redémarrer le worker web pendant un déploiement en cours :
     ```bash
     sqlite3 -readonly "$DB" "select id,appId,action,status from web_deployments where status in ('queued','running')"
     ```
     Le résultat doit être vide avant chaque redémarrage du worker.
3. **Worker actif.**
   ```bash
   systemctl is-active pockethost-web-worker.service
   ```
   Attendu : `active`. Le worker porte les sondes de santé, les sauvegardes et l’envoi des alertes. S’il est arrêté, aucune alerte ne part.
4. **Ordre impératif.** Enregistrer les canaux d’alerte **avant** de provoquer un incident. Un événement antérieur à l’activation des canaux n’est jamais envoyé.

---

## 1. SMTP et authentification de l’expéditeur (sur chaque serveur)

Les emails d’alerte utilisent le SMTP de la plateforme de l’hôte. Il n’existe pas de SMTP propre aux alertes.

1. Dashboard du serveur → **Administration** → **SMTP des emails**.
   - Renseigner l’hôte, le port, l’utilisateur et la méthode d’authentification.
   - Expéditeur : adresse sur un domaine que vous contrôlez, par exemple `alertes@monappli.re`.
   - Nom d’expéditeur : `Kolab Hosting`. Les correctifs l’utilisent par défaut si le champ est vide.
2. **Tester SMTP** vers une boîte que vous lisez, puis **Enregistrer**. Le mot de passe n’est jamais renvoyé au navigateur ; un champ vide conserve le mot de passe enregistré.
3. Vérifier le DNS du domaine expéditeur (ici `monappli.re`) depuis le poste opérateur :
   ```bash
   dig +short TXT monappli.re | grep -i spf
   dig +short TXT _dmarc.monappli.re
   dig +short TXT <sélecteur>._domainkey.monappli.re   # sélecteur fourni par le relais SMTP
   ```
   - **SPF.** Il doit autoriser le relais réellement utilisé :
     - relais externe : son `include:` ;
     - envoi direct depuis le serveur : `ip4:` de **chaque** serveur qui envoie (141.94.92.92, 91.134.89.55, 135.125.67.39).
   - **DKIM.** La clé publique doit exister pour le sélecteur du relais.
   - **DMARC.** Une politique `p=none` au minimum pendant la validation ; noter la politique en place.
4. Dans l’email de test reçu, afficher les en-têtes et relever `Authentication-Results`. Attendu : `spf=pass`, `dkim=pass`, `dmarc=pass`. Vérifier aussi que le message n’est pas classé en indésirables (Gmail et Outlook si possible).
5. Noter le résultat dans le tableau du §7. Si l’un des trois contrôles échoue, corriger le DNS avant de continuer : une alerte classée en spam équivaut à une alerte perdue.

---

## 2. Récepteur webhook contrôlé par l’opérateur

Choisir **un** récepteur privé dont vous êtes seul destinataire :

- **Discord** : salon privé → Paramètres → Intégrations → Webhooks → Nouveau webhook → copier l’URL `https://discord.com/api/webhooks/…`. Format **Discord** **[après correctifs]**.
- **Slack** : application « Incoming Webhooks » sur un canal privé → URL `https://hooks.slack.com/services/…`. Format **Slack** **[après correctifs]**.
- **Générique** : point HTTPS public sur le port 443 qui journalise les requêtes, par exemple un n8n auto-hébergé ou un petit service. C’est le seul format accepté par une release sans correctifs : Discord et Slack refusent le JSON générique (échec HTTP 400 dans l’historique).

Pour le format générique, vérifier la signature. Enregistrer le corps brut reçu dans `body.json` et le secret dans `secret.txt`, en mode 0600 et hors du dépôt, puis lancer :

```bash
python3 - X-PocketHost-Timestamp-reçu X-PocketHost-Signature-reçue body.json secret.txt <<'PY'
import hmac,hashlib,sys,time,pathlib
ts,sig,body,secret=sys.argv[1],sys.argv[2],pathlib.Path(sys.argv[3]).read_bytes(),pathlib.Path(sys.argv[4]).read_text().strip().encode()
ok=hmac.compare_digest('sha256='+hmac.new(secret,ts.encode()+b'.'+body,hashlib.sha256).hexdigest(),sig)
print('signature', 'OK' if ok else 'INVALIDE', '| âge', int(time.time())-int(ts), 's')
PY
```

Attendu : `signature OK` et un âge inférieur à 300 s. Supprimer `secret.txt` après l’essai.

---

## 3. Application de test jetable et configuration des canaux

### 3.1 Créer l’application

- **app1** (GitHub connecté) :
  - Créer un dépôt privé de test avec un site statique minimal qui répond 200 sur `/`, par exemple un Dockerfile `nginx:alpine` ou une page Next.js.
  - Applications web → **Nouvelle application** → ce dépôt, branche `main` → déployer.
  - Attendre le statut `ready` et une réponse HTTP 200 sur l’adresse générée `web-….app.monappli.re`.
- **app2 / app3** (sans GitHub) :
  - Créer une application de moteur **Image immuable**, avec une image publique **épinglée par digest** : `docker.io/library/nginx@sha256:<digest relevé sur Docker Hub>`, port 80.
  - Déployer, puis attendre `ready` et HTTP 200.

Relever `<APP_ID>` et `<DEP_ID>` dans le dashboard.

Vérifier le conteneur :

```bash
wdocker ps --filter name=ph-web-<APP_ID> --format '{{.Names}} {{.Status}}'
```

### 3.2 Activer la surveillance de santé (obligatoire pour l’alerte « site indisponible »)

Onglet **Santé** :
- activer la surveillance ;
- chemin `/` (ou `/api/health` si l’application l’expose) ;
- intervalle 60 s, délai 5 s, seuil 2 ;
- enregistrer.

Attendre l’état **Sain** (1 à 2 minutes). Sans cette étape, aucune alerte d’indisponibilité ne peut partir. Après correctifs, l’onglet Alertes affiche un avertissement dans ce cas.

### 3.3 Configurer les alertes

Onglet **Alertes** :
- **Webhook**. Coller l’URL du récepteur et choisir le format **[après correctifs]**.
  - Format générique : saisir **votre** secret de 32 caractères ou plus, conservé dans le gestionnaire de mots de passe. Ou bien laisser le champ vide et, **[après correctifs]**, copier le secret affiché une seule fois après l’enregistrement.
  - Cocher « Activer ce webhook ».
- **Email**. Saisir une boîte opérateur, cocher « Activer les emails ».
  - **Ne pas cocher** « propriétaires et administrateurs du projet » **[après correctifs]** pendant ce protocole : ces destinataires sont résolus à l’envoi, et chaque test et chaque incident provoqué du §4 partirait chez tous les membres du projet (clients ou collègues). L’application jetable doit être hors projet, ou dans un projet dont l’opérateur est le **seul** membre (vérifier l’onglet Membres).
  - Pour valider cette option, la cocher seulement dans un projet dont l’opérateur est le seul membre, envoyer **un** test (§3.4), vérifier la réception, puis la **décocher et enregistrer avant le §4**.
- **Événements**. Tous cochés.
- **Regroupement**. 60 secondes.
- **Enregistrer les canaux et événements**. Noter l’heure : c’est l’heure d’activation.

### 3.4 Envoyer les tests

1. Cliquer **Envoyer le test** pour le webhook, puis pour l’email.
   - Attendu **[après correctifs]** : un résultat affiché à côté du bouton, « Envoyé — HTTP 2xx » ou « Envoyé au serveur SMTP ».
   - Sur une release antérieure : le message « ajoutée à la file », puis l’état « Envoyée » dans l’historique sous 10 secondes environ.
2. **[après correctifs]** Choisir le type « Échec de déploiement » et renvoyer un test. Le message reçu porte la mention TEST et des valeurs fictives.
3. Vérifier la réception :
   - Discord ou Slack : un seul message.
   - Générique : signature OK (§2).
   - Email : expéditeur « Kolab Hosting » **[après correctifs]** ; objet `[Kolab Hosting] TEST · Test d’alerte · <nom>` ; lien « Ouvrir dans le dashboard » qui mène à l’onglet Alertes de l’application.
4. Contrôle serveur : `deliveries <APP_ID>`. Attendu : deux lignes `test`, `sent`, `attempts=1`.

---

## 4. Provoquer les trois événements cibles (après l’enregistrement du §3.3)

Pour chaque événement, vérifier :
- **livraison** : un message sur chaque canal ;
- **contenu** : libellé français, nom de l’application, lien vers le dashboard, aucun secret ni valeur privée d’environnement ; les emails d’échec de déploiement joignent le journal masqué décrit au §4.2.1 ; les autres alertes et les webhooks ne joignent pas de journal ;
- **déduplication** : attendre 2 minutes de plus et contrôler qu’aucun second message ne part ;
- **historique** : la ligne de l’onglet Alertes et `deliveries <APP_ID>` concordent.

### 4.1 Site indisponible (`health_degraded`), puis rétabli (`health_recovered`)

1. Vérifier que l’état Santé est **Sain**.
2. Arrêter le conteneur publié de l’application de test, et seulement lui :
   ```bash
   wdocker stop ph-web-<APP_ID>-<DEP_ID>
   ```
3. Sous 2 à 3 minutes (2 échecs à 60 s d’intervalle), l’onglet Santé passe à **Dégradé** et l’alerte « Site indisponible » arrive sur les deux canaux.
4. Redémarrer le conteneur :
   ```bash
   wdocker start ph-web-<APP_ID>-<DEP_ID>
   ```
   Au contrôle suivant, l’état revient à **Sain** et l’alerte « Site rétabli » arrive.
5. Ne pas redémarrer le worker pendant cet essai.
6. **[après correctifs] Variante « chemin de santé cassé ».**
   - Dans Santé, remplacer le chemin par `/chemin-inexistant-test`, à condition que l’application réponde 404 sur ce chemin, puis enregistrer. Attendre l’alerte « Site indisponible ».
   - Remettre le chemin `/`, enregistrer, attendre l’alerte « Site rétabli ».
   - Sur une release antérieure, ne pas utiliser cette variante : la modification de configuration réinitialise l’état et l’alerte de rétablissement ne part jamais.
7. Limite connue : la sonde interroge le conteneur en local (`127.0.0.1`). Une panne de DNS, de TLS ou du proxy public n’est pas détectée par ce mécanisme. **[après correctifs]** Le worker sait aussi sonder le domaine public en HTTPS quand le contrôleur lui transmet `publicHost` ; toute redirection 3xx y compte comme joignable.

### 4.2 Échec de déploiement (`deployment_failed`)

#### 4.2.1 Email et pièce jointe (correctif du 31/10/2026)

Un échec de déploiement déclenché par un push Git, le dashboard ou la CI produit un email avec un fichier `deployment-<APP_ID>-<JOB_ID>.log`, si le canal concerné est actif. La lecture du fichier est réservée à un job `failed`, d’action `deploy` ou `ci-deploy`, appartenant à cette application. Un test manuel d’alerte joint un journal fictif ; il ne lit jamais le journal d’un vrai job.

| Canal | Événement | Destinataires et portée |
| --- | --- | --- |
| Application | `deployment_failed` | Réglages de l’onglet Alertes de cette application ; adresses vérifiées encore autorisées dans son projet, et propriétaires/administrateurs si cette option est cochée. Les droits sont revérifiés à l’envoi. |
| Plateforme | `app_deployment_failed` | Canal réservé aux opérateurs, avec `copyAppEvents: true` et cet événement sélectionné. Il détecte aussi les applications sans réglage d’alerte propre. L’auteur du canal doit rester superAdmin non suspendu. |

Les deux destinataires approuvés pour le canal plateforme sur **app1, app2 et app3** sont `hello@kolab.tech` et `guillaume@lapub.re`. Cette configuration est distincte de celle du monitoring général des bases et des serveurs, même lorsque les destinataires sont les mêmes. Enregistrer uniquement l’événement `app_deployment_failed` lors d’une première activation ; préserver les autres canaux et événements si un opérateur les a configurés depuis.

La pièce jointe contient au maximum **1 Mio (1 048 576 octets après décodage MIME)** de texte UTF-8, pris dans les dernières lignes complètes. Un marqueur signale une troncature. Le lecteur refuse les liens symboliques et fichiers spéciaux, utilise un chemin construit à partir de l’identifiant validé du job et expire après cinq secondes. Il ne reçoit aucun chemin ni aucune URL du client.

Une seconde passe masque les valeurs privées de l’environnement **figé dans le snapshot du build** et de l’environnement actuel de l’application, y compris les lignes de secrets multi-lignes d’au moins 12 caractères. Les mots de passe courts restent masqués ; une valeur également utilisée comme configuration publique reste privée si elle apparaît sous un nom de secret. Les réglages publics connus (`NODE_ENV`, `PGSSLMODE`, ports, etc.) restent lisibles hors de ce cas. Les codes ANSI, contrôles de terminal et identifiants intégrés aux URL sont retirés. Si le fichier manque ou si l’environnement ne peut pas être déchiffré, l’email part avec une pièce jointe indiquant que le journal est indisponible.

Le corps du message donne le nom de l’application, le dépôt et la branche du snapshot, le commit du job, sa phase, sa date de fin et le lien vers le dashboard. Le résumé d’erreur reçoit le même masquage que la pièce jointe. Une modification ultérieure du dépôt ou de la branche de l’application ne modifie pas ces métadonnées.

Les emails `deployment_failed` et `app_deployment_failed` ne sont ni regroupés par le délai de cinq minutes ni supprimés par le quota de douze alertes par heure : chaque job garde sa pièce jointe. La déduplication par job, les baux d’envoi et les trois tentatives SMTP restent actifs. Le scan global et la copie issue du scan d’application utilisent la même clé `copy:<APP_ID>:deployment:<JOB_ID>`, donc ne doublent pas l’email plateforme. Si un canal d’application et le canal plateforme visent la même boîte, ils restent deux abonnements distincts. Les limites des autres événements et des webhooks sont inchangées.

**Aucun rattrapage historique.** Le scan commence à `activatedAt` et n’envoie pas les échecs terminés avant l’activation du canal. Enregistrer les canaux avant de lancer le test ; ne pas reculer `activatedAt` ni effacer les curseurs pour rejouer les anciennes erreurs. Les applications absentes, en cours de suppression ou suspendues sont ignorées.

Validation automatisée, sans envoi réel :

```bash
python3 packages/pockethost/scripts/test-web-deployment-email-log-reader.py
python3 packages/pockethost/scripts/test-web-deployment-emails-api.py \
  --source <COPIE_SOURCE_ISOLEE> --pocketbase <POCKETBASE_0_39_11>
```

La seconde commande démarre un PocketBase jetable et un SMTP sur des ports loopback aléatoires. Elle vérifie le MIME, les deux destinataires, le masquage, l’isolation entre applications, les fichiers absents/liens/FIFO, la borne de taille, les trois tentatives et l’absence de rattrapage historique. Ne pas lui fournir le répertoire de données du contrôleur actif.

Pour l’essai SMTP réel, créer une seule application jetable et un seul job déjà en échec, sans build ni conteneur exécutable. Utiliser uniquement des secrets fictifs dans le snapshot et le journal, attendre `emailSentAt` et `status=sent`, puis supprimer l’application par l’API et attendre la fin de son nettoyage. L’état `sent` prouve l’acceptation par le relais SMTP ; la réception dans les deux boîtes reste à vérifier côté destinataire.

#### 4.2.2 Provoquer un échec sur l’application jetable

- **app1 (GitHub)** :
  - Sur la branche suivie du dépôt de test, pousser un commit qui casse la construction : `RUN exit 1` dans le Dockerfile, ou `"build": "exit 1"` dans `package.json`.
  - Le déploiement automatique passe à **Échec** et la version publiée précédente reste en ligne : vérifier HTTP 200 sur l’adresse du site.
  - L’alerte « Échec de déploiement » arrive. Elle indique l’étape et un résumé d’erreur masqué ; l’email joint le journal décrit au §4.2.1.
- **app2 / app3 (image)** :
  - Onglet profil d’exécution : remplacer le digest par `sha256:` suivi de 64 zéros, puis déployer.
  - Le téléchargement de l’image échoue, le déploiement passe à **Échec** et l’alerte arrive.
- Déduplication : un seul message par canal pour ce déploiement. Un nouvel essai crée un nouveau déploiement, donc une nouvelle alerte : c’est normal.
- Remise en état :
  - GitHub : `git revert` du commit cassé et push ;
  - image : remettre le digest valide et redéployer.

  Attendre `ready` et HTTP 200.

### 4.3 Échec de sauvegarde (`backup_failed`)

**Utiliser la méthode B sur tous les hôtes, app1 compris.** Elle ne touche que l’application de test. La méthode A reste un dernier recours, uniquement si la méthode B est impossible.

Avant la méthode A, vérifier qu’aucune autre opération de sauvegarde ne tournera pendant la fenêtre :

```bash
sqlite3 -readonly -column "$DB" "select appId,nextRunAt from web_app_backup_settings where enabled=1 order by nextRunAt limit 10"
sqlite3 -readonly -column "$DB" "select id,appId,status from web_app_backups where status in ('queued','running')"
sqlite3 -readonly "$DB" "select count(*) from web_app_backups where status='deleting'"
sudo -u ubuntu env WEB_PLATFORM_RECOVERY_STATE_DIR=<STATE_DIR> \
  python3 /opt/pockethost-web/web_platform_recovery_service.py api status '{}' | jq '{enabled:.settings.enabled, nextRunAt:.settings.nextRunAt, active}'
```

- La première date de la première requête doit tomber après la fin de la fenêtre, et la deuxième requête doit être vide.
- La troisième doit renvoyer **0**. Une ligne `deleting` (rétention ou suppression manuelle) serait retentée chaque minute pendant la fenêtre. Face au bucket inexistant, le worker signale « bucket indisponible » : la ligne reste `deleting` avec cette erreur, sans épuiser ses essais et sans alerte chez le client, puis la suppression aboutit après la remise en état. Seule une boucle de crash du worker (aucun compte rendu, 3 baux perdus) l’abandonne : elle passe alors `failed` avec `operation=delete`, l’alerte « Suppression de sauvegarde bloquée » part sur le **canal plateforme** uniquement, et un nouvel essai a lieu le lendemain. S’il y a des lignes `deleting`, attendre quand même qu’elles passent à `deleted` avant de commencer, pour garder la fenêtre lisible.
- La capture de reprise de la plateforme ne doit pas tomber dans la fenêtre : `nextRunAt` après la fin de la fenêtre (ou `enabled: false`) et `active` à `null`. Sinon, la préparation de la capture créerait une sauvegarde par site, toutes en échec S3, avec des alertes « Échec de sauvegarde » chez **chaque** client, et la capture échouerait. À défaut, passer `executionEnabled: false` le temps de la fenêtre (WEB_RECOVERY_RUNBOOK §4).
- Sur un hôte sans service de reprise (`api status` indique `configured: false`), ce dernier point est sans objet.

**Méthode A (dernier recours) : copie S3 en échec (hôte avec S3 configuré, app1).**

Cette méthode touche **toutes** les sauvegardes de l’hôte pendant la fenêtre, d’où les contrôles ci-dessus. Garder la fenêtre aussi courte que possible.

1. Repérer le fichier d’environnement du worker :
   ```bash
   systemctl cat pockethost-web-worker.service | grep EnvironmentFile
   ```
   En général : `/home/pockethost-web/web-apps.env`.
2. Le copier sans l’afficher :
   ```bash
   sudo cp -p <ENV> <ENV>.alert-test.bak
   ```
3. Remplacer uniquement le nom du bucket par un bucket inexistant au nom valide, sans afficher les autres lignes :
   ```bash
   sudo sed -i 's/^WEB_BACKUPS_S3_BUCKET=.*/WEB_BACKUPS_S3_BUCKET=kolab-alerte-test-inexistant-2026/' <ENV>
   ```
4. Vérifier qu’aucun déploiement n’est en cours (§0.2), puis :
   ```bash
   sudo systemctl restart pockethost-web-worker.service
   ```
5. Onglet **Sauvegardes** de l’application de test → créer une sauvegarde manuelle. Attendu :
   - archive locale **prête** ;
   - copie S3 **en échec** ;
   - alerte « Échec de sauvegarde » sur les deux canaux, une seule fois.
6. **Remise en état immédiate :**
   ```bash
   sudo cp -p <ENV>.alert-test.bak <ENV>
   sudo systemctl restart pockethost-web-worker.service
   sudo rm <ENV>.alert-test.bak
   ```
   Créer ensuite une nouvelle sauvegarde manuelle et vérifier que sa copie S3 est **prête**.

**Méthode B (recommandée) : échec local limité à l’application de test (tous les hôtes).**

1. Retirer l’étiquette de l’image publiée de l’application de test. Le conteneur continue de tourner sur l’image déjà chargée.
   ```bash
   wdocker inspect --format '{{.Config.Image}}' ph-web-<APP_ID>-<DEP_ID>
   wdocker image rm -f <référence ph-web/<APP_ID>:… affichée ci-dessus>
   ```
2. Créer une sauvegarde manuelle. Elle passe à **Échec** (« L’image publiée est absente… ») et l’alerte « Échec de sauvegarde » arrive.
3. Remise en état : redéployer l’application de test, ou passer directement à sa suppression (§6).

Ne jamais remplir le disque pour provoquer un échec : sous 15 Gio libres, toutes les constructions et sauvegardes de l’hôte sont suspendues.

---

## 5. Déduplication, regroupement et historique

1. Dans l’onglet Alertes, la vue historique doit contenir, pour chaque événement provoqué, **une** ligne par canal à l’état « Envoyée ». Le webhook affiche HTTP 2xx.
2. Le même relevé côté serveur :
   ```bash
   deliveries <APP_ID>
   sqlite3 -readonly "$DB" "select event,channel,count(*) from web_notification_deliveries where appId='<APP_ID>' and trigger='auto' group by eventKey,channel having count(*)>1"
   ```
   La seconde requête doit être vide : aucun doublon. Elle regroupe par `eventKey` (un événement source) : la variante « chemin de santé cassé » du §4.1.6 et le regroupement du §5.3 produisent légitimement un second `health_degraded`/`health_recovered` (nouvelles transitions, nouvelles clés) et ne doivent pas être comptés comme des doublons.
3. **Regroupement.** Avec un intervalle de santé minimal de 60 s et un seuil de 2, un cycle « indisponible → rétabli → indisponible » prend au moins 2 à 3 minutes : un regroupement de 60 s ne peut donc jamais le capter.
   - Onglet Alertes : passer le regroupement à **600 secondes**, enregistrer.
   - Refaire le §4.1 deux fois de suite (arrêt, attente de l’alerte, redémarrage, attente du rétablissement, nouvel arrêt), en moins de 10 minutes au total.
   - Attendu : le second « Site indisponible » (et le second « Site rétabli ») apparaît « Regroupée / annulée » dans l’historique, sans message immédiat : c’est la limite de fréquence voulue. À la fin de l’heure d’horloge en cours (UTC), un résumé « Alertes regroupées » arrive sur chaque canal concerné : le noter aussi.
   - Remettre le regroupement à 60 secondes et enregistrer.

   Les tests manuels ne sont **jamais** regroupés **[après correctifs]**. Au-delà de 10 tests par heure et par canal, un refus explicite s’affiche.
4. **Lien profond.** Ouvrir le lien d’un email d’incident dans un navigateur connecté avec un compte qui a accès à l’application. Il doit ouvrir l’onglet correspondant : Santé, Déploiements ou Sauvegardes.
5. **[après correctifs] Canal plateforme.** Section « Alertes de la plateforme ».
   - Configurer l’email opérateur, puis envoyer un test.
   - Optionnel, dans une fenêtre sans déploiement : `sudo systemctl stop pockethost-web-worker.service`. L’alerte « Worker web injoignable » arrive par **email** entre 2 min 30 et 4 min après l’arrêt : c’est le contrôleur qui l’envoie, le webhook dépend du worker. **Attendre de l’avoir reçue** (ou 5 minutes) avant de redémarrer : un redémarrage plus tôt peut rafraîchir le battement avant le contrôle du chien de garde, et aucune des deux alertes ne part.
   - `sudo systemctl start pockethost-web-worker.service`. L’alerte « Worker web rétabli » suit.
   - Canal enregistré avant les derniers correctifs : vérifier que l’événement « Suppression de sauvegarde bloquée » (`backup_cleanup_failed`) est coché, puis enregistrer. Les nouveaux canaux l’ont par défaut.
6. **Routes du worker fermées depuis Internet.** Depuis le poste opérateur (jamais avec le secret worker) :
   ```bash
   for p in _notifications/claim _platform/recovery-events _worker/claim %5Fnotifications/claim; do
     curl -s -o /dev/null -w "$p %{http_code}\n" -X POST "https://<DASHBOARD>/api/web-apps/$p"
   done
   ```
   Attendu : `404` partout (le pare-feu refuse avant tout relais). Un `401` signale un pare-feu ou un proxy de dashboard antérieur au correctif : seul le secret protège alors ces routes, à corriger avant d’activer les alertes.
   - « Contrôleur injoignable » (`controller_unreachable`) : le worker l’envoie lui-même, **par webhook uniquement**, au webhook du canal plateforme qu’il garde en mémoire, après 3 minutes de réclamations en échec (3 tentatives au plus, une par minute). Il n’existe pas de voie email quand le contrôleur est arrêté. Ce chemin est couvert hors ligne par `test-web-observability.py`. Une validation réelle impose d’arrêter le contrôleur plus de 3 minutes (dashboard et API indisponibles) : la réserver à une fenêtre de maintenance annoncée, sur app2 ou app3, avec le webhook plateforme déjà enregistré et le worker en marche.

---

## 6. Retour arrière et nettoyage (obligatoire)

1. Onglet Alertes de l’application de test : décocher les deux canaux, cocher « Effacer ce webhook à l’enregistrement », enregistrer.
2. Onglet Santé : désactiver la surveillance.
3. Supprimer l’application de test depuis ses paramètres. Vérifier que plus aucun conteneur ne subsiste :
   ```bash
   wdocker ps -a --filter name=ph-web-<APP_ID>
   ```
4. Méthode A : vérifier qu’il ne reste aucune trace de la modification :
   ```bash
   sudo grep -c 'kolab-alerte-test-inexistant' <ENV>
   ```
   Attendu : 0. Vérifier aussi que le worker est `active` et que la dernière sauvegarde d’une application réelle a une copie S3 **prête** (ou la prochaine sauvegarde planifiée).
5. GitHub : supprimer le dépôt de test ou annuler le commit cassé. Discord ou Slack : supprimer le webhook de test, s’il ne devient pas le canal de production.
6. SMTP : laisser la configuration validée en place. Supprimer seulement les éventuelles adresses de test.
7. Si les correctifs viennent d’être déployés et posent problème, suivre le retour arrière de la release : migration descendante, puis bundle hooks et build dashboard précédents (plan de câblage §12). Les sites clients ne sont pas concernés.

---

## 7. Compte rendu

Noter les résultats sans aucune valeur secrète, par exemple dans `.secret/web-alerts-validation-<serveur>.json`, puis reporter la conclusion dans `WEB_HOSTING.md` et dans la ligne « Recevoir des alertes » de `WEB_HOSTING_ROADMAP.md`.

| Contrôle | app1 | app2 | app3 |
| --- | --- | --- | --- |
| SMTP « Tester SMTP » OK | | | |
| SPF / DKIM / DMARC = pass (en-têtes) | | | |
| Email non classé en indésirables | | | |
| Test webhook reçu (format, signature si générique) | | | |
| Test email reçu (objet, expéditeur, lien) | | | |
| Site indisponible : reçu une fois par canal | | | |
| Site rétabli : reçu une fois par canal | | | |
| Échec de déploiement : reçu une fois, site resté en ligne | | | |
| Échec de sauvegarde (méthode A ou B) : reçu une fois | | | |
| Aucun doublon (requête §5.2 vide) | | | |
| Regroupement observé sur un incident répété | | | |
| Lien profond ouvre le bon onglet | | | |
| Canal plateforme / worker injoignable [après correctifs] | | | |
| Routes `/api/web-apps/_*` en 404 depuis Internet (§5.6) | | | |
| Nettoyage §6 terminé (app supprimée, env restauré) | | | |
| Release testée | | | |
