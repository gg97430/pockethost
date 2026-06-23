---
title: Étendre PocketBase avec JSVM
description: Apprendre à étendre PocketBase avec JSVM, des plugins et du rendu côté serveur avec PocketPages
---
# Programmer et étendre PocketBase avec JSVM

PocketBase n'est pas seulement une solution backend simple : il offre de puissantes capacités d'extension via JavaScript, notamment avec le **moteur Goja** et sa **JSVM** (JavaScript Virtual Machine). Cela permet d'ajouter logique personnalisée, plugins et rendu côté serveur à vos applications, ce qui rend PocketBase très polyvalent.

## Un écosystème en croissance

L'**écosystème PocketBase** se développe rapidement, ce qui facilite l'amélioration des applications de manière modulaire et scalable. Un projet important est **[pocketpages.dev](https://pocketpages.dev)** : une plateforme pour construire des pages classiques rendues côté serveur (SSR) avec PocketBase. Grâce à la **JSVM** de PocketBase et au moteur Goja, les développeurs peuvent servir du contenu SSR et proposer une expérience fluide pour des pages statiques ou dynamiques directement depuis PocketBase.

## Étendre PocketBase avec JSVM

La **JSVM** de PocketBase permet d'exécuter du code JavaScript personnalisé directement dans votre instance PocketBase. Elle peut servir à beaucoup de tâches, de l'extension d'API à l'ajout de logique métier côté serveur. Avec la JSVM, vous pouvez écrire du JavaScript qui interagit avec les services, hooks et événements internes de PocketBase.

### Cas d'usage de JSVM

- **Routes API personnalisées** : utilisez la JSVM pour définir et exécuter des routes personnalisées au-delà des API PocketBase par défaut. Cela permet des manipulations de données plus avancées, des validations personnalisées et une logique métier complexe.
- **Hooks et déclencheurs** : intégrez des **hooks JavaScript** à votre application pour déclencher des actions lors de changements en base, par exemple envoyer des notifications lors de mises à jour ou appliquer des validations complexes avant l'écriture des données.

- **Modules réutilisables** : empaquetez votre logique personnalisée comme modules JavaScript dans `pb_hooks` et partagez des patterns entre instances. La JSVM rend simple la création de code serveur réutilisable pour PocketBase.

## Héberger du contenu statique et SSR

**PocketHost** est un excellent choix pour héberger du contenu statique/SSG (Static Site Generation) comme du contenu SSR (Server-Side Rendered). Avec **PocketPages.dev** et des technologies similaires, vous pouvez combiner la JSVM de PocketBase et le rendu côté serveur pour livrer des applications web entièrement dynamiques.

- **Contenu statique** : PocketHost prend en charge les fichiers statiques et le SSG, idéal pour les sites statiques ou applications monopage.
- **Rendu côté serveur (SSR)** : avec PocketPages et les capacités JavaScript de PocketBase, vous pouvez servir des pages SSR directement depuis votre backend. Vous profitez ainsi d'un meilleur SEO et de chargements initiaux plus rapides pour les contenus dynamiques.
