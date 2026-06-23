---
title: Extensions de l'interface admin
description: Étendre le dashboard superuser PocketBase avec des pages personnalisées, via l'enregistrement pb_hooks et les assets client pb_admin_ext sur PocketHost
---
# Extensions de l'interface admin

PocketBase **0.37+** inclut un dashboard superuser réécrit avec un système expérimental d'**extensions UI**. Vous pouvez ajouter des pages admin personnalisées, des liens d'en-tête, des onglets de collection et des types de champs sans forker PocketBase ni construire une SPA séparée.

Ce mécanisme est différent des [hooks côté serveur](/docs/js) :

| Couche | S'exécute dans | Rôle |
| ------ | -------------- | ---- |
| **Enregistrement serveur** | JSVM (`pb_hooks`) | Monter les fichiers statiques et enregistrer l'extension auprès de PocketBase |
| **Code client** | Navigateur (SPA admin) | `main.js` utilise le global `window.app` pour les routes, les données et l'UI |

Les instances client sur PocketHost nécessitent PocketBase **≥0.37**. Le plan de contrôle mothership tourne en **0.39**.

## Structure des dossiers

Placez les assets de l'extension à côté de vos hooks. PocketHost monte les deux via [SFTP](/docs/ftp) ou [phio](/docs/phio) :

```
pb_hooks/
  admin_plugins.pb.js       ← enregistrement serveur (JSVM)
pb_admin_ext/
  my-plugin/
    main.js                   ← entrée client (requise pour les hooks UI)
    style.css                 ← assets statiques optionnels
```

Dans le dépôt mothership, les hooks d'enregistrement sont empaquetés par tsdown dans `pb_hooks/mothership.pb.js`. Les arborescences statiques sous `pb_admin_ext/` sont de simples fichiers et doivent être déployées à côté de `pb_hooks/`.

## Enregistrement serveur

Enregistrez l'extension dans un hook `onServe`. Chaque `name` devient un segment d'URL sous `/_/extensions/{name}/`.

```js
// pb_hooks/admin_plugins.pb.js
$app.onServe().bindFunc((e) => {
  e.uiExtensions.push({
    name: 'my-plugin',
    fs: $os.dirFS(`${__hooks}/../pb_admin_ext/my-plugin`),
  })
  e.next()
})
```

PocketBase expose automatiquement :

| Route | Rôle |
| ----- | ---- |
| `GET /_/extensions/{name}/{path...}` | Fichiers statiques du dossier d'extension |
| `GET /_/extensions.js` | `main.js` concaténés de toutes les extensions enregistrées |

Redémarrez PocketBase après modification de **l'enregistrement** (le hook). Les modifications de `main.js` seul apparaissent souvent après un rafraîchissement du navigateur, car PocketBase reconstruit `/_/extensions.js` depuis le disque à chaque requête.

## Entrée client (`main.js`)

Le code client s'exécute dans le **navigateur**. Le JavaScript moderne fonctionne (`async`/`await`, API DOM). Il n'est **pas** soumis aux restrictions de la JSVM.

```js
// pb_admin_ext/my-plugin/main.js
app.store.headerLinks.push({
  href: '#/my-plugin',
  icon: 'ri-pulse-line',
  label: 'Mon plugin',
})

app.routes.superuserOnly('#/my-plugin', () => {
  return t.div({ className: 'page' }, t.h1(null, 'Bonjour depuis un plugin admin'))
})
```

Chargez le CSS de l'extension avec des chemins sous `/_/extensions/{name}/` :

```js
document.head.appendChild(
  t.link({
    rel: 'stylesheet',
    href: '/_/extensions/my-plugin/style.css',
  })
)
```

Utilisez `app.pb` pour l'accès aux données. Il hérite de l'authentification superuser de la session admin.

## Déployer sur PocketHost

1. Envoyez `pb_hooks/*.pb.js` et `pb_admin_ext/**` ensemble via SFTP ou phio.
2. Redémarrez l'instance (ou le mothership) pour charger proprement l'enregistrement du hook.
3. Ouvrez l'interface admin (`/_/`), connectez-vous en superuser et vérifiez que votre lien d'en-tête apparaît.

Vérifiez en ligne de commande :

```bash
curl -sI https://your-instance.pockethost.io/_/extensions.js | grep -iE 'content-length|cache-control'
curl -s https://your-instance.pockethost.io/_/extensions/my-plugin/main.js | head
```

`/_/extensions.js` doit renvoyer du JavaScript avec un corps non vide quand `main.js` existe.

## Cache Cloudflare (important)

PocketHost sert le trafic admin via **Cloudflare**. En production (hors `--dev`), PocketBase définit un en-tête de cache long sur la plupart des routes statiques `/_/*` :

```
Cache-Control: max-age=1209600, stale-while-revalidate=86400
```

Cela correspond à **14 jours**. Cloudflare le respecte.

`/_/extensions.js` est reconstruit depuis le disque à chaque requête origin, mais le CDN peut encore servir une **copie en cache obsolète** pendant plusieurs jours. Symptôme : l'interface admin charge, `/_/extensions.js` renvoie **200**, mais le corps est **vide** ou obsolète après le déploiement d'un nouveau plugin.

Nous avons rencontré ce cas sur le dashboard opérateur mothership **Live**. Le plugin était correctement enregistré côté serveur, mais Cloudflare continuait à servir un bundle vide mis en cache avant l'existence de l'enregistrement.

### Correctif : contourner le cache pour les routes d'extension

Dans le dashboard Cloudflare de votre zone, ajoutez une **Cache Rule** :

![Cloudflare Cache Rule — bypass cache for /_/extensions.js and /_/extensions/*](2026-06-16_20-25-07.png)

**If** (expression personnalisée) :

```
(http.request.uri.path eq "/_/extensions.js") or (http.request.uri.path wildcard r"/_/extensions/*")
```

Limitez la règle au nom d'hôte quand c'est possible (mothership ou domaine personnalisé d'instance) :

```
(http.host eq "pockethost-central.pockethost.io") and (
  (http.request.uri.path eq "/_/extensions.js") or
  (http.request.uri.path wildcard r"/_/extensions/*")
)
```

**Then:** Cache eligibility → **Bypass cache**

Les deux chemins sont nécessaires :

- `/_/extensions.js` est l'entrée empaquetée chargée par la SPA admin.
- `/_/extensions/*` sert les assets statiques (`style.css`, images).

Le wildcard `/_/extensions/*` ne couvre **pas** `/_/extensions.js`. Les deux conditions sont nécessaires.

Après l'enregistrement de la règle, **purgez une fois le cache** pour `/_/extensions.js`. Un ancien `HIT` ne se corrigera pas seul.

Vérifiez :

```bash
curl -sI https://your-host/_/extensions.js | grep -i cf-cache-status
```

Il faut obtenir `BYPASS` ou `MISS`, pas `HIT` avec un ancien `age`.

### Développement local

Quand vous lancez mothership avec `--dev`, PocketBase n'ajoute pas l'en-tête de cache de 14 jours sur `/_/*`. Les tests locaux sans Cloudflare ne reproduisent donc pas le problème CDN.

## Realtime vs polling dans le code client

Les routes de plugin admin utilisent le rendu réactif Shablon. Si votre callback de route relance du code d'initialisation à chaque rendu, vous pouvez déclencher des appels API en boucle par erreur.

Pour des données à l'échelle d'une flotte (milliers d'instances) :

- Abonnez-vous en realtime à de **petites collections** (ex. `edges`).
- Interrogez des **compteurs agrégés** à intervalle régulier plutôt que de vous abonner à `instances/*` et de relancer une requête à chaque événement.
- Exécutez l'initialisation une seule fois par visite de page, pas à chaque mise à jour réactive.

## Statut de l'API

Les extensions UI admin sont **expérimentales** dans PocketBase 0.37 à 0.39. Attendez-vous à des changements de forme d'API avant la v1.0. Inspectez `console.log(app)` dans DevTools sur votre version cible de PocketBase.

## Docs liées

- [Étendre avec JS](/docs/js) — hooks JSVM (côté serveur, pas la SPA admin)
- [Accès fichiers SFTP](/docs/ftp) — envoyer hooks et fichiers d'extension
- [phio CLI](/docs/phio) — déployer hooks et dossiers voisins
- [Publier des assets statiques](/docs/static-assets) — cache Cloudflare pour `pb_public`
