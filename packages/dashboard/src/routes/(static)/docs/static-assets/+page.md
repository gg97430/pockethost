---
title: Publier des assets statiques
description: Héberger des fichiers statiques sur PocketHost ou les publier directement sur Cloudflare pour éviter de réveiller les instances hibernées
---
# Publier des assets statiques

Les instances PocketHost peuvent héberger des assets statiques. Envoyez les fichiers dans `pb_public` via [SFTP](/docs/ftp) ou [phio](/docs/phio), et PocketBase les servira en HTTPS sur le sous-domaine de votre instance ou votre [domaine personnalisé](/docs/custom-domain).

## Héberger sur PocketHost

Les fichiers statiques de `pb_public` sont servis via l'edge PocketHost et mis en cache sur le **CDN Cloudflare**. C'est bon pour les performances : les visiteurs récurrents obtiennent des réponses rapides depuis le cache.

Il y a aussi un inconvénient. Un **cache miss** (première requête pour un fichier, ou après expiration du cache) atteint votre instance. Si l'instance est en [hibernation](/docs/limits), cette requête **la réveille** et compte dans l'usage. Un site actif avec beaucoup d'assets, ou un trafic qui rate régulièrement le cache, peut garder votre instance réveillée plus que prévu.

Pour les petits projets, prototypes ou apps où quelques cache misses ne posent pas problème, tout héberger sur PocketHost est simple et fonctionne bien.

## Publier directement sur Cloudflare

Si vous pouvez héberger votre frontend ou vos assets statiques sur **Cloudflare** (Pages, R2, Workers ou un autre produit adapté à votre stack), nous le recommandons lorsque c'est pertinent pour votre projet.

Avantages :

- Le trafic statique reste sur le CDN Cloudflare et ne réveille pas votre instance PocketHost.
- Votre backend PocketBase reste inactif jusqu'à l'arrivée de trafic API ou auth.
- Vous pouvez séparer proprement les domaines : l'app sur le site principal, l'API base de données sur un sous-domaine.

### Structure DNS suggérée

Faites pointer votre **site principal** (ou hostname d'app) vers Cloudflare, où vous déployez les assets statiques. Faites un CNAME de votre **instance PocketHost** vers un sous-domaine backend, par exemple :

| Host | Target |
| ---- | ------ |
| `yoursite.com` (ou `www.yoursite.com`) | Votre hébergement Cloudflare Pages / statique |
| `db.yoursite.com` | `your-instance.pockethost.io` |

Ajoutez `db.yoursite.com` comme [domaine personnalisé](/docs/custom-domain) sur l'instance depuis le dashboard. Dans votre frontend, pointez le client PocketBase vers l'URL backend :

```ts
import PocketBase from 'pocketbase'

const client = new PocketBase('https://db.yoursite.com')
```

Configurez CORS dans PocketBase (ou dans `pb_hooks`) pour que votre origine hébergée sur Cloudflare puisse appeler l'API.

## Quand utiliser quelle approche

| Approche | Adapté pour |
| -------- | -------- |
| **`pb_public` sur PocketHost** | Petits sites, interfaces admin-only, backends uniquement avec hooks, prototypes rapides |
| **Cloudflare + sous-domaine `db.*`** | SPA, sites marketing, fort trafic statique, instance hibernée entre les appels API |

Vous pouvez combiner les deux : publier une page admin ou health minimale dans `pb_public` et héberger l'app principale sur Cloudflare.
