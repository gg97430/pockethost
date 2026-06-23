---
title: PocketHost 3.0
description: Ce qui change avec PocketHost 3.0 : SFTP, fin de Flounder, tarifs et préparation.
---

# PocketHost 3.0 arrive

Nous préparons une mise à jour majeure de la plateforme. Rien ne changera du jour au lendemain, mais plusieurs éléments que vous utilisez aujourd'hui évoluent. Cette page résume l'essentiel pour vous aider à vous préparer.

Pour suivre les annonces de lancement et les détails de chaque changement, consultez le [blog PocketHost](/blog).

## SFTP remplace FTPS

**SFTP sur le port 2222** avec des clés SSH Ed25519 devient la méthode principale pour les fichiers d'instance. **FTPS sur le port 21** (email + mot de passe) entre en fin de vie et sera retiré après une période de grâce.

**Ce qui fonctionne aujourd'hui**

- Uploads manuels via SFTP. Consultez [Accès fichiers SFTP](/docs/ftp) et ajoutez vos clés dans [Compte → Clés](/account/keys).
- **`phio dev`**, **`phio deploy`** et **`phio logs`** via SFTP. Consultez [phio CLI](/docs/phio).
- FTPS fonctionne encore pendant la transition. Les nouvelles configurations doivent utiliser SFTP uniquement.

**Ce qui est encore en cours**

- Les workflows GitHub Actions qui ciblent encore FTPS sur le port 21. Migrez vers SFTP ou utilisez `phio deploy` en CI. Consultez [phio CLI](/docs/phio).

Plus de détails : [accès fichiers SFTP](/blog/sftp-file-access) · [fin de FTPS](/blog/ftps-sunset)

## L'accès à vie Flounder disparaît

La formule **Flounder** à vie et paiement unique cesse d'être vendue le **1 juillet 2026**.

- Les **abonnés Flounder existants** conservent leur accès. Votre hébergement ne change pas.
- Les **comptes créés avant le 1 juillet** disposent d'une **période de grâce de 30 jours** (jusqu'au **31 juillet**) pour acheter Flounder s'ils le souhaitent encore.
- Après cela, les nouveaux achats à vie ne seront plus disponibles.

Si vous envisagiez Flounder, lisez le calendrier complet sur le blog et consultez les [tarifs](/pricing) tant que la formule est encore disponible.

Plus de détails : [dernier appel pour l'accès à vie Flounder](/blog/flounder-lifetime-sunset)

## Les tarifs changent

Les abonnements sont ce qui nous permet de maintenir l'hébergement sur le long terme. Nous arrêtons les ventes à vie et lançons de **nouvelles formules mensuelles** avec des limites plus claires pour les **nouveaux clients**.

**Si vous avez déjà un abonnement, votre facturation ne change pas.** Même formule, même prix. Les nouvelles formules s'appliqueront aux inscriptions après leur annonce, pas aux comptes existants.

Nous publierons les détails des nouvelles formules sur le [blog](/blog) avant leur activation pour les nouveaux clients.

## PocketBase v0.39 sur le plan de contrôle

PocketHost 3.0 s'appuie sur un plan de contrôle **mothership PocketBase v0.39**. Cette mise à niveau peut arriver avant le lancement public de la 3.0. Elle ouvre l'accès à des API de contrôle plus récentes et nous maintient alignés avec les versions actuelles de PocketBase. Le choix de version PocketBase pour vos projets reste un sujet séparé. Nous communiquerons clairement tout changement qui affecte vos instances.

## Que faire maintenant

1. **Configurer SFTP.** Générez une clé Ed25519, ajoutez-la dans [Compte → Clés](/account/keys), puis testez une connexion avec [/docs/ftp](/docs/ftp).
2. **Retirer les favoris FTPS.** Orientez vos clients et votre CI vers SFTP quand vous le pouvez. Gardez FTPS uniquement là où vous en avez encore besoin, le temps que vos outils de déploiement suivent.
3. **Décider pour Flounder.** Si l'accès Pro à vie vous convient, achetez-le avant le **1 juillet** (ou avant le **31 juillet** si vous aviez déjà un compte au 1 juillet).
4. **Surveiller les mises à jour.** Suivez le [blog](/blog) et les emails de votre compte pour les calendriers de déploiement de la 3.0.

Des questions ? [Discord](https://discord.gg/nVTxCMEcGT) ou [support](/support).
