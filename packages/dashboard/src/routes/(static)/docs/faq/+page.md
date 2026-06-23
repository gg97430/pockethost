---
title: FAQ
description: Questions fréquentes sur PocketHost
---
# FAQ

## À propos

### Qu'est-ce que PocketHost ?

[PocketHost](https://github.com/pockethost/pockethost) est une plateforme d'hébergement open source et multitenant conçue pour déployer et gérer des backends PocketBase. Sous licence MIT, PocketHost a été créé par [benallfree](https://github.com/benallfree) et est maintenu par une communauté de contributeurs. Ben a construit PocketHost pour simplifier l'hébergement de ses projets personnels et clients, sans devoir repartir de zéro à chaque fois.

L'objectif de PocketHost est de fournir une expérience à la Firebase/Supabase, où les utilisateurs peuvent provisionner instantanément de nouvelles instances PocketBase.

En plus de l'hébergement, PocketHost prend en charge sauvegarde, restauration, accès SFTP, hébergement de fichiers statiques et workers cloud Node.js. L'objectif est de fournir une solution clé en main pour déployer rapidement des projets PocketBase petits à moyens avec un minimum de configuration.

### Qu'est-ce que pockethost.io ?

[pockethost.io](https://pockethost.io) est le service d'hébergement officiel basé sur le projet open source PocketHost, également maintenu par [benallfree](https://github.com/benallfree).

### Quels sont les plans à long terme ?

PocketHost a été créé pour servir la communauté PocketBase, en combinant les avantages de souveraineté de l'auto-hébergement avec le confort d'un hébergement géré.

Les priorités de développement suivent les besoins personnels et communautaires, et les contributions de tous sont encouragées.

Vous pouvez toujours exporter les données de votre instance et auto-héberger si nécessaire.

## Données, confidentialité et sécurité

### Quelle est la stabilité ?

pockethost.io et PocketHost sont très stables, avec plus de 99,964 % de disponibilité (voir la [page de statut](https://status.pockethost.io/)). Les incidents sont documentés dans notre [communauté Discord](https://discord.gg/nVTxCMEcGT).

### À quelle fréquence mes données sont-elles sauvegardées ?

Nous sauvegardons les données quotidiennement. Vous pouvez aussi sauvegarder vos données à tout moment avec SFTP.

### Mes données sont-elles en sécurité ?

Oui, vos données sur pockethost.io sont aussi sécurisées que sur votre propre serveur, potentiellement davantage, car notre infrastructure est testée rigoureusement. L'accès à l'infrastructure pockethost.io est sécurisé via SSH avec chiffrement RSA-2048.

Même si le volume de données n'est pas chiffré au niveau OS (voir [#143](https://github.com/benallfree/pockethost/issues/143)), le VPS lui-même est chiffré par Digital Ocean. Vous pouvez toujours sauvegarder et télécharger vos données via [SFTP](/docs/ftp) avec une clé SSH enregistrée dans votre compte.

### Puis-je importer des données dans PocketHost ?

Oui, l'import de données est possible via [SFTP](/docs/ftp/).

### Comment quitter PocketHost et héberger PocketBase moi-même ?

Vous pouvez utiliser SFTP pour télécharger et transférer toutes vos données.

## Tarifs, limites et restrictions d'usage

### Combien coûte le service ?

PocketHost propose des tarifs flexibles selon le nombre d'instances PocketBase dont vous avez besoin.
Consultez les détails sur la [page Tarifs](https://pockethost.io/pricing).

### Restrictions d'usage
PocketHost applique des restrictions pour garantir une expérience équitable et fiable à tous :

**Utilisation raisonnable**
Votre app doit consommer à peu près la même bande passante, le même stockage et le même CPU que l'app active moyenne de notre plateforme. Les apps à faible trafic coexistent efficacement avec les apps à fort trafic grâce à une gestion dynamique des ressources.

**Activités interdites**
- Contenu illégal ou interdit par nos partenaires (ex. paiement/hébergement)
- Spam
- Minage crypto
- Toute utilisation autre que l'hébergement de PocketBase pour applications web ou mobiles
- Mauvaise utilisation des ressources ou activités qui dégradent fortement les performances système ou l'expérience des autres utilisateurs

Consultez les détails dans les [conditions d'utilisation](https://pockethost.io/terms).

## PocketBase

### Comment fonctionne l'email sortant ?

Actuellement, vous devez configurer votre propre service d'email sortant ([SES recommandé](https://pockethost.io/docs/ses)). Nous suivons les plans futurs de support SMTP intégré et discutons des options dans [#154](https://github.com/benallfree/pockethost/discussions/154).

### Comment fonctionne le stockage S3 ?

Vous pouvez configurer un stockage S3 comme avec une instance PocketBase autonome, même si ce n'est souvent pas nécessaire, car notre infrastructure gère déjà efficacement l'hébergement des assets statiques.

### Quelles versions de PocketBase prenez-vous en charge, et comment mettre à niveau ?

Nous prenons en charge toutes les versions de PocketBase. Les nouvelles versions sont détectées automatiquement et votre instance peut recevoir les patch releases. Les mises à niveau majeures sont toutefois verrouillées par défaut. Contactez-nous si vous souhaitez effectuer une mise à niveau majeure.

### Puis-je héberger des binaires PocketBase personnalisés ou un backend Node.js personnalisé ?

Le code backend personnalisé est pris en charge via `pb_hooks`. Le support des binaires PocketBase personnalisés et des backends Node.js personnalisés est en développement.
