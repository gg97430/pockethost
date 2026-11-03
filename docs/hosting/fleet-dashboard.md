# Vue des trois serveurs

La page `/fleet` réunit l’inventaire et les mesures d’app3, app4 et app5. Elle n’exécute aucune commande d’application, ne démarre pas Docker et ne réveille pas les bases. Les liens vers une ressource ouvrent le tableau de bord du serveur concerné avec sa propre connexion.

L’ouverture directe et le rechargement de `/fleet` et `/fleet/` doivent répondre HTTP 200 avec `Cache-Control: no-store`. Le serveur statique reconnaît explicitement ces chemins ; les chemins inconnus restent en 404.

## Accès

L’API `GET /api/admin/fleet` exige une session native `users`, le TOTP obligatoire, un compte superadmin actif et une adresse e-mail vérifiée. Le corps et les paramètres HTTP ne permettent pas de choisir une autre identité.

Chaque serveur distant vérifie à nouveau dans sa propre base de contrôle que cette même adresse e-mail correspond à un superadmin vérifié, non suspendu. Aucun compte, permission, mot de passe ou jeton n’est créé ou copié entre les serveurs. Si les adresses diffèrent ou que le rôle manque, seule la mention « Accès distant refusé » apparaît pour cette cible.

Le contrôleur conserve `NoNewPrivileges` et appelle le mode non privilégié `platform_fleet_reader.py request`. Ce client utilise uniquement `/run/kolab-fleet-reader.sock`, un socket root:ubuntu 0660, avec vérification des identités par le noyau. Le service systemd accepte seulement l’UID 1000 et une requête de lecture version/adresse e-mail, bornée à 4 Kio. Chaque connexion possède son propre processus root borné à 15 secondes, sans commande ni hôte arbitraire. Les clés SSH restent root 0600. Le lecteur abandonne temporairement root pour SQLite, puis réutilise les seules clés de lecture des pairs fixes. Aucun `sudo` n’est exécuté par le contrôleur.

La confiance entre serveurs est limitée à cette lecture. Un serveur source compromis avec accès root pourrait demander la lecture au nom d’un superadmin existant sur la cible. Il ne reçoit pas d’accès aux clés, environnements, configurations, données métier ou commandes des projets. Révoquer la clé publique correspondante retire cette confiance.

## Installation sans arrêt

1. Livrer le helper `platform_fleet_reader.py` à `/opt/pockethost-web/`, appartenant à `root:root`, non modifiable par les autres utilisateurs. Livrer le handler natif, sa route et l’interface avec la procédure de flotte habituelle. Valider app4 et app5 avant app3.
   Livrer aussi `pockethost-fleet-reader.socket` et `pockethost-fleet-reader@.service` depuis `scripts/systemd` dans `/etc/systemd/system/` (root:root 0644). `prepare` active le socket ; une installation existante peut utiliser `install-platform-fleet-reader.py socket` sans régénérer les clés ni modifier les pairs. Le socket est un service requis dans le manifeste de flotte ; les instances de service se terminent après chaque lecture.
2. Sur chaque serveur, via la session SSH d’administration déjà authentifiée, exécuter en root :

   ```sh
   python3 /chemin/du/repo/packages/pockethost/scripts/install-platform-fleet-reader.py prepare app4
   ```

   Adapter uniquement `app4` à l’identité locale. L’installateur vérifie cette identité dans `/var/lib/kolab-platform-release.json`. Il crée le compte système sans mot de passe utilisable `kolab-fleet`, les dossiers publics de lecture SSH et une paire Ed25519 dédiée pour chaque destination. Les clés privées restent exclusivement sur leur hôte source dans `/etc/kolab/fleet/keys/`, droits 0600 sous répertoire 0700.

3. Conserver le JSON de sortie de chaque hôte : il ne contient que l’identité, la clé publique SSH de l’hôte et les clés publiques de lecture destinées aux pairs. Les relever par la session d’administration existante, jamais par `ssh-keyscan` non authentifié.
4. Préparer pour chaque hôte le document suivant. Pour app4, `publicKey` est la clé de sortie **app5 vers app4**, donc `keys.app4` dans le JSON renvoyé par app5. `hostKey` est `hostKey` de ce même JSON app5 :

   ```json
   {
     "version": 1,
     "self": "app4",
     "peers": [
       {"key": "app5", "publicKey": "ssh-ed25519 …", "hostKey": "ssh-ed25519 …"}
     ]
   }
   ```

   Pour la flotte complète, chaque document comporte les deux autres hôtes. Pour la recette pilote, commencer par app4 ↔ app5 ; ajouter les liens avec app3 après validation des deux pilotes.
5. Envoyer ce document sur l’entrée standard de la commande root :

   ```sh
   python3 /chemin/du/repo/packages/pockethost/scripts/install-platform-fleet-reader.py connect < peer-public-material.json
   ```

   Cette opération met à jour uniquement son bloc de clés autorisées, les clés d’hôte épinglées et la configuration locale. Elle n’arrête ni ne redémarre aucun service. Les clés publiques autorisées sont contraintes par l’IP source fixe, `restrict` et une commande forcée unique. Le compte système n’a pas accès aux fichiers de la base de contrôle : sudo autorise seulement le lecteur root, sans commande, requête SQL ou chemin arbitraire fourni par l’appelant. Le helper abandonne temporairement root pour lire SQLite et les caches sous l’UID/GID 1000 du contrôleur, avant de lancer les lectures SSH. Ainsi, les éventuels fichiers de coordination WAL restent au bon propriétaire. Ne pas changer les droits de `data.db` pour ce besoin.
6. Vérifier la page avec un compte autorisé sur les deux pilotes, puis étendre la confiance à app3 et vérifier les trois hôtes. Tester aussi un compte sans rôle distant : les autres lignes doivent rester visibles, la cible doit refuser l’accès. Terminer par le contrôle commun `platform-fleet.py check`.
   La recette native `test-platform-fleet-api.py --source SOURCE --pocketbase BINAIRE --helper-real --no-new-privileges` utilise uniquement un contrôleur et des comptes jetables avec le vrai TOTP. Elle doit réussir sous `NoNewPrivileges`, sans compte de test ajouté au contrôleur réel.

## Fichiers locaux à sauvegarder

- `/etc/kolab/fleet/config.json`, `/etc/kolab/fleet/known_hosts` et `/etc/kolab/fleet/keys/` : privés, root 0600/0700.
- `/var/lib/kolab-fleet-reader/.ssh/authorized_keys` : clés publiques, fichier root 0644 et dossiers root 0755 pour que `sshd` puisse les lire sous l’UID dédié. Ce compte ne doit jamais pouvoir les modifier.
- `/etc/sudoers.d/kolab-fleet-reader` : root 0440, syntaxe vérifiée par `visudo` avant installation.

Les sauvegardes de ces fichiers restent séparées par serveur. Aucune clé privée de source ne doit être installée sur une cible. En cas de changement de clé d’hôte, la connexion échoue jusqu’à une nouvelle vérification administrative et mise à jour explicite du matériel public.

Le service de sauvegarde global tourne sans root et ne doit pas recevoir la lecture des clés SSH. Le helper `platform_fleet_recovery.py seal` produit à la place `/var/lib/kolab-fleet-reader-recovery/trust.json.enc`, chiffré et authentifié avec une sous-clé dédiée issue de la clé de reprise propre à cet hôte. Seul ce fichier chiffré est ajouté au plan global ; les clés actives restent root 0600. Exécuter de nouveau ce scellement après chaque changement de confiance. Si la reprise globale n’est pas configurée, fournir `--config` avec un fichier privé contenant `keyFile` (clé aléatoire séparée de 64 caractères hexadécimaux) et conserver hors serveur une copie privée du fichier chiffré et de cette clé. Un fichier uniquement présent sur le serveur n’est pas une sauvegarde externe.

La commande suivante déchiffre dans un **nouveau dossier de quarantaine**, sans installer les clés ni modifier la confiance en service :

```sh
python3 platform_fleet_recovery.py unseal --source trust.json.enc --key-file cle-reprise.key --server app4 --destination /chemin/prive/confiance-restauree
```

Vérifier l’identité du serveur, les IP et clés d’hôte avant réinstallation administrative. Le dossier restauré est privé ; le fichier actif `authorized_keys` devra retrouver ses permissions publiques root 0644 pour être lisible par sshd sous l’UID du compte dédié. Aucun rétablissement automatique de confiance n’est déclenché lors d’un essai de reprise.

## Interprétation

Les totaux concernent uniquement les serveurs accessibles à l’utilisateur. Une cible non autorisée, non configurée ou impossible à lire n’est jamais présentée comme un serveur en panne.

Le CPU correspond au pourcentage de l’hôte entier. Les mesures RAM/disque et les contrôles de santé gardent leurs horodatages ; ils deviennent explicitement anciens au-delà de 60 secondes et trois minutes respectivement. Les états des lignes de l’inventaire sont ceux déclarés par les contrôleurs, distincts des sondes de santé. Les compteurs d’incidents sont recalculés depuis les contrôles et ne font pas confiance à un résumé vert enregistré.

L’inventaire est borné à 500 ressources par serveur. Si un hôte dépasse ce nombre, les totaux restent complets, la troncature est signalée et le reste demeure consultable sur le serveur concerné. Aucun historique de logs, adresse de destinataire, URL arbitraire ou secret n’est inclus.

## Révocation et retour arrière

Retirer la clé du pair dans le bloc géré de `authorized_keys`, ou refaire `connect` sans ce pair. Une liste `peers` vide révoque les deux lectures distantes. Les sites, bases et applications restent en service. Revenir au code antérieur du tableau de bord peut masquer cette vue sans toucher aux données métier ; supprimer ensuite les autorisations de lecture si le lot est abandonné.
