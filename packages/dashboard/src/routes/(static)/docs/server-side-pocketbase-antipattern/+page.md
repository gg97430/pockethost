---
title: PocketBase côté serveur est généralement un anti-pattern
description: Utiliser PocketBase depuis les fichiers serveur SvelteKit ou Next.js est généralement un anti-pattern.
---

# PocketBase côté serveur est généralement un anti-pattern

Quand vous construisez une application avec PocketBase, il peut être tentant d'y accéder depuis le code serveur de frameworks comme SvelteKit ou Next.js. Cette approche révèle souvent un problème d'architecture et devrait généralement être évitée. Voici pourquoi :

## Le problème de l'accès côté serveur

### Double saut réseau

Quand vous accédez à PocketBase depuis votre code serveur, les requêtes font deux sauts réseau :

1. Client -> votre serveur
2. Votre serveur -> PocketBase

Cela ajoute une latence inutile par rapport à une communication directe du client vers PocketBase.

### Complexité de gestion de l'état JWT

La gestion de l'état d'authentification devient plus complexe quand il faut transférer des tokens JWT entre client et serveur. Si ce flux est mal géré, il introduit souvent des failles de sécurité.

### Problèmes de limitation de débit

Accéder à PocketBase depuis une seule adresse IP backend peut déclencher plus facilement les limites de débit qu'un accès réparti depuis les clients. Ce comportement encourage volontairement la communication directe côté client.

## Meilleures approches

### Utiliser l'accès direct depuis le client

PocketBase est conçu pour être appelé directement depuis les applications clientes. Ses règles de sécurité intégrées fournissent un contrôle d'accès fin sans serveur middleware.

### Utiliser les hooks JS pour les opérations privilégiées

Si vous avez besoin de logique côté serveur ou d'opérations privilégiées, utilisez les hooks JS de PocketBase plutôt que d'envelopper les appels PocketBase dans un backend séparé :

- Créer des endpoints API personnalisés avec les hooks JS
- Gérer les opérations privilégiées directement dans PocketBase
- Implémenter la logique métier là où elle appartient

### Envisager la génération statique

Si vous utilisez principalement le rendu côté serveur pour protéger l'accès à PocketBase, envisagez plutôt :

- Passer à la génération statique (SSG)
- Utiliser les règles de sécurité PocketBase pour la protection
- Implémenter les opérations sensibles via des hooks JS

## Quand l'accès côté serveur est pertinent

Même s'il s'agit généralement d'un anti-pattern, certains cas justifient un accès PocketBase côté serveur :

- Agrégation de données complexe nécessitant des ressources serveur
- Intégration avec des services externes qui ne peuvent pas être exposés aux clients
- Exigences de sécurité spécifiques impossibles à couvrir avec les règles API

Même dans ces situations, vérifiez d'abord si la fonctionnalité peut être implémentée avec les capacités natives de PocketBase.

## Pour aller plus loin

Gani a aussi publié à ce sujet sur le site PocketBase : [JS SSR - issues and recommendations when interacting with PocketBase](https://github.com/pocketbase/pocketbase/discussions/5313)

## Conclusion

PocketBase est conçu comme une solution backend complète, avec sécurité et extensibilité intégrées. Ajouter une couche serveur supplémentaire complique souvent l'architecture sans nécessité. Avant d'implémenter un accès PocketBase côté serveur, demandez-vous si vous pouvez :

1. Utiliser l'accès côté client avec les règles de sécurité
2. Implémenter la fonctionnalité via des hooks JS
3. Restructurer votre application pour tirer parti des capacités natives de PocketBase

Vous obtiendrez des applications plus simples, plus maintenables et plus performantes.
