---
title: Étendre PocketBase avec JavaScript
description: Apprendre à étendre PocketBase avec JavaScript dans l'environnement JSVM de PocketBase, et comprendre ses différences avec les environnements JavaScript classiques
---
# Étendre PocketBase avec JavaScript

PocketBase peut être [étendu avec JavaScript](https://pocketbase.io/docs/js-overview/) grâce à des scripts côté serveur qui permettent de personnaliser et d'améliorer votre application. Ces scripts sont exécutés dans le serveur PocketBase au sein d'une machine virtuelle JavaScript (JSVM) basée sur [Goja](https://github.com/dop251/goja), un interpréteur JavaScript écrit en Go.

Il est important de comprendre que l'environnement JSVM de PocketBase diffère des environnements JavaScript classiques comme les API navigateur ou Node.js. Ce guide explique ces différences et montre comment travailler efficacement dans la JSVM PocketBase.

<!-- @import "[TOC]" {cmd="toc" depthFrom=2 depthTo=2 orderedList=false} -->

<!-- code_chunk_output -->

- [Différences avec les API navigateur et Node.js](#différences-avec-les-api-navigateur-et-nodejs)
- [API navigateur absentes](#api-navigateur-absentes)
- [Objet process et variables d'environnement](#objet-process-et-variables-denvironnement)
- [Pas de Promises ni de code asynchrone](#pas-de-promises-ni-de-code-asynchrone)
- [Modules CommonJS pris en charge](#modules-commonjs-pris-en-charge)
- [Fonctionnalités ECMAScript prises en charge](#fonctionnalités-ecmascript-prises-en-charge)
- [Limites de prise en charge](#limites-de-prise-en-charge)
- [Absence des modules standards Node.js](#absence-des-modules-standards-nodejs)

<!-- /code_chunk_output -->

---

## Différences avec les API navigateur et Node.js

L'environnement JSVM de PocketBase n'inclut pas l'ensemble complet des API navigateur ni des API Node.js. Beaucoup d'objets et de fonctions globales attendus dans ces environnements ne sont pas disponibles.

## API navigateur absentes

- **Objets `window` et `document`** : comme les scripts tournent côté serveur, il n'y a pas de Document Object Model (DOM) à manipuler.
- **API Web** : les fonctions comme `fetch`, `alert`, `setTimeout` et `setInterval` ne sont pas disponibles.
- **Écouteurs d'événements** : les méthodes de gestion des événements DOM sont absentes.
- **Prise en charge de `require()`** : la fonction `require()` est disponible dans la JSVM PocketBase pour charger des modules. En revanche, les modules intégrés de Node.js (ex. `fs`, `http`, `path`) ne sont pas disponibles.
- **Pas de modules standards Node.js** : le code qui dépend de modules natifs Node.js comme `fs`, `http` ou `path` ne fonctionnera pas dans l'environnement PocketBase.

  **Exemple de code non pris en charge :**

  ```javascript
  // Ce code ne fonctionnera PAS dans PocketBase
  const fs = require('fs') // module Node.js indisponible
  ```

  Le projet [pocketbase-node](https://www.npmjs.com/package/pocketbase-node) vise à fournir un sous-ensemble compatible des modules standards Node.js, afin de faciliter le portage de code Node.js vers la JSVM PocketBase.

- **Modules personnalisés** : vous pouvez utiliser `require()` pour charger vos propres modules dans l'environnement PocketBase. Tous les modules nécessaires doivent être fournis explicitement par votre codebase.

  **Exemple de code pris en charge :**

  ```javascript
  // Ce code fonctionne si vous fournissez votre propre fichier 'utils.js'
  const utils = require('./utils')
  ```

## Objet process et variables d'environnement

- **Shim `process.env`** : même si le module `process` complet n'est pas disponible, PocketBase fournit un shim pour `process.env`. Vous pouvez utiliser `process.env` pour accéder aux variables d'environnement, comme dans Node.js.

  **Exemple :**

  ```javascript
  const dbHost = process.env.DB_HOST || 'localhost'
  ```

  Le reste de l'objet `process` n'est toutefois pas pris en charge.

## Pas de Promises ni de code asynchrone

Goja, le moteur JavaScript utilisé par PocketBase, ne prend pas en charge les Promises ni le code asynchrone. Tout le code exécuté dans la JSVM est synchrone.

### Implications :

- **Pas d'objets `Promise`** : vous ne pouvez pas créer ni gérer de Promises.
- **Pas de syntaxe `async`/`await`** : les fonctions asynchrones et le mot-clé `await` ne sont pas reconnus.
- **Opérations synchrones uniquement** : toutes les opérations doivent être traitées de façon synchrone.

**Exemple de code non pris en charge :**

```javascript
// Ce code ne fonctionnera PAS dans la JSVM PocketBase
async function fetchData() {
  const response = await fetch('https://api.example.com/data')
  return response.json()
}
```

## Modules CommonJS pris en charge

L'environnement Goja de PocketBase **prend en charge les modules CommonJS** via `require()`. Vous pouvez donc organiser votre code dans plusieurs fichiers et les charger avec `require()`. En revanche, comme indiqué plus haut, les modules intégrés de Node.js ne sont pas disponibles, et tous les modules personnalisés doivent être fournis par votre projet.

### Exemple de prise en charge CommonJS :

```javascript
// utils.js
function greet(name) {
  return `Hello, ${name}`
}

module.exports = { greet }
```

```javascript
// main.js
const utils = require('./utils')
console.log(utils.greet('PocketBase'))
```

## Fonctionnalités ECMAScript prises en charge

Goja prend en charge la plupart des fonctionnalités ECMAScript 2020 (ES11) et ES6. Vous pouvez donc utiliser de nombreuses syntaxes et fonctionnalités JavaScript modernes.

### Fonctionnalités disponibles :

- **Fonctions fléchées** :

  ```javascript
  const add = (a, b) => a + b
  ```

- **Classes et héritage** :

  ```javascript
  class Person {
    constructor(name) {
      this.name = name
    }
  }

  class Employee extends Person {
    constructor(name, id) {
      super(name)
      this.id = id
    }
  }
  ```

- **Template literals** :

  ```javascript
  const greeting = `Hello, ${name}!`
  ```

- **Affectation par déstructuration** :

  ```javascript
  const { x, y } = point
  const [first, second] = array
  ```

- **Paramètres par défaut** :

  ```javascript
  function multiply(a, b = 1) {
    return a * b
  }
  ```

- **Opérateurs spread et rest** :

  ```javascript
  const arr1 = [1, 2]
  const arr2 = [...arr1, 3, 4] // opérateur spread

  function sum(...numbers) {
    // opérateur rest
    return numbers.reduce((a, b) => a + b, 0)
  }
  ```

- **Déclarations let et const** :

  ```javascript
  let count = 0
  const PI = 3.1416
  ```

- **Maps et Sets** :

  ```javascript
  const map = new Map()
  map.set('key', 'value')

  const set = new Set()
  set.add(1)
  ```

- **Type Symbol** :

  ```javascript
  const sym = Symbol('description')
  ```

- **Chaînage optionnel** :

  ```javascript
  const street = user?.address?.street
  ```

- **Opérateur de coalescence des nuls** :

  ```javascript
  const value = input ?? defaultValue
  ```

## Limites de prise en charge

Même si Goja prend en charge de nombreuses fonctionnalités ECMAScript, certaines limites existent :

- **Pas de prise en charge de BigInt** : le type `BigInt` n'est pas disponible.
- **Pas d'objet Intl** : les fonctionnalités d'internationalisation ne sont pas disponibles.
- **Expressions régulières limitées** : certaines fonctionnalités regex avancées peuvent ne pas être entièrement prises en charge.

## Absence des modules standards Node.js

Comme les modules natifs Node.js ne sont pas disponibles, assurez-vous que votre code n'en dépend pas. Si vous avez besoin de fonctionnalités fournies par ces modules, regardez [pocketbase-node](https://www.npmjs.com/package/pocketbase-node), qui vise à fournir un sous-ensemble de modules Node.js compatible avec l'environnement JSVM de PocketBase.
