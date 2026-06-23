---
title: Changer de version PocketBase
description: Apprendre à mettre à niveau ou rétrograder votre version PocketBase dans PocketHost
---
# Changer de version PocketBase

PocketHost prend en charge la dernière version mineure de chaque version PocketBase (ex. `0.16.*`), et vous pouvez mettre à jour votre instance pour rester sur la dernière release. Quand vous changez de version PocketBase, en montée comme en retour arrière, il est important de prendre des précautions pour préserver la stabilité de l'instance et l'intégrité des données.

## Stratégie de mise à niveau

Mettre votre instance à niveau vers la dernière version de PocketBase est simple, mais demande un peu de préparation pour éviter perte de données ou comportement inattendu. Approche recommandée :

1. [Sauvegardez votre instance](/docs/backup-restore) pour protéger vos données.
   ![](2024-10-06-15-31-47.png)
2. **Créez une seconde instance** : nous recommandons de créer une seconde instance PocketHost et d'y restaurer votre sauvegarde. Cela permet de tester la mise à niveau sans toucher à l'instance en production.
3. Effectuez la mise à niveau sur la seconde instance et vérifiez que tout fonctionne comme prévu.
   ![](version-change.png)
4. Quand vous êtes sûr que tout fonctionne, mettez à niveau votre instance principale.
5. [Éteignez votre instance](/docs/power) pour la relancer après la mise à niveau.

## Étapes de mise à niveau automatique

Dans la plupart des cas, une mise à niveau automatique se fait simplement en passant à la dernière version :

1. [Sauvegardez votre instance](/docs/backup-restore).
   ![](2024-10-06-15-31-47.png)
2. Passez à la dernière version prise en charge dans le dashboard PocketHost (ex. `0.16.*`).
   ![](version-change.png)
3. [Éteignez votre instance](/docs/power) puis relancez-la pour appliquer les changements.

## Étapes de mise à niveau manuelle (cas rares)

Si la mise à niveau automatique rend l'instance inaccessible à cause de problèmes de schéma de base, une mise à niveau manuelle est nécessaire :

1. [Sauvegardez votre instance](/docs/backup-restore) via l'admin PocketBase.
2. Téléchargez votre sauvegarde de base depuis l'admin PocketBase.
3. Sur votre machine locale, suivez les étapes de mise à niveau nécessaires selon la [documentation PocketBase](https://pocketbase.io/docs/).
4. Faites une sauvegarde locale après la mise à niveau.
5. [Restaurez](/docs/backup-restore) la sauvegarde mise à niveau via l'admin PocketBase live.

## Notes importantes

### Rétrograder

**Soyez prudent avec les rétrogradations** : PocketBase ne prend pas officiellement en charge les retours arrière. Cela peut fonctionner dans certains cas, mais rien ne garantit que cela réussira sans problème. Si vous tentez une rétrogradation et rencontrez des erreurs, il n'existe pas de chemin de résolution officiel.

### Tester avant la mise à niveau

Pour éviter les interruptions, créez toujours une seconde instance, restaurez-y votre sauvegarde et testez d'abord la mise à niveau dessus. Cette pratique permet de repérer les problèmes avant d'appliquer les changements à votre instance live.

### Migrations des tables système

Lors d'une mise à niveau, PocketBase peut effectuer des migrations automatiques sur les tables système. Ces migrations sont généralement non destructives, mais procédez avec prudence et assurez-vous d'avoir une sauvegarde avant toute mise à jour.
