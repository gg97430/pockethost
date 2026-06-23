---
title: Introduction
description: Introduction à PocketHost, une plateforme cloud pour PocketBase qui simplifie la configuration et la gestion backend
---
# 👋 Bienvenue sur PocketHost

## Vue d'ensemble

PocketHost héberge vos projets [PocketBase](https://pocketbase.io), pour que vous n'ayez pas à le faire. Créez un projet comme vous le feriez dans Firebase ou Supabase, puis laissez PocketHost gérer le reste.

PocketHost est une plateforme d'hébergement cloud pour PocketBase. Elle permet de provisionner instantanément un backend PocketBase pour votre dernier projet. Fonctionnalités incluses :

- Créer des projets PocketBase, chacun avec un sous-domaine ou un domaine personnalisé.
- Faire tourner chaque instance sur un sous-domaine de `pockethost.io`.
- Accéder à votre instance PocketBase avec le SDK JavaScript PocketBase aussi simplement que `new PocketBase('https://my-project.pockethost.io')`.
- Exécuter votre instance dans un environnement partagé très dimensionné.

## Concentrez-vous sur votre app

Obtenez une instance PocketBase opérationnelle en 10 secondes, sans configuration backend :

1. Créez un compte sur pockethost.io.
2. Provisionnez votre première instance PocketBase.
3. Connectez-vous depuis n'importe où.

```ts
import PocketBase from 'pocketbase'

const client = new PocketBase(`https://harvest.pockethost.io`)
```

## Tout est inclus

Voici tout ce que PocketHost gère côté Linux/devops :

- Email, DKIM, SPF et plus encore
- Jargon DNS : MX, TXT, CNAME
- Provisionnement et gestion des certificats SSL
- Stockage
- Montages de volumes
- Déploiement cloud ou VPS
- CDN et hébergement d'assets statiques
- Amazon AWS
- Et beaucoup d'autres choses : scaling, firewalls, protection DDoS, sécurité utilisateur, rotation des logs, correctifs, mises à jour, outils de build, architectures CPU, multitenancy, etc.
