---
title: Limites
description: Comprendre les limites appliquées par PocketHost, notamment rate limiting, hibernation, limites d'usage et contenus interdits
---

# Limites

PocketHost applique plusieurs limites afin de garantir une expérience équitable et fiable pour tous les utilisateurs. Voici les principales limites et règles d'utilisation.

## Rate limiting

PocketHost applique plusieurs couches de rate limiting pour assurer une allocation équitable des ressources et la stabilité du système.

### Limites edge Cloudflare

La première couche est appliquée par **Cloudflare**, qui limite les requêtes à **50 requêtes par 10 secondes et par IP**. Cette limite est appliquée à l'edge avant que le trafic n'atteigne l'infrastructure PocketHost.

### Limites PocketHost

PocketHost applique des limites supplémentaires au niveau applicatif :

#### Limites horaires

- **1 000 requêtes par heure et par adresse IP**
- **10 000 requêtes par heure et par instance**

Ces limites se réinitialisent chaque heure et suivent le nombre total de requêtes.

#### Limites de requêtes simultanées

- **5 requêtes simultanées par adresse IP**
- **50 requêtes simultanées par instance**

Ces limites restreignent le nombre de requêtes actives pouvant être traitées en même temps. Une fois une requête terminée, le slot redevient disponible.

### Bonnes pratiques

Si vous faites beaucoup de requêtes côté client, nous recommandons le package NPM [Bottleneck](https://www.npmjs.com/package/bottleneck) pour gérer et limiter efficacement les appels.

En général, dépasser les limites indique souvent un problème de code. Une autre option consiste à écrire des routes personnalisées avec les [hooks JS](/docs/programming) pour faire des récupérations et filtrages en masse côté serveur, ce qui peut être difficile à gérer efficacement côté client.

### Rendu côté serveur (SSR) et serveurs proxy

Si vous utilisez un serveur proxy pour du rendu côté serveur (SSR), toutes les requêtes vers PocketHost sembleront venir de l'adresse IP de votre serveur, et non des IP de vos utilisateurs. Votre serveur atteindra donc vite les limites par IP (1 000 requêtes/heure et 5 requêtes simultanées), ce qui affectera tous vos utilisateurs.

**Solutions recommandées :**

1. **Passer au rendu côté client (CSR)** - Faire les appels API directement depuis le navigateur plutôt que via votre serveur.
2. **Utiliser [PocketPages.dev](https://pocketpages.dev)** - Une solution SSR légère qui tourne directement dans PocketBase.

**Si vous devez utiliser un serveur proxy :**

Si aucune des solutions précédentes ne convient, vous pouvez configurer votre proxy pour transmettre les vraies adresses IP client :

1. Configurez votre serveur proxy pour envoyer l'en-tête `X-PocketHost-Client-IP` à chaque requête, avec la vraie IP du client.
2. Contactez le [support PocketHost](/support) pour mettre l'adresse IP de votre proxy en liste blanche.

Une fois la liste blanche appliquée, PocketHost utilisera l'IP de l'en-tête `X-PocketHost-Client-IP` pour le rate limiting au lieu de l'IP de votre proxy, afin que chaque utilisateur final ait sa propre limite.

### Cas particuliers

Dans certains cas, par exemple lors de conférences ou événements où beaucoup de trafic vient d'une seule IP, nous pouvons augmenter ou contourner ces limites. Si cela vous concerne, contactez le [support PocketHost](/support).

## Hibernation

Pour économiser les ressources, les instances PocketHost peuvent entrer en **hibernation** pendant les périodes d'inactivité. En hibernation, votre instance ne répond pas immédiatement aux requêtes entrantes, mais se réveille lorsqu'une nouvelle requête arrive.

### Points importants :

- **Tâches planifiées et sauvegardes** : les tâches automatisées, comme les sauvegardes planifiées, peuvent ne pas s'exécuter si l'instance est en hibernation au moment prévu. Réveiller l'instance selon un planning ne déclenche pas les intervalles manqués. C'est généralement moins problématique quand votre instance grandit et devient plus active. Consultez [webhooks](/docs/webhooks) pour une alternative aux tâches planifiées qui résiste à l'hibernation.
- **Réveil de l'instance** : l'instance se réveille lors de nouvelles requêtes, mais la première requête après hibernation peut subir un délai pendant le redémarrage.

## Limites d'utilisation

En plus des limites de requêtes, nous surveillons :

- **Bande passante** (entrante et sortante)
- **Stockage**
- **Utilisation CPU**

Nous fonctionnons selon une **politique d'utilisation raisonnable**, décrite dans nos [conditions d'utilisation](/terms). Votre usage est acceptable tant qu'il reste aligné avec celui des autres utilisateurs. Si votre usage dépasse largement les niveaux habituels, nous pouvons vous contacter pour trouver une solution.

Dans les cas extrêmes, si le problème ne peut pas être résolu et impacte négativement les autres utilisateurs, votre instance peut être suspendue. Dans les cas graves, nous pouvons être forcés de supprimer des données sans fournir de sauvegarde. C'est rare, mais il est important de rester dans des limites raisonnables.

## Contenu interdit

Nos [conditions d'utilisation](/terms) détaillent aussi d'autres limites, notamment les restrictions sur les contenus et usages interdits. Consultez-les pour vous assurer que votre instance respecte nos règles.
