---
title: Sauvegarde et restauration
description: Apprendre à sauvegarder et restaurer votre instance PocketBase
---
# Sauvegarder et restaurer

PocketBase propose des fonctionnalités intégrées de sauvegarde et de restauration, ce qui facilite la protection de vos données et la récupération en cas de problème. Il existe toutefois des points importants et des méthodes alternatives pour garantir des sauvegardes sûres et fiables.

## Sauvegarde et restauration via l'admin

PocketBase dispose d'une fonctionnalité simple de sauvegarde et restauration directement dans le panneau admin. Vous pouvez créer des sauvegardes des données de votre instance et les restaurer si nécessaire.

![](2024-10-06-15-45-55.png)

- **Pas besoin d'éteindre** : vous pouvez effectuer sauvegardes et restaurations depuis le panneau admin sans éteindre votre instance, avec une interruption minimale.

Si votre instance ne répond plus ou que l'interface admin est inaccessible, vous devrez peut-être utiliser d'autres méthodes de sauvegarde et restauration.

## Sauvegarde et restauration via SFTP

Si la fonctionnalité de sauvegarde/restauration du panneau admin est indisponible ou que votre instance ne répond plus, vous pouvez sauvegarder vos données manuellement via [SFTP](/docs/ftp). Cela donne accès à tous les fichiers PocketBase, y compris la base et les uploads, afin de créer et restaurer des sauvegardes manuelles.

## S3 et sauvegardes planifiées

Automatiser vos sauvegardes est recommandé, surtout lorsque votre instance devient plus complexe et plus utilisée. Il y a toutefois quelques réserves importantes :

- **Hibernation** : si votre instance est en [hibernation](/docs/limits), les sauvegardes automatisées peuvent ne pas s'exécuter. Réveiller l'instance selon un planning ne déclenchera pas les intervalles manqués. Plus votre instance sera active, moins l'hibernation sera fréquente, ce qui limite ce problème. Pour en savoir plus, consultez [S3](/docs/s3) et [Limites](/docs/limits).

- **Sauvegardes S3** : sauvegarder vers S3 (ou une autre solution de stockage externe) est fortement recommandé. S3 permet de préserver l'espace PocketHost pour les ressources critiques comme la base et les logs, qui ne peuvent pas être stockés ailleurs. Les fichiers comme uploads et sauvegardes peuvent en revanche très bien vivre sur S3.

- **Accès SFTP aux sauvegardes** : les sauvegardes stockées sur S3 ne sont **pas** accessibles via SFTP. Vous devrez les gérer directement via S3 ou votre fournisseur de stockage.

## Bonnes pratiques

Pour garder vos données en sécurité :

1. **Sauvegardez régulièrement** votre instance avec le panneau admin ou des méthodes automatisées comme S3.
2. **Utilisez un stockage externe** (ex. S3) pour les sauvegardes et uploads afin d'éviter de consommer l'espace PocketHost.
3. Si vous automatisez les sauvegardes, assurez-vous que votre instance est réveillée et active pendant les créneaux planifiés.
4. Gardez en tête que les **sauvegardes SFTP manuelles** restent disponibles si votre instance devient inaccessible via le panneau admin.

En suivant ces pratiques, vous gardez une stratégie de sauvegarde fiable et vos données en sécurité.
