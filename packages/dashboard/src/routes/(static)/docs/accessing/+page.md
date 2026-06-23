---
title: Accéder à une instance
description: Apprendre à accéder à votre instance PocketBase gérée par PocketHost
---
# Accéder à une instance

Votre instance PocketBase gérée par PocketHost est accessible de plusieurs façons :

1. `<uuid>.pockethost.io`
2. `<subdomain>.pockethost.io`
3. [Accès fichiers SFTP](/docs/ftp) pour la gestion directe des fichiers

Chaque instance PocketHost reçoit un UUID permanent et un sous-domaine unique que vous pouvez personnaliser et modifier à tout moment.

> **Exemple :** j'utilise PocketHost pour faire tourner le backend de mon jeu web, Harvest. J'ai créé une instance PocketHost et choisi le sous-domaine `harvest`, ce qui la rend accessible sur `https://harvest.pockethost.io`. Comme les instances peuvent être [renommées](/docs/rename-instance/), PocketHost attribue aussi un UUID permanent. Dans ce cas, l'UUID est `mfsicdp6ia1zpiu`, donc l'instance reste toujours accessible sur `https://mfsicdp6ia1zpiu.pockethost.io`, quels que soient les changements de sous-domaine.

Les domaines personnalisés sont disponibles avec l'offre Pro, et nous gérons les certificats SSL pour vous. En savoir plus sur la configuration des domaines personnalisés [ici](/docs/custom-domain).

Pour l'accès direct aux fichiers, utilisez [SFTP](/docs/ftp) avec une clé SSH afin de gérer sauvegardes, uploads, hooks et logs sur votre instance.
