# RustDesk OSS sur app4

Serveur préparé le 1 novembre 2026 depuis le service Coolify `os0koooc0kckc8s80gks44g0` du projet RUSTDESKSRV, hôte source `147.135.212.215`. L’ancien serveur reste actif : la migration des clients n’est pas encore confirmée.

## Accès client

- Serveur ID : `51.255.69.154` (21116).
- Relais : `51.255.69.154:21117`.
- Clé publique identique à l’ancien serveur ; paramètres complets disponibles localement dans `.secret/rustdesk-app4-20261101/configuration-client.txt`.
- Configurer les deux postes d’une session. Aucun client n’a été reconfiguré automatiquement.
- Le réglage enregistré sur le Mac de l’opérateur pointe vers un serveur public RustDesk et ne contient pas de serveur personnalisé ; ne pas en déduire la configuration des autres postes.

Documentation : <https://rustdesk.com/docs/en/self-host/client-configuration/>.

## Exploitation

Ce service TCP/UDP est géré séparément des applications web du dashboard.

Hôte : `ubuntu@51.255.69.154`. Unité : `kolab-rustdesk.service`. Conteneurs du Docker système : `kolab-rustdesk-hbbs` et `kolab-rustdesk-hbbr`. Données : `/var/lib/kolab-rustdesk/data`. Image épinglée : `rustdesk/rustdesk-server@sha256:680f8ba5accafc264d15076f33a6fdb9cb6f4d963a0fc92e01023ca0e919cc83`, couches identiques à la source, sans mise à niveau de version.

Les conteneurs utilisent UID/GID 10001, une racine en lecture seule, aucune capacité Linux, 256 MiB et 1 CPU chacun. La base SQLite a été reprise avec l’API de sauvegarde SQLite, puis vérifiée ; les empreintes des deux fichiers de clés sont identiques à la source. Les sauvegardes de migration et rapports sont privés ; ne pas publier la clé privée.

Le réseau dédié `kolab-rustdesk` conserve les IP réelles des clients. Le Docker rootless existant utilise RootlessKit 2.3.6, dont le transfert de ports masquait l’IP ; cette préparation a été remplacée, ses deux conteneurs supprimés et ses fichiers déplacés sous `/var/lib/kolab-rustdesk/migration-rootless-preparation`, inaccessible aux utilisateurs du moteur web.

TCP publics 21115–21119 et UDP 21116. `/opt/kolab-rustdesk/ingress.py` ajoute six exceptions strictes à la chaîne `KOLAB-DOCKER-IN`, limitées à l’interface publique, au réseau RustDesk, aux IP des conteneurs, à l’IP publique et aux ports d’origine. Le refus général des autres entrées Docker reste présent. L’unité applique les exceptions après le démarrage et les retire à l’arrêt ; elle suit les redémarrages Docker. Ne pas supprimer le garde réseau ni ouvrir globalement le forwarding. Après une réapplication manuelle du garde, relancer uniquement le script d’exception pour restaurer l’accès à RustDesk.

Commandes sur app4 :

```sh
sudo systemctl status kolab-rustdesk.service
sudo docker logs --tail 50 kolab-rustdesk-hbbs
sudo docker logs --tail 50 kolab-rustdesk-hbbr
sudo python3 /opt/kolab-rustdesk/ingress.py
```

Ne pas publier les journaux sans les vérifier : ils peuvent contenir des identifiants de postes et des clés publiques.

## Validation et suite

Les deux processus tournent sans redémarrage, les cinq ports TCP sont joignables depuis l’extérieur, UDP 21116 est publié et écouté, l’IP source a été vérifiée sur une connexion externe. Clés inchangées, intégrité SQLite valide et unité activée au démarrage. Aucune session réelle entre deux clients RustDesk ni redémarrage de l’hôte n’a été testé.

Configurer et valider deux clients avant de retirer l’ancien serveur. Si d’autres postes utilisent l’ancienne IP ou un domaine encore inconnu, les inventorier et mettre à jour leur configuration ou le DNS approprié avant l’arrêt. Ne pas arrêter l’ancien serveur sur la seule base des vérifications de ports.
