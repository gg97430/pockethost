# Réglages RAM et pause des nouvelles opérations

Dans Supervision, les réglages de RAM et la pause/reprise des nouvelles opérations utilisent les routes `/api/admin/controls`, `/memory` et `/maintenance`. Elles exigent une session `users` superadmin active et le premier code TOTP validé. Les comptes natifs `_superusers` n’ouvrent pas ces routes.

Le contrôleur conserve ses protections, dont `NoNewPrivileges` lorsqu’il est activé. Il lance seulement le client Python non privilégié `platform_operator_socket.py request ACTION JSON`. Ce client contacte `/run/kolab-platform-operator.sock`, root:ubuntu 0660, et vérifie que le serveur appartient à root. Le service vérifie l’UID 1000 du client avec les informations du noyau ; les clés supplémentaires et les actions autres que `status`, `memory`, `pause` et `resume` sont refusées. Aucun hôte, chemin, script, shell ou commande de conteneur ne peut être choisi par une requête.

Chaque requête possède son propre processus root : `NoNewPrivileges`, système de fichiers protégé, capacités SETUID/SETGID/DAC_READ_SEARCH bornées et explicites, 320 Mio sans swap, un demi-cœur, 12 tâches. Les lectures SQLite utilisent l’UID 1000 pour préserver les propriétaires du WAL. Les seules écritures permises par le protocole restent les politiques de RAM, les mises à jour de mémoire Docker déjà validées et le bail de pause. Aucun arrêt, démarrage ou recréation de conteneur n’est disponible. Le délai d’exécution est borné, avec une exception avant l’arrêt forcé du service.

Les contrôles existants restent appliqués : révision de formulaire, réserve physique du serveur, capacité libre, marge au-dessus de la consommation vive, identité du conteneur, sauvegarde/restauration active et verrou de migration. L’allocation et les modifications manuelles partagent le même verrou. Une pause laisse les opérations actives terminer et conserve les nouvelles demandes en file ; seule la pause possédée par l’opérateur peut être reprise.

## Installation

`scripts/install-memory-allocation.py` installe le client et active le socket lors d’une nouvelle installation. Sur un serveur existant, livrer les modules root à `/opt/pockethost-web`, les deux fichiers `pockethost-platform-operator.socket` et `pockethost-platform-operator@.service` dans `/etc/systemd/system`, puis exécuter en root `install-platform-operator.py`. Ce programme ne change aucune politique, base ou clé et ne redémarre aucun client. Ajouter le socket aux services requis du manifeste de flotte. Livrer ensuite les hooks régénérés depuis TypeScript et recharger uniquement le contrôleur selon les vagues app4/app5 → app3.

## Réception sans modifier les clients

Sur chaque pilote, `test-platform-operator-api.py --source SOURCE --pocketbase BINAIRE --isolated` démarre un contrôleur jetable UID1000 sous `NoNewPrivileges`, valide le TOTP et les refus de droits, puis utilise un socket et des politiques de recette séparés. Seul un conteneur Docker jetable portant un label unique change de RAM : réservation fixe, retour automatique, même PID et même date de démarrage. La recette couvre la révision périmée, le verrou de migration, l’audit et la pause/reprise isolée. Elle vérifie que les politiques et le bail réels restent identiques et nettoie toutes ses ressources.

Après activation, `--installed-read-only` vérifie le vrai lecteur installé avec un contrôleur/TOTP jetables. Les essais d’écriture doivent être refusés : instance absente de la vraie base, révision périmée ou paramètres invalides. Aucun compte de recette n’est créé dans le contrôleur réel et aucun réglage client n’est changé. Terminer par le contrôle commun `platform-fleet.py check` et vérifier que les identités/démarrages des conteneurs clients sont inchangés.
