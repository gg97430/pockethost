---
title: Webhooks
description: Utiliser les webhooks PocketHost pour planifier des appels API fiables sans cron externe. Automatisez sauvegardes, nettoyage, notifications et intégrations, même quand votre instance est en hibernation
---

# Webhooks

Les webhooks permettent de planifier des appels API vers votre instance PocketBase à des moments précis, sans planificateur cron externe. Ils servent à automatiser des tâches comme le nettoyage de données, les sauvegardes, les notifications et les intégrations avec des services externes.

> **Important** : sur PocketHost, les webhooks remplacent la [planification cron intégrée de PocketBase](https://pocketbase.io/docs/js-jobs-scheduling/) (`cronAdd`). `cronAdd` fonctionne dans les déploiements PocketBase classiques, mais devient peu fiable sur PocketHost à cause de l'hibernation des instances. Les webhooks planifiés s'exécutent de façon fiable, même quand votre instance est en hibernation.

## Vue d'ensemble

Les webhooks se configurent depuis le dashboard PocketHost et envoient automatiquement des requêtes HTTP GET vers les endpoints indiqués selon l'intervalle choisi. Chaque webhook contient :

- **Endpoint API** : le chemin d'URL à appeler dans votre instance
- **Planification** : une expression cron qui définit quand le webhook s'exécute

Tous les webhooks s'exécutent en **heure UTC**. Adaptez vos expressions cron en conséquence.

### Pourquoi utiliser les webhooks plutôt que `cronAdd` ?

Sur PocketHost, les webhooks ont plusieurs avantages par rapport au `cronAdd` intégré à PocketBase :

- **Fiabilité** : ils s'exécutent même quand votre instance est en hibernation
- **Constance** : ils ne dépendent pas du temps de disponibilité de votre instance
- **Scalabilité** : ils sont gérés par l'infrastructure PocketHost, pas par votre instance
- **Suivi** : ils donnent une meilleure visibilité sur l'état d'exécution et les erreurs

## Configuration

### Endpoint API

L'endpoint API doit être un chemin valide dans votre instance PocketBase :

- Il doit commencer par `/` (ex. `/api/webhooks/backup`)
- Il peut inclure des paramètres de requête (ex. `/api/cron?token=abc123`)
- Il ne doit pas inclure de protocole ni d'hôte (pas de `http://` ni de `https://`)
- Il prend en charge toute structure de chemin URL valide

**Exemples :**

- `/api/webhooks/daily-cleanup`
- `/api/backup?type=full&compress=true`
- `/webhook/slack/notifications`
- `/api/maintenance/cleanup-old-records`

### Planification (expression cron)

Les webhooks utilisent des expressions cron standards pour définir les horaires d'exécution. Vous pouvez utiliser :

#### Macros prédéfinies

| Macro       | Description                                | Expression équivalente |
| ----------- | ------------------------------------------ | ---------------------- |
| `@yearly`   | Une fois par an à minuit, le 1er janvier   | `0 0 1 1 *`            |
| `@annually` | Identique à `@yearly`                      | `0 0 1 1 *`            |
| `@monthly`  | Une fois par mois à minuit, le premier jour | `0 0 1 * *`            |
| `@weekly`   | Une fois par semaine, le dimanche à minuit | `0 0 * * 0`            |
| `@daily`    | Une fois par jour à minuit                 | `0 0 * * *`            |
| `@midnight` | Identique à `@daily`                       | `0 0 * * *`            |
| `@hourly`   | Une fois par heure, au début de l'heure    | `0 * * * *`            |
| `@minutely` | Une fois par minute                        | `* * * * *`            |
| `@secondly` | Une fois par seconde                       | `* * * * * *`          |
| `@weekdays` | Chaque jour de semaine à minuit            | `0 0 * * 1-5`          |
| `@weekends` | Chaque week-end à minuit                   | `0 0 * * 0,6`          |

#### Expressions cron standards

Les expressions cron standards utilisent 5 champs : `minute heure jour mois jour_semaine`

| Champ | Valeurs | Caractères spéciaux | Description |
| ----- | ------- | ------------------- | ----------- |
| Minute | 0-59 | `* , - / ?` | Minute de l'heure |
| Heure | 0-23 | `* , - / ?` | Heure du jour |
| Jour du mois | 1-31 | `* , - / ? L W` | Jour du mois |
| Mois | 1-12 | `* , - / ?` | Mois de l'année |
| Jour de semaine | 0-6 | `* , - / ? L #` | Jour de la semaine (0 = dimanche) |

**Caractères spéciaux :**

- `*` - N'importe quelle valeur
- `,` - Séparateur de liste de valeurs
- `-` - Plage de valeurs
- `/` - Pas d'incrément
- `?` - N'importe quelle valeur (alias de `*`)
- `L` - Dernier jour du mois ou de la semaine
- `W` - Jour ouvré le plus proche du jour indiqué
- `#` - Nième jour du mois

### Horaires

Tous les webhooks s'exécutent en **heure UTC**. Convertissez votre heure locale en UTC lors de la planification :

- **EST (UTC-5)** : 9 h EST = 14 h UTC (14:00)
- **PST (UTC-8)** : 18 h PST = 2 h UTC le lendemain (02:00)
- **GMT+3** : 15 h = 12 h UTC (12:00)

Utilisez un convertisseur UTC en ligne pour calculer les bons horaires.

## Exemples courants

### Opérations métier (heure UTC)

```cron
# Jours de semaine à 9 h UTC
0 9 * * 1-5

# Chaque lundi à midi UTC
0 12 * * 1

# Chaque vendredi à 18 h UTC
0 18 * * 5

# Premier jour de chaque mois à minuit UTC
0 0 1 * *

# 15 de chaque mois à 8 h UTC
0 8 15 * *
```

### Gestion des données (heure UTC)

```cron
# Sauvegarde quotidienne à 2 h UTC
0 2 * * *

# Nettoyage des anciens enregistrements toutes les 6 heures
0 */6 * * *

# Export hebdomadaire des données le dimanche à minuit UTC
0 0 * * 0

# Maintenance mensuelle le 1er à minuit UTC
0 0 1 * *
```

### Utiliser les macros

```cron
# Opérations quotidiennes
@daily

# Rapports hebdomadaires
@weekly

# Nettoyage mensuel
@monthly

# Jours ouvrés uniquement
@weekdays
```

## Implémentation

### Créer des endpoints webhook

Créez des endpoints API dans votre instance PocketBase pour traiter les requêtes webhook avec le [système de routage PocketBase](https://pocketbase.io/docs/js-routing/) :

```javascript
// pb_hooks/onRequest.pb.js
routerAdd('GET', '/api/webhooks/backup', (e) => {
  // Votre logique de sauvegarde ici
  console.log('Webhook de sauvegarde déclenché')

  // Exemple : créer un enregistrement de sauvegarde
  const backup = new Record($app.findCollectionByNameOrId('backups'), {
    timestamp: new Date().toISOString(),
    status: 'completed',
    size: '1.2GB',
  })

  $app.save(backup)

  return e.json(200, { status: 'success' })
})
```

### Gestion des erreurs

Les webhooks doivent renvoyer des codes HTTP adaptés :

- `200` - Succès
- `400` - Requête invalide
- `500` - Erreur serveur interne

```javascript
routerAdd('GET', '/api/webhooks/cleanup', (e) => {
  try {
    // Votre logique de nettoyage
    return e.json(200, { status: 'success' })
  } catch (error) {
    console.error('Erreur webhook :', error)
    return e.json(500, { error: 'Internal server error' })
  }
})
```

### Authentification

Pour sécuriser vos webhooks, ajoutez une authentification dans vos endpoints :

```javascript
routerAdd('GET', '/api/webhooks/secure', (e) => {
  const token = e.request.url.query().get('token')

  if (token !== process.env.WEBHOOK_SECRET) {
    return e.json(401, { error: 'Unauthorized' })
  }

  // Votre logique webhook sécurisée
  return e.json(200, { status: 'success' })
})
```

## Bonnes pratiques

### 1. Idempotence

Rendez vos webhooks idempotents afin qu'ils puissent être réessayés sans risque :

```javascript
routerAdd('GET', '/api/webhooks/process', (e) => {
  const jobId = e.request.url.query().get('jobId')

  // Vérifier si la tâche a déjà été traitée
  const existing = $app.findFirstRecordByData('jobs', 'jobId', jobId)
  if (existing && existing.get('status') === 'completed') {
    return e.json(200, { status: 'already_processed' })
  }

  // Traiter la tâche
  // ...
})
```

### 2. Logs

Journalisez toujours les exécutions de webhook pour faciliter le debug :

```javascript
routerAdd('GET', '/api/webhooks/backup', (e) => {
  console.log(`Webhook de sauvegarde déclenché à ${new Date().toISOString()}`)

  // Votre logique de sauvegarde

  console.log('Webhook de sauvegarde terminé avec succès')
  return e.json(200, { status: 'success' })
})
```

## Dépannage

### Problèmes courants

1. **Le webhook ne s'exécute pas** : vérifiez la syntaxe de l'expression cron et assurez-vous que les horaires sont en UTC
2. **Endpoint introuvable** : vérifiez que l'endpoint API existe dans votre instance PocketBase via le [routage PocketBase](https://pocketbase.io/docs/js-routing/)
3. **Erreurs d'authentification** : vérifiez les tokens ou secrets requis
4. **Timeouts** : optimisez le temps d'exécution du webhook
5. **Utilisation de `cronAdd` au lieu des webhooks** : remplacez les appels `cronAdd` par des webhooks planifiés pour une exécution fiable sur PocketHost
6. **Mauvaise heure d'exécution** : toutes les planifications sont en UTC ; convertissez votre heure locale en conséquence

## Limites

- Les exécutions concurrentes de webhooks peuvent être limitées
- Les webhooks peuvent ne pas s'exécuter exactement à l'heure indiquée selon la charge système et l'état de l'instance
- Les webhooks sont déclenchés par le système de planification PocketHost, pas par l'horloge interne de votre instance
