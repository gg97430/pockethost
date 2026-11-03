# Reprise isolée d’un projet Kolab

Cette procédure concerne la plateforme app3/app4/app5. Les applications publiées et les bases d’origine restent en service. La publication du logiciel se valide sur app4 et app5 avant app3 ; aucun redémarrage des processus clients n’est nécessaire.

## Ce que prouve l’essai

Une capsule chiffrée PHPR contient l’inventaire sélectionné du projet, ses configurations/environnements publiés, les commandes de ses crons, les images Docker immuables, les volumes locaux nommés, ses liaisons PocketBase déclarées, les snapshots SQLite et fichiers natifs, les clés TOTP nécessaires et les supports hooks/public/migrations. L’inventaire synthétique de contrôleur n’inclut aucun compte, projet, clé globale de plateforme ou historique d’un autre projet.

`completeArtifacts=true` signifie que ces artefacts sont reconstruits et contrôlés. Ce n’est pas une validation de tous les parcours métier. La recette de démarrage vérifie uniquement les frontaux par HTTP et leur processus, avec les bases copiées à leur port de liaison. `applicationVerified` reste faux. Les hooks et migrations natifs sauvegardés ne sont pas exécutés ; une application dont la santé dépend de ces hooks peut donc nécessiter une recette supplémentaire. Les workers sont archivés mais ne démarrent pas pendant l’essai.

Les connexions BDD connues mais non liées et les variables de stockage externe R2/S3 déclenchent un blocage de couverture, au lieu d’un résultat complet trompeur. Le stockage natif PocketBase S3 suit les contrôles conditionnels et de contenu du moteur de reprise existant. Un service externe arbitraire ou une dépendance inscrite dans le code du projet ne peut pas être découvert automatiquement : la revue du périmètre demeure nécessaire.

Chaque base bénéficie d’un snapshot SQLite cohérent. Les fichiers sont hachés et comparés avant/après la capture ; une modification interdit la capsule. Une transaction distribuée entre plusieurs bases n’est pas revendiquée. Une migration avec bascule des écritures exige donc une barrière source spécifique, revue séparément.

Les historiques de sauvegarde et `logs.db` ne sont pas nécessaires à la reconstruction courante et sont exclus explicitement. Les fichiers, autorisations ordinaires et répertoires vides des volumes sont conservés ; liens symboliques et physiques non maîtrisés sont refusés. Aucun SQL, hook ou commande de projet ne s’exécute sur l’hôte pendant une reconstruction.

## Séparation des moteurs

- Docker hébergement UID1001, `/run/user/1001/docker.sock` : lecture d’inventaire et export d’images du projet choisi. Aucun arrêt/pause/redémarrage de source.
- Docker de recette **UID1002**, compte `kolab-recovery`, `/run/user/1002/docker.sock` : copies uniquement. Répertoire personnel et répertoire du socket en mode0700. UID1001 ne doit pas pouvoir accéder à ce socket ni aux copies de bases.
- Le coordinateur root lit les seules sources sélectionnées et envoie les copies au moteur1002. Il utilise une clé d’API dédiée, jamais la clé du worker1001. Il ne charge aucun fichier d’environnement détenu par UID1001.

Le moteur1002 a une slice systemd CPU100%, RAM4Gio, swap0, 512tâches. Les copies sont exécutées sans privilèges, utilisateur10001, système de fichiers racine en lecture seule, capacités supprimées, sans port publié, sans montage d’une source ni de `docker.sock`. Une ancre `network=none` est partagée uniquement entre les copies d’une même application. Les identifiants d’envoi/stockage externes sont vidés, e-mails et crons sont désactivés. Les applications du projet sont répétées séquentiellement pour borner la pointe mémoire.

L’admission conserve la réserve hôte (15% / 2Gio minimum), les engagements mémoire des bases, une pointe de4Gio pour le moteur1002 et1Gio pour le coordinateur. Elle est revérifiée avant chaque clone. Le verrou root `/run/kolab-project-recovery/drill.lock` empêche deux recettes simultanées, y compris depuis la CLI. La réserve disque de15Gio est contrôlée pendant la copie, l’export, la reconstruction et la matérialisation Docker ; le budget d’artefacts agrégé est32Gio.

Les snapshots des bases d’origine sont lus par un sous-processus exécuté avec le propriétaire du fichier SQLite, sans capacité effective ou ambiante après le changement d’identité. Le scratch privé est créé sur le disque dans `/var/tmp`, avec réserve et taille contrôlées pendant la copie ; `/tmp` peut être un tmpfs trop petit pour une grosse base. Le coordinateur root ne crée donc pas de fichiers WAL/SHM root dans les données natives. Aucun chmod/chown de données d’origine n’est effectué.

Les chemins de volumes ne sont jamais déduits librement d’une réponse du moteur1001 : seule la racine locale fixe `/home/pockethost-web/.local/share/docker/volumes/<nom>/_data`, sans lien symbolique et avec le propriétaire attendu, est admise. Un point de montage rapporté vers un autre répertoire est refusé avant toute lecture.

## Installation d’un serveur neuf

Les commandes sont des commandes opérateur root, avec chemins locaux réels. Ne pas reprendre les clés, états, utilisateurs ou politiques d’un autre serveur.

1. Installer les prérequis rootless usuels (Docker, rootlesskit, subordinate IDs, cgroupv2). Le profil UID1001 doit déjà fonctionner. Vérifier l’image auxiliaire approuvée par son ID SHA256 exact.
2. Exécuter d’abord le plan, puis la création du **nouveau** moteur :

```bash
sudo python3 packages/pockethost/scripts/install-project-recovery-engine.py \
  --helper-image sha256:<ID_APPROUVE>
sudo python3 packages/pockethost/scripts/install-project-recovery-engine.py \
  --helper-image sha256:<ID_APPROUVE> --execute
```

L’installateur refuse un UID/GID1002 appartenant à un autre compte, tout sous-UID/GID chevauchant et toute home étrangère. Il conserve les plages existantes. Il ne redémarre ni Docker natif, ni le moteur1001, ni PM2. Il démarre uniquement le gestionnaire du nouvel utilisateur et son nouveau Docker. L’image auxiliaire est exportée du moteur1001 puis importée et vérifiée dans1002 ; aucune clé ou variable métier ne transite dans cet amorçage.

Le plan affiche `rootlessSetupExecutable`. L’installateur cherche les outils uniquement dans les répertoires système approuvés et vérifie leurs propriétaires/permissions, y compris les cibles de liens. Cela couvre le paquet Ubuntu qui expose `dockerd-rootless-setuptool.sh` par `/usr/local/bin` vers `/usr/share/docker.io/contrib`, ainsi que l’installation Docker qui le place dans `/usr/bin`. Aucun chemin n’est repris du `PATH` de l’opérateur. En cas d’échec, `stage` indique la phase ; les diagnostics bornés concernent uniquement l’amorçage du compte1002 sans identifiants. Reprendre l’installateur après correction conserve le compte et les sous-IDs déjà créés. Une livraison commune repasse toujours par les pilotes app4/app5 avant app3.

3. Publier la migration additive `1793700600_project_recovery.js`, le module API compilé et les scripts cohérents. Installer le coordinateur après la disponibilité des routes natives :

```bash
sudo python3 packages/pockethost/scripts/install-project-recovery.py \
  --data-root /home/ubuntu/.local/share/pockethost/data \
  --binary-root /home/ubuntu/.local/share/pockethost/pocketbase \
  --instance-app-root <RUNTIME_ROOT>/packages/pockethost/src/instance-app \
  --helper-image sha256:<ID_APPROUVE> --activate
```

L’installateur crée ses propres clés. `/etc/kolab/project-recovery-controller.key` est root:ubuntu0640, nécessaire uniquement au contrôleur et au coordinateur. `/etc/kolab/project-recovery.key` est root0600, utilisée uniquement pour les capsules éphémères de recette. Ces deux clés sont différentes et ne doivent pas être transférées entre serveurs. La configuration root600 est `/etc/kolab/project-recovery.json` ; les données temporaires privées sont `/var/lib/kolab-project-recovery` root0700.

Le fichier d’unité est `scripts/systemd/pockethost-project-recovery.service`. Les seules nouvelles admissions sont suspendues pendant une maintenance de plateforme ; les rapports et le nettoyage d’un essai actif continuent. Le contrôleur et le coordinateur doivent attendre la fin d’une recette avant leur remplacement.

## Surveillance et comptabilité

Le coordinateur publie `/run/kolab-project-recovery/health.json` root:ubuntu0640, parent0755, toutes les15secondes : date, disponibilité, blocage, disponibilité du moteur1002 et protection du socket. Aucune ID de client ni clé n’y figure. Une ancienne date est un état indisponible, même si le dernier `ready` valait vrai. Le marqueur privé `status.json.runId` sert à empêcher une publication pendant une recette/finalisation.

Configurer le collecteur de ressources après la création du moteur, sans ajouter un second endpoint Docker rootless à son inventaire de conteneurs :

```json
{
  "services": [
    {"id":"backups:project-sandbox","category":"backups","cgroup":"user.slice/user-1002.slice"},
    {"id":"backups:project-recovery","category":"backups","cgroup":"system.slice/pockethost-project-recovery.service"}
  ],
  "diskRoots": [
    {"path":"/home/kolab-recovery/.local/share/docker","category":"backups","resourceId":"backups:project-sandbox"},
    {"path":"/var/lib/kolab-project-recovery","category":"backups","resourceId":"backups:project-recovery"}
  ]
}
```

Il faut la validation explicite de la slice1002 dans le collecteur. Compter seulement son `docker.service` oublierait les scopes de conteneurs, qui sont des frères dans `app.slice`. Ne pas ajouter aussi leurs mesures individuelles : cela compterait deux fois la mémoire/CPU. Les quotas UID1001 ne changent pas.

## Échec, nettoyage et sauvegarde de la plateforme

Les conteneurs, volumes et nouvelles images de recette portent un journal root propre à l’essai. Le nettoyage revalide leurs identités et retire uniquement ces objets. Une image déjà présente avant l’essai, notamment l’outillage, est conservée. Les nouvelles images importées sont retirées sans `--force` ; une image utilisée bloque le nettoyage plutôt que de supprimer son utilisateur.

Si le nettoyage échoue, les copies et le journal restent privés. Le résultat porte `cleanup_incomplete`, la santé indique un blocage et aucune nouvelle recette n’est exécutée avant réconciliation. Ne pas effacer le journal pour faire passer l’indicateur au vert. Le redémarrage du seul coordinateur réconcilie les journaux laissés par une interruption, sans toucher les sources.

Après acquisition du verrou exclusif et nettoyage prouvé, le coordinateur solde les anciens baux en cours avec `worker_lost` avant de reprendre la file. Un rapport dont l’accusé de réception a été perdu suit la même réconciliation ; il ne bloque pas le projet pendant la durée maximale du bail. L’API relit elle-même le verrou de maintenance sous transaction, même si une requête d’admission était déjà en vol.

Les captures et copies de recette sont supprimées après chaque essai. Le moteur1002 ne contient que de l’état recréable ; ne pas sauvegarder ses copies temporaires ni synchroniser sa home vers un autre serveur. La reprise de plateforme doit conserver l’installateur, l’unité et les **fichiers root de configuration/clés** propres à l’hôte, plus ses plages `/etc/subuid`/`subgid` et l’identité1002. L’outillage et le moteur se reconstruisent sans identifiants métier.

Le compte de sauvegarde ne reçoit pas la lecture des clés root. Après installation ou changement des clés, root scelle la configuration et les petits journaux de migration avec une clé dérivée, dédiée à cet usage, de la clé de reprise externe de **ce même hôte** :

```bash
sudo python3 -I /opt/pockethost-web/platform_project_recovery_export.py seal \
  --config /chemin/prive/configuration-reprise-globale.json --server app5
```

Inclure `/var/lib/kolab-project-tools-recovery/configuration.json.enc` dans le plan de sauvegarde et conserver séparément la clé externe. Le répertoire d’export est root:ubuntu0750, le seul chiffré est root:ubuntu0640 ; aucune permission des sources ne change. App5 peut utiliser l’ancien nom `/home/ubuntu/kolab-secrets/recovery-config.json`. Sur un hôte sans reprise globale, préparer une clé externe et une configuration minimale privée contenant `keyFile`, puis conserver l’export hors du serveur.

La commande `unseal --source ... --key-file ... --server app5 --destination ...` authentifie tout l’inventaire avant d’écrire un **nouveau répertoire de quarantaine**, privé. Elle n’installe aucune clé et ne reprend aucune opération. Après examen, restaurer uniquement sur l’hôte concerné, reconstruire le moteur1002 depuis son installateur et réconcilier les journaux avant de réactiver le coordinateur. Les capsules de transfert et leurs clés temporaires sont des artefacts opérateur distincts : elles ne sont pas incluses dans cet export.

## Recettes opérateur

```bash
python3 packages/pockethost/scripts/test-web-project-recovery.py
python3 packages/pockethost/scripts/test-project-recovery-engine-install.py
```

Le test API exige une compilation isolée exportant `projectRecovery.ts` et un binaire PocketBase jetable. Il vérifie TOTP, droits, clé dédiée, chiffrement, révision, bail, inventaire complet et refus d’une fausse réussite. Le test Docker est opt-in root, source entièrement synthétique, et permet capture sur app4 puis import isolé sur app5 avec une clé de transfert éphémère. Ne jamais transférer une clé de service/reprise globale pour cet essai. Les flux migration réels utilisent leur assistant et un plan distinct ; une recette HTTP ne constitue pas une autorisation de changer les DNS.
