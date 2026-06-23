---
title: Auto Vacuum
description: Comprendre comment Auto Vacuum récupère l'espace disque SQLite sur les instances PocketHost inactives pendant la maintenance nocturne
---
# Auto Vacuum

Auto Vacuum garde les bases de données de votre instance compactes en lançant SQLite `VACUUM` pendant la maintenance nocturne de PocketHost.

## Fonctionnement

PocketHost compacte les fichiers `data.db` et `logs.db` de votre instance lorsqu'elle est **inactive** (hibernée). Les instances actives sont ignorées jusqu'à ce qu'elles n'aient plus de requêtes ouvertes et que le conteneur soit arrêté.

La rétention des logs supprime les anciennes lignes, mais SQLite ne réduit pas automatiquement la taille du fichier. Vacuum récupère cet espace mort afin que les sauvegardes et l'usage disque restent raisonnables.

## Indisponibilité

Vacuum est planifié pendant les périodes d'inactivité. Si une requête arrive pendant la compaction, vous pouvez observer **jusqu'à environ 5 secondes** d'indisponibilité pendant que la base termine son opération.

Pendant la maintenance nocturne, l'edge pose un court verrou de maintenance sur chaque instance en cours de compaction. Si votre instance est la suivante dans la file et que vous envoyez une requête à ce moment-là, vous pouvez voir un bref message de maintenance au lieu d'une réponse normale. Réessayer après quelques secondes suffit généralement.

## Activer Auto Vacuum

Auto Vacuum est **activé par défaut** pour les nouvelles instances. Vous pouvez le modifier par instance dans **Zone dangereuse → Auto Vacuum** dans le dashboard.

Quand il est désactivé, PocketHost ignore votre instance pendant la maintenance nocturne. Vous pouvez toujours compacter les bases manuellement si vous auto-hébergez ou accédez au volume en SSH.

## Désactiver Auto Vacuum

Désactivez Auto Vacuum si vous préférez gérer vous-même la maintenance SQLite, ou si vous voulez éviter toute contention, même brève, lors du réveil pendant la tâche nocturne.
