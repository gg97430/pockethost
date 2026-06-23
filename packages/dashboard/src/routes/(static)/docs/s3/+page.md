---
title: Stockage S3
description: Apprendre à intégrer un stockage compatible S3 avec PocketBase pour gérer sauvegardes, restaurations et uploads tout en économisant les ressources locales
---

# Stockage S3 et PocketBase

Utiliser un stockage compatible S3 avec PocketBase peut optimiser la gestion des ressources et la portabilité de votre instance. Voici ce qu'il faut savoir pour intégrer S3 avec PocketBase.

![](2024-10-06-15-52-14.png)

## Stockage compatible S3 abordable

Une option très abordable pour le stockage compatible S3 est **iDrive**, qui propose des tarifs compétitifs et un service fiable. Vous pouvez configurer PocketBase pour utiliser iDrive ou tout autre fournisseur compatible S3 afin de stocker fichiers, sauvegardes et autres ressources.

## Ce que prend en charge le stockage S3

Le stockage S3 fonctionne très bien avec PocketBase pour les usages suivants :

- **Sauvegardes** : stockez les sauvegardes PocketBase dans un stockage compatible S3 pour les sortir de l'instance locale.
- **Restaurations** : utilisez S3 comme source lors de la restauration de sauvegardes vers votre instance PocketBase.
- **Uploads de fichiers** : déplacez les fichiers uploadés par PocketBase vers S3 pour libérer du stockage local.

En déportant ces tâches vers S3, vous réduisez la charge sur le stockage local de votre instance PocketHost.

## Économie de stockage

Utiliser S3 pour les fichiers comme les uploads et sauvegardes permet de réserver le stockage local aux données critiques, comme les **bases de données et logs**, qui doivent rester sur votre instance. Votre instance PocketHost fonctionne ainsi plus efficacement. En savoir plus sur les limites de stockage dans [Limites](/docs/limits).

## Migrer des fichiers vers S3

Si vous devez déplacer beaucoup de fichiers depuis votre instance PocketHost vers S3, contactez le [support PocketHost](/support). Il peut vous aider à simplifier la migration.

## Portabilité et migration

En stockant les fichiers dans S3, votre instance PocketBase devient beaucoup plus **portable**. Si vous décidez un jour de quitter PocketHost, avoir vos fichiers déjà stockés sur S3 facilite la transition vers un autre hébergeur.

## Point important sur les sauvegardes

Attention : les **sauvegardes PocketBase n'incluent PAS les fichiers de stockage** déplacés vers S3. Lors d'une sauvegarde, seules la base locale et le système de fichiers local sont inclus. Vous devrez gérer séparément les fichiers stockés sur S3 pour obtenir une couverture complète.
