---
title: Domaine personnalisé
description: Apprendre à configurer un domaine personnalisé pour votre instance PocketHost
---
# Domaine personnalisé

Les instances PocketHost peuvent utiliser un domaine personnalisé au lieu du sous-domaine `*.pockethost.io` par défaut.

## Fonctionnement

Configurer un domaine personnalisé est simple :

1. **Configurez votre enregistrement CNAME** chez votre fournisseur DNS pour pointer vers votre instance PocketHost.
2. **Ajoutez le domaine** dans l'onglet Paramètres de votre instance dans le dashboard PocketHost.
3. **La vérification se fait automatiquement** via le système de vérification HTTP de Cloudflare.

La vérification prend généralement de quelques secondes à quelques minutes, si votre CNAME a bien propagé. PocketHost vérifie automatiquement l'état du domaine, et vous pouvez aussi rafraîchir manuellement avec le bouton près du domaine.

## Processus de configuration

### Étape 1 : configurer le CNAME

Créez un enregistrement CNAME chez votre fournisseur DNS :

- **Nom/Hôte** : le sous-domaine souhaité (ex. `api` pour `api.yourdomain.com`) ou `@` pour le domaine racine.
- **Valeur/Cible** : l'URL de votre instance PocketHost (ex. `your-instance-name.pockethost.io`).

### Étape 2 : ajouter le domaine dans le dashboard

1. Ouvrez votre instance dans le dashboard PocketHost.
2. Allez dans l'onglet **Paramètres**.
3. Ajoutez votre domaine personnalisé dans la section dédiée.
4. PocketHost lance automatiquement la vérification.

### Étape 3 : vérification

PocketHost utilise le système de vérification HTTP de Cloudflare pour confirmer la propriété du domaine. Une fois votre CNAME propagé (souvent en quelques minutes), la vérification se termine automatiquement.

Vous pouvez vérifier l'état à tout moment dans votre dashboard ou cliquer sur le bouton de rafraîchissement pour relancer une vérification.

## Fichiers statiques et domaine personnalisé

Tous les fichiers statiques du dossier `pb_public` de votre instance seront servis via votre domaine personnalisé en HTTPS, ce qui est idéal pour les sites et assets statiques.

---

## Informations de fond

### Comprendre les CNAME

Un enregistrement CNAME (Canonical Name) est un type d'enregistrement DNS qui associe un nom de domaine à un autre. Quand vous créez un CNAME pointant votre domaine vers votre instance PocketHost, les visiteurs de votre domaine personnalisé sont dirigés vers l'instance PocketHost.

Par exemple, si vous créez un CNAME :

- **Name**: `api.yourdomain.com`
- **Value**: `your-instance.pockethost.io`

Quand quelqu'un visite `api.yourdomain.com`, il se connecte en réalité à votre instance PocketHost.

### Gestion DNS sans transfert de domaine

Vous n'avez pas besoin de transférer votre domaine vers un nouveau registrar pour gérer les DNS. Vous pouvez :

1. Garder votre domaine chez votre registrar actuel.
2. Changer vos serveurs de noms pour pointer vers un fournisseur DNS comme Cloudflare.
3. Gérer les enregistrements DNS (dont les CNAME) depuis l'interface de votre fournisseur DNS.

### CNAME Flattening

Le DNS traditionnel n'autorise pas les CNAME sur les domaines racines (ex. `yourdomain.com`). Cloudflare fournit toutefois le CNAME flattening, qui permet aux domaines racines de fonctionner comme des CNAME. Vous pouvez donc pointer votre domaine racine directement vers votre instance PocketHost.

### Avantages Cloudflare

Nous recommandons Cloudflare parce que :

- **CNAME flattening** permet l'utilisation du domaine racine.
- **Vérification HTTP** fournit une vérification de domaine rapide et automatique.
- **CDN global** améliore les performances dans le monde entier.
- **Certificats SSL gratuits** provisionnés automatiquement.
