# Runbook de reprise de la plateforme (app1, puis app2/app3)

Procédure opérateur pour (A) prouver la reprise complète d’app1 sans toucher à la production, (B) activer la capture nocturne, (C) basculer réellement sur un nouveau serveur après la perte d’app1. Les sections 6 et 7 couvrent app2/app3 et les preuves à archiver.

**État au 29 octobre 2026 :** aucune capture complète des 7 bases n’a encore atteint `ready`. Les trois dernières tentatives ont échoué sans dommage : `cd65718d` en préparation, `33d069b4` faute de clés MFA, `d106ac2f` à cause d’une archive UXEO retirée en cours de capture. Ce dernier cas est désormais géré par `reconcile_retired_archives`. La planification est désactivée, avec 21:30 UTC et une rétention de 3 déjà saisies. `executionEnabled` vaut `false` sur app1.

## Résultats du 29-30 octobre 2026

**Reprise complète non validée** : aucune écriture n’a pu être faite sur app1 cette nuit. La première écriture, la génération du plan dans un nouveau fichier vers 19:00 UTC, a été refusée par le système de permissions de la session. Les étapes 1 à 5 restent donc à faire. Aucune capture n’a été lancée, aucun service n’a été redémarré et rien n’a changé sur les sites.

Pré-vol en lecture seule (29/10, 18:50-19:00 UTC) :

- Scripts : les 6 empreintes déployées dans `/opt/pockethost-web` sont égales au dépôt (`root:root 0644`). `protectCustomerBackups`, `controller_excludes_platform`, `skip_locked_occurrence` et `skippedScheduledDate` sont présents.
- Service : `active/running`, `NRestarts=0`, `worker.online=true`, `canCapture` et `canDrill` à `true`, `active == null`. Les trois runs sont en échec (`cd65718d`, `33d069b4`, `d106ac2f`). La planification est désactivée (21:30, rétention 3).
- `executionEnabled` vaut désormais **`true`** dans `<CFG>`, contrairement à l’état noté plus haut. `failureEvents` est absent, donc désactivé.
- Plan : `check` termine sur `exit=0`, sans dérive, avec 6 instances vivantes et 6 planifiées. `<N>` = 7. Le mothership tourne sur `0.39.11`. Cinq instances sont en `0.39.*` et `qiqimptesxh6jyt` en `0.40.*`. `drillRoot` est hors de `<DATA_ROOT>` et hors des `hostPaths`, mais le dossier `drills/` n’existe pas encore.
- L’avertissement « binaires non capturés » est un **faux positif** : le plan capture déjà `platform/pocketbase/0.39.11/…` et `0.40.6/…` fichier par fichier, et la sonde native les reconnaît. L’outil n’accepte que la cible exacte `platform/pocketbase`. Correctif prévu : une entrée unique `<PB_ROOT>` → `platform/pocketbase` (64 binaires, environ 2,46 Go par capture), à la place des deux entrées par fichier. Les garder en plus provoquerait une cible dupliquée.
- `hostPaths` incomplets :
  - `/home/pockethost-web/web-apps.env` manque. Il appartient à `pockethost-web` en 0600, et le service, qui tourne sous `ubuntu`, ne peut pas le lire. `WEB_APPS_WORKER_SECRET` reste disponible dans le `.env` capturé.
  - Les unités `pockethost-web-worker.service` et `pockethost-web-terminal.service` manquent aussi.
- Disque : 63 Go libres sur `/` (103 Go au total). La plus grosse base est `qyf2wsb3qx7pqq8`, avec 13,1 Go. La restauration complète est estimée à environ 17 Go.
- Sites : la préparation crée une archive `platform` pour 8 sites. Seul `23efjvjl2otqpi3` a un volume (`uploads`) et sera arrêté brièvement. Les deux sites en ligne n’ont pas de volume.

Procédure prête à lancer, avec les valeurs d’app1 et le remplacement du plan sur place : le plan se capture lui-même, donc `planFile` reste inchangé. Elle couvre aussi le fichier secret de `failureEvents`, hors de `.secret/`, et sa vérification face au contrôleur en cours. Elle se trouve dans `scratchpad/trackA-app1-recovery-morning.md` de la session du 29/10. La planification reste **désactivée** tant que la capture et l’exercice des 7 bases n’ont pas réussi.

## 0. Règles et espaces réservés

- Ne jamais écrire une clé, un mot de passe ou un identifiant S3 dans une ligne de commande, un ticket ou un rapport. On ne transmet que des **chemins** de fichiers privés (mode 0600, sans lien symbolique).
- Les commandes qui ont besoin des identifiants S3 passent par `systemd-run -p EnvironmentFile=…`. Les secrets n’entrent ainsi ni dans le shell, ni dans l’historique, ni dans `ps`.
- Sauf mention `sudo`, tout s’exécute sur le serveur en tant que `ubuntu`. Les fichiers restaurés appartiennent à l’utilisateur qui a lancé la restauration. La sonde refuse les fichiers d’un autre propriétaire que root ou l’utilisateur courant.
- Outils livrés dans le dépôt et alignés ici :
  - `web_platform_recovery_plan.py` (`check`, `generate`) ;
  - `web_platform_recovery_probe.py native-proof` (lanceur root PrivateNetwork) ;
  - `web_platform_recovery.py inspect --target` (budget) ;
  - `web_platform_recovery_service.py` `captures`, `hold`, `release`, `record-drill`, `sweep`.

  Vérifier qu’ils sont **déployés** (§1.1) avant de les utiliser, et relire `--help`.
- La levée de pause par catégorie (`web_platform_recovery_unpause.py`, §5.8) est livrée elle aussi. Aucun `TODO-FLAG` ne subsiste au 29/10 ; relire tout de même `--help` sur la version déployée.

| Espace réservé | Signification | Comment l’obtenir |
|---|---|---|
| `<CFG>` | Configuration privée du service de reprise | `systemctl cat pockethost-platform-recovery.service`, argument `--config` |
| `<ENV_REPRISE>` | Fichier d’environnement de l’unité (`WEB_PLATFORM_S3_*` ou `WEB_BACKUPS_S3_*`, `WEB_PLATFORM_RECOVERY_PREFIX`) | `systemctl show -p EnvironmentFiles pockethost-platform-recovery.service` |
| `<STATE_DIR>`, `<PLAN>`, `<KEYFILE>`, `<DRILL_ROOT>` | Champs `stateDir`, `planFile`, `keyFile`, `drillRoot` de `<CFG>` | étape 1.2 |
| `<DATA_ROOT>` | `/home/ubuntu/.local/share/pockethost/data` | `.env`, `srv.md` §5 |
| `<PB_ROOT>` | `/home/ubuntu/.local/share/pockethost/pocketbase` | `srv.md` §10 |
| `<MOTHERSHIP_SEMVER>` | Version du contrôleur, `0.39.*` par défaut | `grep ^MOTHERSHIP_SEMVER /home/ubuntu/pockethost/.env` |
| `<N>` | Bases attendues = 1 (contrôleur) + nombre d’instances | étape 1.4 |
| `<RUN_ID>`, `<OBJ>` | Identifiant de run (32 hex) et objet S3 `…/<backupId>/platform.phpr` | étape 2 |
| `<R>` | Dossier privé des rapports, hors de la copie | `mkdir -m 700` |

Le script vit dans `/opt/pockethost-web/`. Ses sources se trouvent dans `packages/pockethost/scripts/` : `web_platform_recovery.py` (moteur), `web_platform_recovery_service.py` (service et `api`), `web_platform_recovery_control.py` (file d’attente), `web_platform_recovery_probe.py` (sonde et lanceur `native-proof`).

---

## 1. Pré-vol sur app1 (lecture seule)

### 1.1 Les scripts déployés correspondent au dépôt

Sur le poste opérateur :

```bash
cd /Users/guillaume/DEV/pockethos/packages/pockethost/scripts
shasum -a 256 web_platform_recovery.py web_platform_recovery_control.py \
  web_platform_recovery_probe.py web_platform_recovery_service.py \
  web_platform_recovery_plan.py web_platform_recovery_unpause.py > /tmp/recovery-repo.sha256
scp /tmp/recovery-repo.sha256 ubuntu@141.94.92.92:/tmp/recovery-repo.sha256
```

Sur app1 :

```bash
cd /opt/pockethost-web && sha256sum -c /tmp/recovery-repo.sha256
stat -c '%U:%G %a %n' /opt/pockethost-web/web_platform_recovery*.py
grep -c reconcile_retired_archives /opt/pockethost-web/web_platform_recovery.py   # >= 1
grep -c "native-proof" /opt/pockethost-web/web_platform_recovery_probe.py          # >= 1 (lanceur)
grep -c "budget_check" /opt/pockethost-web/web_platform_recovery.py              # >= 1 (budget d’inspection)
```

Si une ligne affiche `FAILED` ou si un fichier manque (les outils de plan et de levée de pause sont nouveaux), redéployer les six fichiers ensemble, **quand aucune opération n’est active** (voir 1.3). Conserver le propriétaire et le mode relevés par `stat`, par exemple `sudo install -o <PROPRIO> -g <GROUPE> -m <MODE> <fichier> /opt/pockethost-web/`. Redémarrer ensuite l’unité (`sudo systemctl restart pockethost-platform-recovery.service`). Noter les nouvelles empreintes dans le rapport de preuve (§7).

### 1.2 Configuration du service (valeurs non secrètes)

```bash
systemctl cat pockethost-platform-recovery.service        # User=, EnvironmentFile=, --config
systemctl show -p EnvironmentFiles,User,ActiveState pockethost-platform-recovery.service
jq '{version,platform,executionEnabled,stateDir,planFile,keyFile,drillRoot,dataRoot,drillSelect,maxDrillBytes,drillMinFreeGiB,captureTimeoutSeconds,
     drillVerify,drillCleanup,sweepEnabled,sweepMinAgeHours,planFromInventory,failureEvents:(.failureEvents|{enabled}),adminUserIdSet:(.adminUserId!=null)}' <CFG>
```

Ce qu’il faut vérifier :

- `platform` vaut `app1`, et doit être identique au `platform` du plan : le service refuse un plan d’une autre plateforme. Les clés absentes prennent les valeurs par défaut du code. `drillVerify`, `drillCleanup` et `sweepEnabled` valent `true`. `planFromInventory` vaut `false`. À `true`, il recalcule les instances du plan à chaque capture.
- `failureEvents.enabled` vaut `false` tant que la route d’alerte du contrôleur n’est pas déployée (§4.7). Elle est implémentée localement mais pas encore déployée. Son déploiement exige **tout** le répertoire `pb_migrations` (au moins `1793271100`, `1793271200` et `1793271300` : les hooks lisent des champs de `1793271100`), le bundle `pb_hooks` et la release du pare-feu et du dashboard qui refuse les routes `/api/web-apps/_*` venant d’Internet.
- `executionEnabled` : la valeur absente compte comme `true`, mais app1 a `false` aujourd’hui.
- `adminUserIdSet` devrait valoir `true`. Fixer `adminUserId` évite que la préparation emprunte au hasard le premier superAdmin.
- `drillRoot` doit être **hors de `<DATA_ROOT>`** et hors de toute source `hostPaths` du plan (voir 1.4). Sinon, la capture suivante embarquerait la copie de l’exercice.

### 1.3 Aucune opération active

```bash
sudo -u ubuntu env WEB_PLATFORM_RECOVERY_STATE_DIR=<STATE_DIR> \
  python3 /opt/pockethost-web/web_platform_recovery_service.py api status '{}' \
  | jq '{configured, worker, capabilities, settings, active:(.active|{id,operation,status,stage}), latest:(.latest|{id,status,verified,recoverable})}'
```

Il faut `active == null` et `worker.online == true`. Le redémarrage de l’unité tue toute opération en cours.

### 1.4 Dérive du plan et nombre de bases attendu

**Outil de plan** (`web_platform_recovery_plan.py`, à déployer dans `/opt/pockethost-web` avec les autres scripts et à ajouter à 1.1). Il ouvre le contrôleur en lecture seule :

```bash
sudo -u ubuntu python3 /opt/pockethost-web/web_platform_recovery_plan.py check --plan <PLAN> --data-root <DATA_ROOT>; echo "exit=$?"
# exit=0 : plan aligné ; exit=3 : dérive ; exit=2 : erreur (JSON sur stderr)
# sortie : {drift, added[], removed[], databaseMissing[], pathMismatch[], liveInstances, plannedInstances, warnings[]}
```

Si une dérive est signalée, regénérer le plan dans un **nouveau** fichier. La commande garde `hostPaths`, `nativeBackups`, les `s3Refs` statiques et les options du plan de base. Relire le résultat, puis remplacer `<PLAN>` (0600) en dehors de toute opération active :

```bash
sudo -u ubuntu python3 /opt/pockethost-web/web_platform_recovery_plan.py generate --base <PLAN> \
  --output <DOSSIER_DU_PLAN>/plan-$(date -u +%Y%m%dT%H%M%SZ).json --data-root <DATA_ROOT>
# rapport : {instances, added[], removed[], withoutDatabase[], nativeBinariesCaptured, written}
```

`--allow-without-database` n’est à utiliser qu’après avoir vérifié qu’une instance n’a pas encore de base. Le service publie aussi un `planCheck` (`drift`, `added`, `removed`, `checkedAt`) dans `api status`, s’il est déployé dans une version qui le calcule.

Le contrôle complémentaire ci-dessous reste requis pour `<N>`, les versions, `hostPaths` et l’emplacement de `drillRoot`. Il n’affiche que des identifiants et des booléens.

```bash
sudo -u ubuntu python3 -I - <CFG> <<'EOF'
import json, pathlib, sqlite3, sys
cfg = json.loads(pathlib.Path(sys.argv[1]).read_text()); plan = json.loads(pathlib.Path(cfg['planFile']).read_text())
db = pathlib.Path(plan['controller']['database'])
con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
live = dict(con.execute('SELECT id, version FROM instances').fetchall())
planned = {i['id']: i for i in plan.get('instances', [])}
sources = [pathlib.Path(h['source']).resolve() for h in plan.get('hostPaths', [])]
drill = pathlib.Path(cfg['drillRoot']).resolve()
# withoutDatabase:true (generate --allow-without-database / planFromInventory) = instance jamais démarrée :
# la capture la saute tant que son fichier est absent, elle ne compte donc pas dans <N>.
skipped = sorted(i for i, d in planned.items() if d.get('withoutDatabase') is True and not pathlib.Path(d['database']).exists())
print(json.dumps({
  'instancesLive': len(live), 'expectedDatabases': 1 + sum(1 for i in planned if i in live and i not in skipped),
  'withoutDatabase': skipped,
  'missingFromPlan': sorted(set(live) - set(planned)), 'extraInPlan': sorted(set(planned) - set(live)),
  'missingDatabases': sorted(i for i, d in planned.items() if i not in skipped and not pathlib.Path(d['database']).is_file()),
  'versions': sorted(set(live.values())),
  'pocketbaseHostPath': any(h.get('target') == 'platform/pocketbase' for h in plan.get('hostPaths', [])),
  'hostPathTargets': sorted(h.get('target') for h in plan.get('hostPaths', [])),
  'drillRootInsideHostPath': any(drill == s or s in drill.parents for s in sources),
  'drillRootInsideDataRoot': db.parents[2] in drill.parents}, indent=1))
EOF
```

**Arrêt** si `check` ne renvoie pas `exit=0`, ou si `missingFromPlan`, `extraInPlan` ou `missingDatabases` n’est pas vide. La capture échouerait : instance absente du plan, ou « Base SQLite source absente ». Corriger le plan avec `generate` avant de continuer. Ne pas activer `allowPartial`.

Arrêt aussi si `pocketbaseHostPath` vaut `false` : la preuve native n’aurait pas de binaires. Même chose si `drillRoot*` vaut `true`.

Pour la suite, **`<N>` = `expectedDatabases`** (7 au dernier inventaire : le contrôleur plus 6 instances, à recompter le jour même). Les instances listées dans `withoutDatabase` ne comptent pas : la capture les saute (`coverage.instancesWithoutDatabase`). Après la capture du §2, recouper avec `jq '.result.databases|length'` : c’est ce nombre que `restore`, `probe --expected-databases` et `native-proof` attendent. Chaque version doit être de la forme `x.y.z` ou `x.y.*`, et le dossier `<PB_ROOT>/<version>/linux_amd64/pocketbase` doit exister.

Dans `hostPathTargets`, vérifier la présence des éléments qu’une perte totale exige :

- le `.env` du projet ;
- `/home/pockethost-web/web-apps.env` ;
- les unités systemd ;
- `/opt/pockethost-web` ;
- `ssl/` ;
- la configuration privée du service de reprise et le mot de passe technique ;
- les `pb_hooks`/`pb_public` des instances s’ils existent sous `<DATA_ROOT>/instances/<id>/`. La capture d’une instance ne prend que `pb_data` et son stockage.

Garder **une copie du plan** (chemins uniquement, aucun secret) sur le poste, dans `.secret/web-platform-app1-plan-<AAAAMMJJ>.json`. Le manifeste ne contient pas les chemins sources des `host-files/`, et sans le plan on ne sait plus où les remettre (§5.9).

### 1.5 Budget disque

```bash
df -B1 --output=target,avail <DATA_ROOT> <DRILL_ROOT>
du -sb <DATA_ROOT>/mothership/pb_data <DATA_ROOT>/instances/*/pb_data | sort -n | tail
```

La restauration exige 15 Gio + les octets restaurés sur le disque de `<DRILL_ROOT>`, avec un contrôle continu pendant l’opération. La capture exige de son côté 15 Gio libres sur la source. Si les deux partagent le disque d’app1, une copie d’exercice oubliée fait échouer les captures nocturnes. Le chiffre exact vient de `inspect --target` (étape 3.1).

---

## 2. Capture manuelle et contrôle de couverture

1. Si `executionEnabled` vaut `false`, l’éditer dans `<CFG>`. Garder le mode 0600 et le propriétaire, ne pas créer de copie de sauvegarde à côté, puis :
   ```bash
   sudo systemctl restart pockethost-platform-recovery.service
   jq '{online,lastSeenAt,capabilities}' <STATE_DIR>/worker.json   # canCapture=true, canDrill=true
   ```
2. Dashboard → `/applications/recovery` → **Capturer maintenant**. La préparation prend un snapshot de chaque volume de site, et **chaque site à volume est brièvement arrêté**. Choisir un créneau calme.
3. Attendre `ready`. La couverture doit afficher **Complète** et 0 dépendance non protégée. Un échec n’est jamais une preuve.
4. Sur le serveur :
   ```bash
   RUN=$(ls -t <STATE_DIR>/runs | head -1 | sed 's/\.json$//'); echo "$RUN"
   jq '{id,operation,status,stageCode,errorCode,verified,trigger,
        object:.result.object, databases:(.result.databases|length),
        coverage:.result.coverage, retention:.result.retention, encryptedBytes:.result.encryptedBytes}' <STATE_DIR>/runs/$RUN.json
   ```
   Attendu : `status: "ready"`, `verified: true`, `databases == <N>`, `coverage.complete: true`, et `instancesMissing`, `unprotectedWebImages` et `unprotectedVolumes` vides.
   En cas d’échec, lire `jq -r .privateEngineError <STATE_DIR>/runs/$RUN.json`. Ce texte est privé : ne pas le copier tel quel dans un ticket.
5. `OBJ=$(jq -r .result.object <STATE_DIR>/runs/$RUN.json)`. Noter `RUN` et `OBJ` pour la suite.

---

## 3. Exercice complet des `<N>` bases (CLI, cible isolée neuve)

Le bouton du dashboard ne restaure que `drillSelect` (le contrôleur et une petite base). Le test complet passe par la CLI. La planification doit rester **désactivée** pendant l’exercice.

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
T=<DRILL_ROOT>/full-${TS}             # NE PAS créer : le moteur le crée en 0700
R=/home/ubuntu/recovery-reports/${TS}; mkdir -p -m 700 "$R"
```

Poser d’abord la réservation `hold` sur `$RUN` (voir 3.6), pour que la rétention ne puisse pas purger la capture pendant l’exercice.

### 3.1 Inspection avec budget (télécharge l’archive une fois)

```bash
sudo systemd-run --uid=ubuntu --gid=ubuntu --wait --pipe --collect --quiet \
  -p EnvironmentFile=<ENV_REPRISE> -p MemoryMax=2G -p CPUQuota=100% -p IOWeight=50 -p UMask=0077 -p RuntimeMaxSec=6h \
  /usr/bin/python3 /opt/pockethost-web/web_platform_recovery.py inspect \
    --object "$OBJ" --key-file <KEYFILE> \
    --with-host-files --with-native-archives --target "$T" \
  > "$R/inspect.json" 2> "$R/inspect.progress.jsonl"
jq '{status, backupId, databases:(.databases|length), coverage:.coverage.complete, budget}' "$R/inspect.json"
```

Attendu : `status: "authenticated"`, `databases == <N>`, `budget.fits: true`.

Si `fits` vaut `false`, deux options. On peut libérer de l’espace, sans jamais descendre sous 15 Gio : `--offline` est interdit sur le serveur actif. On peut aussi relancer sans `--with-native-archives` et noter ce périmètre réduit dans le rapport. `restoreEstimate` donne les variantes de taille.

### 3.2 Restauration de toutes les bases, en pause

Sans `--select`, toutes les bases du manifeste sont restaurées.

```bash
sudo systemd-run --uid=ubuntu --gid=ubuntu --wait --pipe --collect --quiet \
  -p EnvironmentFile=<ENV_REPRISE> -p MemoryMax=2G -p CPUQuota=100% -p IOWeight=50 -p UMask=0077 -p RuntimeMaxSec=6h \
  /usr/bin/python3 /opt/pockethost-web/web_platform_recovery.py restore \
    --object "$OBJ" --key-file <KEYFILE> --target "$T" \
    --with-host-files --with-native-archives --min-free-gib 15 \
  > "$R/restore.json" 2> "$R/restore.progress.jsonl"
jq '{status, paused, noServicesStarted, databasesRestored, hostFilesRestored, nativeArchivesRestored, webImagesAndVolumesRequireExplicitRecovery}' "$R/restore.json"
```

La restauration télécharge l’archive deux fois : une authentification complète, puis la reconstruction.

### 3.3 Contrôle de `RECOVERY_PAUSED.json`

```bash
jq '{paused, noServicesStarted, backupId, n:(.databasesRestored|length), controller:(.databasesRestored|index("controller")!=null), hostFilesRestored, nativeArchivesRestored, requiredSystemd}' "$T/RECOVERY_PAUSED.json"
jq -r '.files[].target' "$T/recovery-manifest.json" | grep -E '^host-files/platform/pocketbase/[0-9.]+/linux_amd64/pocketbase$'
jq . "$T/recovery-env-overrides.json"        # attendu : {"WEB_BACKUPS_S3_PREFIX": "<prefixe>/<backupId>/refs/web"}
```

Attendu : `paused` et `noServicesStarted` à `true`, `n == <N>`, `controller: true`, et un binaire capturé pour chaque version de 1.4.

### 3.4 Sonde de vérification seule

```bash
python3 /opt/pockethost-web/web_platform_recovery_probe.py \
  --root "$T" --expected-databases <N> --report "$R/verify-only.json" --verify-only
```

Attendu : `passed: true`, `databases == <N>`, `mfaSidecars` égal au nombre de bases MFA, et `files` égal au nombre d’entrées du manifeste dans le périmètre restauré.

### 3.5 Preuve de démarrage natif (PrivateNetwork, lanceur `native-proof`)

Le lanceur s’exécute en root. Il vérifie le marqueur, la couverture et les empreintes des binaires, puis démarre la sonde **sous l’utilisateur propriétaire de la copie** dans une unité transitoire. Cette unité est isolée : `PrivateNetwork=yes`, `IPAddressDeny=any`, `ProtectSystem=strict`, `ProtectHome=read-only`, RAM 2 Gio, 30 min au maximum par défaut (`--max-seconds`, de 300 à 21600 s, règle à la fois `RuntimeMaxSec` de l’unité et le délai du lanceur ; à relever pour les grosses bases, voir §6). Chaque base démarre sur `127.0.0.1` avec des hooks, migrations et `public` vides, puis s’arrête.

```bash
# 1) Essai à blanc : affiche la commande exacte et les versions résolues, ne démarre rien
sudo python3 /opt/pockethost-web/web_platform_recovery_probe.py native-proof \
  --root "$T" --expected-databases <N> --report "$R/native.json" \
  --runtime-root <PB_ROOT> --controller-version '<MOTHERSHIP_SEMVER>' --user ubuntu \
  | jq '{mode, unit, binaries, hashesVerified}'
# 2) Exécution
sudo python3 /opt/pockethost-web/web_platform_recovery_probe.py native-proof \
  --root "$T" --expected-databases <N> --report "$R/native.json" \
  --runtime-root <PB_ROOT> --controller-version '<MOTHERSHIP_SEMVER>' --user ubuntu --execute \
  | tee "$R/native-launcher.json" | jq '{passed, unitStopped, probe}'
```

Attendu : `passed: true`, `unitStopped: true`, `probe.nativeHealthy == <N>`, `probe.noExternalNetwork: true`.

- Le rapport doit être **hors** de `$T`.
- Sur un hôte arm64, ajouter `--arch linux_amd64|linux_arm64` selon les binaires capturés.
- En cas d’échec, lire les journaux `"$T"/native-probe/attempt-*/<id>.log`, qui sont privés. Une nouvelle tentative nettoie seule les tentatives précédentes.

Un repli manuel existe si le lanceur est absent du déploiement : `systemd-run` avec `-p PrivateNetwork=yes … --uid=ubuntu` et `--host-netns-inode "$(sudo stat -L -c %i /proc/1/ns/net)"`. Ne jamais lancer la sonde native en root.

### 3.6 Enregistrer l’exercice pour que la rétention protège la capture

Une restauration CLI ne crée **aucun run** dans `<STATE_DIR>/runs`. La rétention (N plus récentes + source du dernier test réussi) peut donc purger la capture qui vient d’être prouvée.

Les commandes opérateur du service tournent sous `ubuntu`, avec la même `--config`. Le chargement de la configuration ouvre la clé de reprise et le mot de passe technique (contrôle de fichiers privés) : elles doivent donc tourner sous le propriétaire de ces fichiers. `captures`, `hold`, `release` et `record-drill` ne contactent pas S3 ; `sweep` le contacte et passe par l’enveloppe `systemd-run` (§4.8).

**Avant 3.1**, poser une réservation sur la capture (de 1 à 720 h, 48 h par défaut). `apply_retention` et la reprise d’une purge interrompue l’honorent :

```bash
sudo -u ubuntu python3 /opt/pockethost-web/web_platform_recovery_service.py hold --config <CFG> \
  --source "$RUN" --hours 48 --label "exercice complet $TS" | tee "$R/hold.json"
sudo -u ubuntu python3 /opt/pockethost-web/web_platform_recovery_service.py captures --config <CFG> \
  | jq '.captures[] | {id, backupId, databases, held, restoreEstimate}'
```

**Après 3.5**, enregistrer l’exercice. Le service relit les rapports et vérifie trois choses : même `backupId`, contrôleur présent, `nativeHealthy` égal au nombre de bases avec `noExternalNetwork`. Il crée ensuite un run `drill` `ready` (`trigger: "cli"`, `sourceRunId=$RUN`). Comme dernier test réussi, cette capture reste protégée même hors des 3 plus récentes.

```bash
sudo -u ubuntu python3 /opt/pockethost-web/web_platform_recovery_service.py record-drill --config <CFG> \
  --source "$RUN" --report "$R/verify-only.json" --native-report "$R/native.json" | tee "$R/drill-record.json"
# puis, si la réservation n’est plus utile :
sudo -u ubuntu python3 /opt/pockethost-web/web_platform_recovery_service.py release --config <CFG> --source "$RUN"
```

Si la version déployée ne connaît pas encore `hold` ou `record-drill`, utiliser le repli : **Vérifier cette capture** dans `/applications/recovery` sur la même capture. Son run `ready` protège `$RUN`. Avec `drillVerify` et `drillCleanup` à `true` (valeurs par défaut récentes), le service contrôle la copie partielle puis la supprime. Sinon, supprimer `<DRILL_ROOT>/<id du run>` à la main.

### 3.7 Nettoyage (obligatoire)

`$T` contient toutes les données de production, les clés TOTP, `.env` et les clés TLS.

```bash
pgrep -af "pocketbase serve --http=127.0.0.1" | grep -F "$T" || echo "aucun processus"
rm -rf -- "$T"
df -B1 --output=avail <DRILL_ROOT>
ls "$R"     # inspect.json restore.json verify-only.json native.json native-launcher.json (+ *.progress.jsonl)
```

Rapatrier `$R` vers le poste (§7). Ces rapports ne contiennent ni clé ni identifiant S3.

---

## 4. Activer la planification (21:30 UTC, rétention 3)

Préalable : sections 2 et 3 réussies le même jour.

**Sauvegardes des sites et rétention par application.** Chaque capture prépare une sauvegarde d’archive par site publié. Côté contrôleur, toutes les lignes `ready` d’une application comptent dans sa rétention (réglage du site, 3 par défaut), quel que soit leur déclencheur. Une capture nocturne qui créerait chaque nuit une archive pousserait donc dehors les sauvegardes manuelles ou planifiées du client.
- Version du service ≥ correctif du 29/10 (`grep -c protectCustomerBackups /opt/pockethost-web/web_platform_recovery_service.py` ≥ 1) : avec `protectCustomerBackups` (vrai par défaut), le service ne crée une archive que si elle n’évince **que** des archives qu’il a lui-même créées (liste privée `<STATE_DIR>/platform-web-backups.json`). Sinon il réutilise l’archive prête la plus récente du site. Le run indique `webBackups: {created, reused, staleReused}` : `staleReused` liste les sites dont l’archive réutilisée ne correspond plus au déploiement actif (code de l’archive plus ancien que la version publiée ; les volumes restent capturés à chaque fois).
- Version antérieure (résultat 0) : **ne pas armer la planification** avant d’avoir déployé le correctif, sinon trois nuits suffisent à remplacer toutes les sauvegardes client d’un site sans planification propre.
- Correctif définitif côté contrôleur : un déclencheur `platform` exclu du compte de rétention des sites. **Implémenté localement, non déployé** : les lignes `trigger=platform` sont réservées aux superAdmins, hors rétention client, seule la plus récente par site est gardée, et le dashboard les affiche « Plateforme (reprise) ». Tant que ce contrôleur n’est pas déployé, seul `protectCustomerBackups` protège les archives client.
- Bascule automatique : ce contrôleur publie `platformRetention: "excluded"` dans `GET /api/web-apps/<id>/backups`. Un service à jour (`grep -c controller_excludes_platform /opt/pockethost-web/web_platform_recovery_service.py` ≥ 1) le lit une fois par capture, quand il s’apprêtait à réutiliser une archive client : il crée alors **toujours** une archive fraîche (plus de `staleReused` dû à la rétention). Rien à changer dans `<CFG>` ; `protectCustomerBackups` reste utile face à un contrôleur plus ancien. Un service antérieur continue de réutiliser l’archive client la plus récente, même avec le nouveau contrôleur : le déployer en même temps.

1. Vérifier `executionEnabled: true` dans `<CFG>` et `canCapture: true` dans `worker.json`. Laisser `drillSelect` sur le sous-ensemble.
2. **Agir avant 21:30 UTC**, soit 01:30 à La Réunion. Vérifier d’abord le comportement de la version déployée : `grep -c skippedScheduledDate /opt/pockethost-web/web_platform_recovery_control.py`.
   - Résultat ≥ 1 : armer après l’heure saute la journée. La première capture aura lieu le lendemain, sauf `runToday` explicite, et la réponse indique `immediate`.
   - Résultat 0 (ancienne version) : armer après l’heure met **immédiatement** une capture en file.
3. Dashboard `/applications/recovery` : cocher **Activer chaque nuit**, saisir `21:30` (UTC), rétention `3`, puis **Enregistrer**. Passer par le dashboard pour que la ligne d’audit `web_platform_recovery_audit` soit écrite.
4. Contrôle :
   ```bash
   sudo -u ubuntu env WEB_PLATFORM_RECOVERY_STATE_DIR=<STATE_DIR> \
     python3 /opt/pockethost-web/web_platform_recovery_service.py api status '{}' | jq '{settings, capabilities}'
   ```
   Attendu : `enabled: true`, `hourUTC: 21`, `minuteUTC: 30`, `retention: 3`, `nextRunAt` au prochain 21:30 UTC, `canCapture: true`.
   Avec la version récente du contrôle, il faut aussi `settings.status == "active"`. Dans les autres cas, `nextRunAt` est vide et `statusMessage` en donne la raison : `executionDisabled`, `serviceOffline` ou `notConfigured`. Les anciennes versions affichent « Prochaine capture » même si `executionEnabled` vaut `false` : seul `capabilities` fait alors foi.
5. Le lendemain, `ls -t <STATE_DIR>/runs | head -3`. Le dernier run doit avoir `trigger: "scheduled"`, `status: "ready"` et `coverage.complete: true`.
6. Rétention : la capture source du dernier exercice réussi (`record-drill` du §3.6 sur la capture manuelle du §2) reste protégée en plus des 3 plus récentes. Après la 4e capture prête (la manuelle + 3 planifiées), attendre donc `.result.retention.removed == 0` ; c’est à la 5e que `removed == 1` (la plus ancienne planifiée part, la capture exercée reste). `delayed: true` signale une purge S3 incomplète : cette copie n’est alors plus éligible aux tests.
7. Alertes d’échec. Une seule alerte par échec : l’événement `recovery_failed`, clé `platform-recovery:<runId>`, sur le canal plateforme. Deux producteurs partagent cette clé et sont dédupliqués par le contrôleur :
   - l’envoi immédiat du service (sans secret, idempotent par run) vers `POST /api/web-apps/_platform/recovery-events`, avec l’en-tête du secret worker. Il faut `failureEvents: {"enabled": true, "secretFile": "<fichier 0600 contenant WEB_APPS_WORKER_SECRET>"}` dans `<CFG>`, puis un redémarrage de l’unité. Ce secret ouvre toutes les routes du worker (dont les webhooks et secrets HMAC en file) : le fichier reste `ubuntu` 0600, hors de `.secret/`. La route n’accepte qu’une connexion directe en boucle locale, sans en-tête de relais : `controlUrl` doit viser PocketBase lui-même (`http://127.0.0.1:8091`), jamais le proxy du dashboard, qui répond 404 ; depuis Internet, le pare-feu à jour répond 404 avant tout relais (contrôle : `curl -s -o /dev/null -w '%{http_code}' -X POST https://<APP_HOST>/api/web-apps/_platform/recovery-events` → `404`) ;
   - le contrôle du chien de garde du contrôleur (toutes les 15 min, lecture de `runs`), filet de sécurité si l’envoi est désactivé ou perdu.
   **N’activer `failureEvents` qu’une fois la route déployée** : elle est implémentée localement (`HandleWebPlatformRecoveryEvent`, testée sur PocketBase jetable) mais pas encore déployée sur app1/app2/app3. Une version de service antérieure au correctif envoie encore l’ancien nom `platform_recovery_failed` : déployer le service à jour avant de l’activer. En attendant les deux, faire un contrôle quotidien : relancer la commande du point 4 avec `latest`, et surveiller l’espace libre de `<DATA_ROOT>` (réserve de 15 Gio).
8. Les objets S3 partiels d’une capture tuée sont balayés automatiquement (`sweepEnabled`). Pour un contrôle manuel, passer par la même enveloppe que §3.1 : `sudo -u` vide l’environnement, et sans les variables S3 `sweep` échoue (« Configuration S3 privée de reprise invalide ») avant même la liste.
   ```bash
   sudo systemd-run --uid=ubuntu --gid=ubuntu --wait --pipe --collect --quiet \
     -p EnvironmentFile=<ENV_REPRISE> -p UMask=0077 \
     /usr/bin/python3 /opt/pockethost-web/web_platform_recovery_service.py sweep --config <CFG>            # liste seulement
   # puis, après relecture : même commande avec --execute
   ```

Pour suspendre : mettre `executionEnabled: false` puis redémarrer l’unité, uniquement quand `active == null`.
- Service à jour (`grep -c skip_locked_occurrence /opt/pockethost-web/web_platform_recovery_service.py` ≥ 1) : un jour où l’exécution était verrouillée à 21:30 UTC est consommé. Lever le verrou plus tard dans la journée ne lance donc pas de capture en pleine journée ; la suivante a lieu le lendemain, sauf **Capturer maintenant** ou `runToday`.
- Version antérieure : lever le verrou **avant 21:30 UTC** uniquement. Après l’heure, le redémarrage met immédiatement une capture en file, et la préparation arrête brièvement chaque site à volume.

---

## 5. Perte réelle d’app1 : bascule vers un nouvel hôte

Ordre impératif : **rien n’est exposé publiquement (DNS) avant la fin de 5.11**.

### 5.1 Déclarer et isoler l’ancien serveur

- Consigner l’heure UTC de la décision.
- S’assurer que l’ancien app1 **ne peut pas revenir** : arrêt complet ou mode rescue OVH. Deux contrôleurs actifs se disputeraient le webhook GitHub, les préfixes S3, les certificats et les envois.
- Prévenir les utilisateurs : tous les jetons existants seront invalidés (rotation des `tokenKey` à la restauration). Il faudra se reconnecter : utilisateurs, clients API, comptes de service des instances. Le TOTP continue de fonctionner, puisque les clés `.pockethost-totp.key` sont restaurées.

### 5.2 Ce qu’il faut sur le poste opérateur (noms uniquement)

| Besoin | Source sur le poste | Clés JSON / contenu |
|---|---|---|
| Clé de reprise (64 hex), jamais dans la capture | `.secret/web-platform-app1-recovery.key` | fichier texte |
| Identifiants S3 du bucket | `.secret/user-FN6ZpWETHVpb.txt` | identifiant et secret d’accès |
| Endpoint, bucket, région, préfixe web | `.secret/web-backups-app1-recovery.json` | `endpoint`, `bucket`, `region`, `prefix` |
| `WEB_APPS_MASTER_KEY` | `.secret/web-backups-app1-recovery.json` | `controlEncryptionKey` |
| `WEB_BACKUPS_S3_IMAGE_KEY` | `.secret/web-backups-app1-recovery.json` | `imageEncryptionKey` |
| `WEB_APPS_WORKER_SECRET` | **pas d’escrow pour app1** | repris des `host-files` (`.env` et `web-apps.env`) ou regénéré (§5.9) |
| Plan privé (chemins) | `.secret/web-platform-app1-plan-<date>.json` (§1.4) | `hostPaths[].source/target` |

Le jeton Cloudflare et la configuration GitHub App ne sont pas sur le poste. Le jeton est dans le `.env` restauré, et la configuration GitHub App est chiffrée dans la base du contrôleur par la clé maître.

### 5.3 Provisionner le nouvel hôte (sans DNS ni démarrage)

Ubuntu 24.04 ou 26.04, utilisateur `ubuntu`, x86_64 de préférence (binaires capturés `linux_amd64`). Le disque doit valoir au moins 15 Gio + `budget.restoreBytes` + la marge des sites.

Cloner **la même révision que celle déployée sur app1 au moment de la capture**. Le projet capturé dans `host-files/` sert de référence : ne pas démarrer un contrôleur plus récent sur une base plus ancienne sans revue des migrations.

```bash
git clone -b self-host-install-fixes https://github.com/gg97430/pockethost.git /home/ubuntu/pockethost
cd /home/ubuntu/pockethost && git checkout <REVISION_APP1>
DOMAIN=monappli.re APP_HOST=app.monappli.re SERVER_IP=<NOUVELLE_IP> \
ADMIN_EMAIL=<EMAIL_ADMIN> ADMIN_PASSWORD='<temporaire, remplacé par la base restaurée>' \
CLOUDFLARE_DNS=0 AUTO_LETSENCRYPT=0 SELF_SIGNED_TLS=1 START_PM2=0 BOOTSTRAP_ADMIN_USER=0 \
bash script.sh
```

Sans jeton Cloudflare, aucun DNS n’est modifié. `START_PM2=0` ne lance rien, et `BOOTSTRAP_ADMIN_USER=0` ne crée pas de compte dans une base qui sera remplacée.

Ensuite :

- installer les extras Docker rootless ;
- vérifier `/etc/subuid` et `/etc/subgid` pour `pockethost-web` ;
- lancer `sudo bash packages/pockethost/scripts/setup-web-hosting.sh`, qui installe les unités worker et terminal **sans les démarrer** ;
- créer `/opt/pockethost-web` (root, 0755) et y copier les scripts de la même révision.

### 5.4 Déposer les clés (depuis le poste, sans affichage)

```bash
ssh ubuntu@<NOUVELLE_IP> 'umask 077; mkdir -p ~/recovery-secrets'
ssh ubuntu@<NOUVELLE_IP> 'umask 077; cat > ~/recovery-secrets/platform.key' < .secret/web-platform-app1-recovery.key
jq -r .controlEncryptionKey .secret/web-backups-app1-recovery.json | ssh ubuntu@<NOUVELLE_IP> 'umask 077; cat > ~/recovery-secrets/master.key'
jq -r .imageEncryptionKey   .secret/web-backups-app1-recovery.json | ssh ubuntu@<NOUVELLE_IP> 'umask 077; cat > ~/recovery-secrets/image.key'
```

Sur l’hôte, créer `~/recovery-secrets/s3.env` (0600) **avec un éditeur** :

```text
WEB_PLATFORM_S3_ENDPOINT=<endpoint>
WEB_PLATFORM_S3_BUCKET=<bucket>
WEB_PLATFORM_S3_REGION=<region>
WEB_PLATFORM_S3_ACCESS_KEY_ID=<…>
WEB_PLATFORM_S3_SECRET_ACCESS_KEY=<…>
WEB_PLATFORM_RECOVERY_PREFIX=app1/platform-recovery
```

### 5.5 Choisir la capture (`<STATE_DIR>` est perdu avec app1)

```bash
sudo systemd-run --uid=ubuntu --gid=ubuntu --wait --pipe --collect --quiet \
  -p EnvironmentFile=/home/ubuntu/recovery-secrets/s3.env -p UMask=0077 \
  /usr/bin/python3 -I -c '
import json, sys; sys.path.insert(0, "/opt/pockethost-web")
import web_platform_recovery as e
cfg = e.s3_settings(); cl = e.s3_client(cfg); out = []
for page in cl.get_paginator("list_objects_v2").paginate(Bucket=cfg["bucket"], Prefix=cfg["prefix"] + "/"):
    for o in page.get("Contents", []):
        if o["Key"].endswith("/commit.json"):
            c = json.loads(cl.get_object(Bucket=cfg["bucket"], Key=o["Key"])["Body"].read(65536))
            out.append({k: c.get(k) for k in ("backupId", "createdAt", "complete", "verified", "object")})
print(json.dumps(sorted(out, key=lambda v: v["createdAt"] or ""), indent=1))'
```

Prendre la plus récente avec `complete: true` et `verified: true`. De préférence, prendre celle dont l’exercice §3 est archivé (§7). Fixer `OBJ=<object>`.

### 5.6 Inspecter puis restaurer en zone de transit, même disque que `<DATA_ROOT>`

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ); T=/home/ubuntu/recovery/restore-${TS}; R=/home/ubuntu/recovery-reports/${TS}
mkdir -p -m 700 /home/ubuntu/recovery "$R"
for OP in inspect restore; do
  EXTRA=(); [ "$OP" = inspect ] && EXTRA=(--target "$T") || EXTRA=(--target "$T" --min-free-gib 15)
  sudo systemd-run --uid=ubuntu --gid=ubuntu --wait --pipe --collect --quiet \
    -p EnvironmentFile=/home/ubuntu/recovery-secrets/s3.env -p MemoryMax=4G -p UMask=0077 -p RuntimeMaxSec=12h \
    /usr/bin/python3 /opt/pockethost-web/web_platform_recovery.py "$OP" \
      --object "$OBJ" --key-file /home/ubuntu/recovery-secrets/platform.key \
      --with-host-files --with-native-archives "${EXTRA[@]}" > "$R/$OP.json" 2> "$R/$OP.progress.jsonl" || break
done
jq '{status, budget}' "$R/inspect.json"; jq '{status, paused, databasesRestored}' "$R/restore.json"
```

Fixer `<N>` à `(.databasesRestored|length)`. Installer d’abord **uniquement** les binaires capturés, pour que leurs empreintes soient identiques :

```bash
for f in "$T"/host-files/platform/pocketbase/*/linux_amd64/pocketbase; do
  v=$(basename "$(dirname "$(dirname "$f")")")
  install -D -o ubuntu -g ubuntu -m 0755 "$f" "<PB_ROOT>/$v/linux_amd64/pocketbase"
done
```

Lancer ensuite les contrôles 3.3, 3.4 et 3.5, **avant** §5.8. La preuve native reste recommandée avant toute mise en service. Pour `--runtime-root`, utiliser `<PB_ROOT>` du nouvel hôte : les binaires doivent avoir **la même empreinte** que ceux de la capture, d’où l’installation ci-dessus.

### 5.7 Vérifier les clés sans les afficher

```bash
python3 -I - "$T" <<'EOF'
import hashlib, pathlib, re, sys
root = pathlib.Path(sys.argv[1]) / 'host-files'; home = pathlib.Path.home() / 'recovery-secrets'
want = {'WEB_APPS_MASTER_KEY': 'master.key', 'WEB_BACKUPS_S3_IMAGE_KEY': 'image.key'}
h = lambda v: hashlib.sha256(v.strip().encode()).hexdigest()
for f in root.rglob('*.env*'):
    for line in f.read_text(errors='ignore').splitlines():
        m = re.match(r'^(WEB_APPS_MASTER_KEY|WEB_BACKUPS_S3_IMAGE_KEY|WEB_APPS_WORKER_SECRET)=(.*)$', line.strip())
        if m:
            name, value = m.groups(); ref = want.get(name)
            same = None if not ref else h(value.strip('\'"')) == h((home / ref).read_text())
            print(f.relative_to(root), name, 'present', 'escrowMatch=' + str(same))
EOF
```

`escrowMatch=False` sur la clé maître est **bloquant** : les réglages chiffrés, variables, snapshots et configurations d’alertes deviendraient illisibles. Dans ce cas, la valeur d’escrow l’emporte seulement après accord explicite de l’opérateur.

### 5.8 Lever la pause hors ligne, dans `$T`, avant installation

`web_platform_recovery_unpause.py` modifie la **copie restaurée** (`$T`), jamais une base utilisée par un PocketBase. Il remet les valeurs d’avant la pause, enregistrées par le moteur dans `recovery-pause-state.json`. Si ce fichier manque, la capture a été restaurée par un moteur plus ancien : il faut alors `--assume-enabled`, qui réactive tout.

Sans `--apply`, l’outil ne fait qu’un essai à blanc. Il n’affiche aucun secret, et `--audit` écrit un nouveau fichier en 0600.

**À faire après les sondes 3.4 et 3.5** : une fois la pause levée, `RECOVERY_PAUSED.json` passe à `paused: false` et la sonde refuse la copie.

Vague hors ligne recommandée :

```bash
U=/opt/pockethost-web/web_platform_recovery_unpause.py
# 1) Essai à blanc : liste par base les réglages qui seraient réactivés
python3 "$U" --root "$T" --native-s3 --native-smtp --native-backups --health --previews | jq .
# 2) Application : S3 natif d’abord (sinon les URL de fichiers cassent), puis SMTP, sauvegardes natives, santé, previews
python3 "$U" --root "$T" --native-s3 --native-smtp --native-backups --health --previews --apply --audit "$R/unpause-wave1.json"
# 3) Durcissement de la copie : révoquer les jetons CI et faire tourner les secrets de webhooks (GitHub, alertes)
python3 "$U" --root "$T" --databases controller --revoke-ci-tokens --rotate-webhooks   --master-key-file /home/ubuntu/recovery-secrets/master.key --secrets-out "$R/rotated-webhook-secrets.json"   --apply --audit "$R/unpause-hardening.json"
jq '{paused, unpause}' "$T/RECOVERY_PAUSED.json"
```

- `--databases controller,<id>,…` limite l’action à certaines bases. Par défaut, il agit sur toutes les bases restaurées.
- Consulter `warnings` dans la sortie : un état d’origine absent laisse la catégorie inchangée.
- `rotated-webhook-secrets.json` (0600) contient le nouveau `githubWebhookSecret`, les secrets de webhooks d’alerte par application et `gitWebhookSecrets` (un secret de 32 caractères ou plus par dépôt Git à clé de déploiement). Les reporter dans GitHub (§5.13), chez chaque hôte Git (réglages webhook du dépôt) et chez les destinataires des alertes, depuis un terminal privé et sans les consigner nulle part. **Supprimer ensuite le fichier.**
- `--disable-git-sources` coupe à la place les webhooks des dépôts Git à clé de déploiement (réponse 401), sans clé maître : à préférer si les secrets ne peuvent pas être reportés tout de suite. Les clés de déploiement ne sont jamais renouvelées hors ligne ; après un incident, utiliser « Régénérer la clé » dépôt par dépôt dans le dashboard, puis remplacer la clé publique chez l’hôte Git. La pause n’efface pas les secrets Git : elle les liste par hôte (`credentialsKept`), sans nom de dépôt.
- La preuve native (3.5) laisse les bases en mode WAL, sans fichiers `-wal`/`-shm`. L’outil à jour (`grep -c connect_copy /opt/pockethost-web/web_platform_recovery.py` ≥ 1) les ouvre en lecture seule logique (`query_only`) pour l’essai à blanc, et termine `--apply` par un point de contrôle WAL : `data.db` seul contient alors les modifications. Avec une version antérieure, l’essai à blanc échoue à tort sur « Base restaurée verrouillée ou illisible » : déployer l’outil à jour plutôt que de passer directement à `--apply`.
- **Ne pas lever** `--apps`, `--backups`, `--tasks` ni `--notifications` à ce stade : les images et volumes des sites n’existent pas encore sur l’hôte. Ces catégories reviennent en ligne, site par site, en §5.11. Seule exception : `--apps` remet les statuts d’origine et ne convient que si les images sont déjà présentes.

### 5.9 Installer les données dans `<DATA_ROOT>` (propriétaires et modes)

Rien ne doit tourner : `pm2 status` vide ou arrêté, `systemctl is-active pockethost-web-worker` doit répondre `inactive`.

```bash
D=/home/ubuntu/.local/share/pockethost/data
[ -e "$D/mothership/pb_data" ] && mv "$D/mothership/pb_data" "$D/mothership/pb_data.fresh-$TS"
mkdir -p -m 700 "$D/mothership" && mv "$T/controller/pb_data" "$D/mothership/pb_data"
for id in $(jq -r '.databasesRestored[] | select(. != "controller")' "$T/RECOVERY_PAUSED.json"); do
  mkdir -p "$D/instances/$id"; [ -e "$D/instances/$id/pb_data" ] && mv "$D/instances/$id/pb_data" "$D/instances/$id/pb_data.fresh-$TS"
  mv "$T/instances/$id/pb_data" "$D/instances/$id/pb_data"
done
sudo chown -R ubuntu:ubuntu "$D/mothership" "$D/instances"
find "$D" -name .pockethost-totp.key -exec stat -c '%a %U %n' {} +    # 600 ubuntu attendu
```

Pour les fichiers d’hôte `"$T"/host-files/<target>/…`, remettre chaque cible à sa source d’après la copie du plan (§1.4). La restauration écrit tout en 0600 au nom de `ubuntu`, il faut donc rétablir les droits :

| Cible | Propriétaire | Mode |
|---|---|---|
| `/home/ubuntu/pockethost/.env` | `ubuntu:ubuntu` | 0600 |
| `/home/pockethost-web/web-apps.env` | `pockethost-web:pockethost-web` | 0600 |
| `<PB_ROOT>/<ver>/linux_amd64/pocketbase` | `ubuntu:ubuntu` | **0755** |
| `ssl/tls.key` | `ubuntu` | 0600 |
| `ssl/tls.cert` | `ubuntu` | 0644 |
| Données web `/home/pockethost-web/data/{tls,acme}` | `pockethost-web` | 0750 / 0640 |
| Unités `/etc/systemd/system/*.service` | `root:root` | 0644, puis `systemctl daemon-reload` |
| Configuration du service de reprise, plan, mot de passe technique | `ubuntu` | 0600 ; `stateDir` en 0700 |

Corriger dans `.env` et `web-apps.env` les valeurs liées à l’IP : `WEB_APPS_PUBLIC_IP`, `PH_FTP_PASV_IP`. `WEB_APPS_WORKER_SECRET` peut être regénéré, à condition d’utiliser **la même valeur** côté contrôleur et côté worker. La clé maître et la clé d’images, elles, ne changent jamais.

### 5.10 Substitutions S3 et démarrage

- Recopier `WEB_BACKUPS_S3_PREFIX` depuis `"$T/recovery-env-overrides.json"` dans `web-apps.env` (worker) et dans l’environnement du service de reprise. Les lignes `web_app_backups` restaurées pointent déjà vers `<prefixe>/<backupId>/refs/web/...`.
- Garder ce préfixe tant que des sites sont restaurés depuis cette capture. Le retour au préfixe nominal `app1/web-app-backups` est une décision séparée : les anciennes lignes restaurées resteraient sur le préfixe de reprise.
- **Capture en lecture seule.** Les objets sous `<prefixe>/<captureId>/refs/web` appartiennent au manifeste de la capture. Un worker à jour (`grep -c capture_prefix /opt/pockethost-web/web_app_backup_s3.py` ≥ 1) reconnaît ce préfixe : la rétention et la suppression manuelle des sauvegardes et snapshots de volumes retirent la ligne et l’archive locale, **sans jamais supprimer d’objet S3**. Une seconde restauration depuis la même capture reste donc possible. Contrepartie : les nouvelles sauvegardes écrites sous ce préfixe ne sont plus purgées non plus, et l’espace S3 croît jusqu’au retour à un préfixe neuf. Avec un worker antérieur, **ne pas réactiver la planification des sauvegardes (§5.11) avant de l’avoir mis à jour** : la 4e sauvegarde prête d’un site supprimerait des objets de la capture.
- Démarrer le socle : `cd /home/ubuntu/pockethost && pm2 start ecosystem.config.cjs && pm2 save`.
- Contrôler en local `curl -fsS http://127.0.0.1:8091/api/health`, puis la connexion d’un opérateur au dashboard par l’IP ou un `hosts` local. Une reconnexion est attendue.
- Les instances tournent avec les réglages natifs réactivés en §5.8. Les sites web restent `stopped` jusqu’à §5.11.

### 5.11 Reprise en ligne, site par site

1. Démarrer le worker web : `sudo systemctl enable --now pockethost-web-worker.service pockethost-web-terminal.service`. Démarrer aussi le service de reprise avec `executionEnabled: false` jusqu’au retour à la normale.
2. Contrôler les réglages natifs réactivés. Pour le contrôleur : Admin → **SMTP des emails** → Tester SMTP (`srv.md`). Pour chaque instance, ouvrir la console admin → Settings, et vérifier Files storage (S3), Mail settings et Backups. Si un `warning` de §5.8 concernait une base, la régler ici à la main.
3. **Sites web**, pour chaque application :
   - onglet Sauvegardes → **Récupérer depuis S3** sur la dernière archive, puis **Restaurer** après l’aperçu ; l’image est rapatriée et authentifiée depuis S3 ;
   - onglet Stockage → restaurer le dernier snapshot de chaque volume ;
   - contrôler en local sur le port loopback, puis en HTTPS après §5.12.
4. Une fois le site servi, toujours par application :
   - onglet Sauvegardes → réactiver la planification ;
   - réactiver les tâches planifiées une par une ;
   - en dernier, l’onglet Alertes, après un envoi de test. L’enregistrement remet les curseurs à zéro, ce qui évite de rejouer l’historique.
   - La surveillance de santé est déjà réactivée (§5.8) : vérifier qu’elle passe à `healthy`.

### 5.12 DNS Cloudflare et TLS

- Cloudflare (zone `monappli.re`) : `A app.monappli.re → <NOUVELLE_IP>` (proxied), `A *.monappli.re → <NOUVELLE_IP>` (DNS only), `A ftp.monappli.re → <NOUVELLE_IP>` (DNS only). Ne pas toucher `app2.*` ni `app3.*`. SSL/TLS reste en `Full (strict)`.
- Domaines personnalisés des sites : si l’IP a changé, chaque propriétaire doit mettre à jour son A/AAAA. Une IP additionnelle OVH déplaçable éviterait cette étape lors du prochain sinistre.
- Certificat wildcard. Le compte ACME **n’est pas** dans la capture : les certificats restaurés restent valides jusqu’à leur échéance, mais le renouvellement exige un nouveau compte. Créer `/home/ubuntu/.local/share/pockethost/ssl/cloudflare-certbot.ini` (root, 0600) avec le jeton, sans l’afficher, puis :
  ```bash
  sudo certbot certonly --non-interactive --agree-tos --no-eff-email --email <EMAIL_ADMIN> \
    --dns-cloudflare --dns-cloudflare-credentials /home/ubuntu/.local/share/pockethost/ssl/cloudflare-certbot.ini \
    --dns-cloudflare-propagation-seconds 90 --cert-name app.monappli.re -d app.monappli.re -d '*.monappli.re'
  ```
  Remettre ensuite le hook `/etc/letsencrypt/renewal-hooks/deploy/pockethost-app.monappli.re.sh` (celui de `script.sh`, mode 0700) et l’exécuter une fois. Vérifier `curl -sI https://app.monappli.re`.
- Domaines web : onglet Domaines → **Vérifier** sur chaque domaine personnalisé, et contrôler que le worker réémet le certificat HTTP-01. Son compte ACME dédié est à recréer s’il manque. Faire un `certbot renew --dry-run` à blanc.

### 5.13 GitHub, jetons CI et rotations

- GitHub → Settings → Developer settings → GitHub Apps → `pockethost-app1` → Webhook.
  - Saisir le nouveau **Webhook secret**, soit la valeur `githubWebhookSecret` de `rotated-webhook-secrets.json` (§5.8), puis supprimer ce fichier.
  - L’URL `https://app.monappli.re/api/web-apps/github/webhook` est inchangée si `APP_HOST` l’est.
  - Dans *Recent Deliveries*, **Redeliver** un push récent : la réponse doit être 2xx. Dans le dashboard, Connexion GitHub → diagnostic au vert.
- Jetons CI de déploiement. La copie les gardait valides ; `--revoke-ci-tokens` (§5.8) les a révoqués. Pour chaque application, **recréer** un jeton dans l’onglet CI, puis mettre à jour les secrets GitHub Actions. Les jetons doivent apparaître comme révoqués dans l’historique.
- Destinataires des alertes webhook : leur transmettre le nouveau secret de leur application (même fichier), sinon ils rejetteront les signatures.
- Rotations recommandées :
  - `WEB_APPS_WORKER_SECRET`, côté contrôleur et worker ensemble ;
  - le mot de passe technique du service de reprise ;
  - le jeton Cloudflare, si le `.env` restauré a circulé ;
  - une nouvelle clé de reprise pour les captures futures, avec escrow mis à jour dans `.secret/`.
- Réinstaller le service de reprise sur le nouvel hôte avant §1 à §4 (le §1 suppose une unité existante) :
  - unité : `packages/pockethost/scripts/systemd/pockethost-platform-recovery.service` du dépôt → `/etc/systemd/system/` (root, 0644), en ajustant `User`, `EnvironmentFile` et `--config`, puis `systemctl daemon-reload` ; **ne pas** l’activer avant d’avoir écrit la configuration ;
  - environnement : modèle `packages/pockethost/scripts/web-platform-recovery.env.example` → `<ENV_REPRISE>` (0600, propriétaire de l’unité) avec les variables S3 de reprise et `WEB_PLATFORM_RECOVERY_PREFIX` ;
  - `config.json` (0600) d’après les clés documentées dans ce même modèle, `executionEnabled: false` au départ, `stateDir` neuf en 0700 ;
  - plan : le plan restauré dans `host-files/` s’il y figure, sinon la copie hors hôte du §1.4 (`.secret/web-platform-app1-plan-<date>.json`), puis `plan.py check`/`generate` contre le nouvel hôte ;
  - clé de reprise **neuve** (64 hex, escrow mis à jour) et mot de passe technique **neuf** (compte superuser régénéré), tous deux en 0600 ;
  - côté contrôleur : `WEB_PLATFORM_RECOVERY_STATE_DIR` (et `WEB_PLATFORM_RECOVERY_RUNNER` si le chemin diffère) dans le `.env` du projet, puis redémarrage du mothership, pour que `/applications/recovery` lise le nouvel état ;
  - ensuite seulement : §1 à §4 (pré-vol, capture, exercice, planification).

---

## 6. app2 et app3

Le service de reprise n’a été installé que sur app1. La version actuelle du dépôt accepte un `platform` quelconque `[a-z0-9-]`, `app1` par défaut. Le plan doit déclarer la même plateforme, et le préfixe par défaut devient `<platform>/platform-recovery`. La version **déployée** sur app1 refusait encore toute valeur autre que `app1` : déployer les scripts du dépôt avant d’installer le service sur app2/app3.

Le dépôt fournit le modèle d’unité `packages/pockethost/scripts/systemd/pockethost-platform-recovery.service` et le modèle d’environnement `packages/pockethost/scripts/web-platform-recovery.env.example` (variables S3, préfixe, et clés documentées de `config.json`). Les installer comme au §5.13, en adaptant les chemins. Côté contrôleur, ajouter `WEB_PLATFORM_RECOVERY_STATE_DIR` au `.env` du projet (non encore référencé par `.env-template`).

| | app2 | app3 |
|---|---|---|
| Hôte | `app2.monappli.re`, `91.134.89.55` | `app3.monappli.re`, `135.125.67.39`, Ubuntu 26.04, ~878 Gio |
| `platform` | `app2` | `app3` |
| `WEB_PLATFORM_RECOVERY_PREFIX` | `app2/platform-recovery` | `app3/platform-recovery` |
| `WEB_BACKUPS_S3_PREFIX` | `app2/web-app-backups` | `app3/web-app-backups` |
| Instances au dernier relevé | 11, soit `<N>` = 12 à recompter | 4, soit `<N>` = 5 à recompter |
| Escrow poste | `.secret/web-backups-app2-recovery.json` (`masterKey`, `imageKey`) | `.secret/web-backups-app3-recovery.json` (`masterKey`, `workerSecret`) |
| État | S3 web et GitHub non configurés, aucune reprise globale | S3 web et GitHub non configurés, aucune reprise globale ; Litestream actif pour avenirassur et avenirassur-dev |

Avant d’ouvrir la reprise sur ces hôtes :

1. Configurer le S3 web propre à l’hôte (`s3Configured` vaut aujourd’hui `false`) et le SMTP propre à l’hôte, puis vérifier SPF/DKIM.
2. Générer une **clé de reprise distincte par hôte** (64 hex), avec escrow `.secret/web-platform-<appX>-recovery.key`. Ne jamais réutiliser la clé d’app1.
3. Écrire un plan propre à l’hôte, sans chemins d’app1 : `web_platform_recovery_plan.py generate --data-root <DATA_ROOT> --platform app2 --output <plan>`, puis compléter `hostPaths`, en particulier `platform/pocketbase`. Reprendre ensuite les contrôles de 1.4.
4. app3 : les bases d’environ 20 Go et 13 Go imposent un budget de restauration d’au moins 15 Gio + ~35 Gio de bases, plus les fichiers. Une restauration complète prend plusieurs heures : relever `RuntimeMaxSec` des enveloppes `systemd-run` (§3.1, §3.2) et `captureTimeoutSeconds`, et lancer la preuve native avec `--max-seconds 14400` (par exemple) : avant tout démarrage, l’unité refait `integrity_check` et le sha256 de chaque base et de chaque fichier, ce qui dépasse les 30 min par défaut sur ces volumes. Pour ces deux instances, la restauration Litestream déjà prouvée reste une voie de secours indépendante.
5. Pour la bascule (§5), remplacer `DOMAIN` et `APP_HOST` par `app2.monappli.re` ou `app3.monappli.re`. Le wildcard devient `*.app2.monappli.re` ou `*.app3.monappli.re`. Les DNS concernés sont `app2`, `*.app2`, `ftp.app2` (et l’équivalent pour app3).

---

## 7. Preuves à archiver (`.secret/`, jamais de valeur secrète)

Dossier du poste : `.secret/web-platform-recovery-<appX>-<AAAAMMJJ>/`, en mode 0700. Il contient :

- [ ] `scripts.sha256` : sortie de `sha256sum -c` de §1.1, plus les empreintes déployées ;
- [ ] `preflight.json` : sortie de 1.4 (ids, `expectedDatabases`, versions, booléens) et `df` de 1.5 ;
- [ ] `capture.json` : `jq '{id,status,verified,trigger,object,databases:(.result.databases|length),coverage:.result.coverage,encryptedBytes:.result.encryptedBytes}'` du run, sans `privateEngineError` ;
- [ ] `inspect.json`, `restore.json` : sorties de 3.1 et 3.2. Les fichiers `*.progress.jsonl` sont facultatifs ;
- [ ] `RECOVERY_PAUSED.json` : copie du marqueur, qui ne contient pas de secret ;
- [ ] `verify-only.json`, `native.json`, `native-launcher.json` : rapports de la sonde et du lanceur ;
- [ ] `hold.json`, `drill-record.json` : réservation puis run `drill` qui protège la capture (3.6), avec `sourceRunId == RUN`, `nativeProof: true` ;
- [ ] `cleanup.txt` : preuve de `rm -rf $T`, aucun processus restant, `df` après nettoyage ;
- [ ] `schedule.json` : `settings` et `capabilities` après activation (§4), puis le premier run `scheduled` du lendemain ;
- [ ] pour une bascule réelle (§5) :
  - [ ] l’heure de décision et la capture choisie (`backupId`, `object`) ;
  - [ ] `escrowMatch` de 5.7 ;
  - [ ] les audits `unpause-wave1.json` et `unpause-hardening.json` (§5.8), puis les catégories reprises en ligne (§5.11) avec leur heure ; ne pas archiver `rotated-webhook-secrets.json` ;
  - [ ] les contrôles HTTPS 200 du dashboard, d’une instance et de chaque site ;
  - [ ] la redelivery GitHub en 2xx ;
  - [ ] les jetons CI révoqués et recréés (identifiants seulement) ;
  - [ ] les rotations faites ;
- [ ] mettre à jour `MEMORY.md` et `WEB_HOSTING.md` (section « Reprise globale ») avec le résultat et les identifiants de run, sans valeurs.

**Critère « reprise validée »** : une capture `ready` et complète, `restore.json` avec `databasesRestored` de taille `<N>`, `verify-only.json` à `passed`, `native-launcher.json` à `passed` avec `nativeHealthy == <N>`, le nettoyage effectué et la capture protégée par un run d’exercice. Ces six éléments doivent porter sur le même `backupId`.
