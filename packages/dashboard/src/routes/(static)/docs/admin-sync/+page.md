---
title: Admin Sync
description: Comprendre comment Admin Sync synchronise votre compte admin PocketBase avec vos identifiants pockethost.io
---
# Admin Sync

Admin Sync garantit que votre instance dispose toujours d'un compte admin correspondant aux identifiants de connexion de votre compte pockethost.io.

## Activer Admin Sync

![](admin-sync.png)

Quand Admin Sync est activé, les identifiants de votre compte pockethost.io sont automatiquement copiés comme identifiants admin dans votre instance avant son lancement.

Si vous modifiez vos identifiants pockethost.io pendant qu'une instance tourne, les nouveaux identifiants ne seront synchronisés qu'au redémarrage de l'instance. Pour appliquer immédiatement les changements, [éteignez l'instance](/docs/power) puis relancez-la.

Par défaut, Admin Sync est activé. Ainsi, lorsqu'une instance est créée, elle possède un compte admin correspondant à votre connexion pockethost.io. Cette fonctionnalité de sécurité empêche des utilisateurs non autorisés de créer le compte admin initial.

## Plan de contrôle indisponible

Pour les performances, l'edge lance votre instance même lorsque le plan de contrôle PocketHost (mothership) est brièvement indisponible. Admin Sync peut être ignoré sur ce démarrage. Votre instance conserve les identifiants admin de sa dernière synchronisation réussie.

Si la connexion admin échoue après un tel lancement, ou si vous avez changé votre mot de passe pockethost.io pendant que l'instance était éteinte, [éteignez l'instance](/docs/power) puis relancez-la quand le plan de contrôle est sain.

## Désactiver Admin Sync

![](admin-sync-off.png)

Quand Admin Sync est désactivé, vos identifiants pockethost.io ne seront plus copiés dans votre instance lors des futurs lancements.
