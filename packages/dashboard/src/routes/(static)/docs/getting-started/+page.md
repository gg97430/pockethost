---
title: Démarrage
description: Guide pas à pas pour commencer avec PocketHost
---
# Démarrer avec PocketHost

Bienvenue sur PocketHost ! Ce guide vous aide à configurer votre première instance PocketBase et à découvrir les bases de la plateforme.

## 1. Créer un compte PocketHost

La première étape consiste à créer un compte gratuit sur PocketHost.

- **Inscription** : allez sur [pockethost.io/get-started](https://pockethost.io/get-started).
- **Vérification email** : fournissez une adresse email valide et validez-la avec l'email de confirmation.
- **Configuration du compte** : terminez votre profil, puis vous êtes prêt.

_Pour les instructions détaillées, consultez [Création de compte](/docs/account-creation)._

## 2. Choisir un nom d'instance

Après connexion, vous pouvez créer une nouvelle instance PocketBase.

- **Nom automatique** : si vous préférez, PocketHost peut attribuer un nom aléatoire et unique à votre instance.
- **Nom personnalisé** : vous pouvez aussi choisir un nom reflétant votre projet ou votre marque.

**Note** : les noms d'instances doivent être uniques et respecter nos règles de nommage.

_En savoir plus dans [Choisir un nom d'instance](#)._

## 3. Accéder à l'interface admin de l'instance

Une fois votre instance créée, vous pouvez accéder à l'interface admin PocketBase.

- **URL admin** : ouvrez `https://<instance>.pockethost.io/_`
  - Remplacez `<instance>` par le nom de votre instance.
- **Connexion** : utilisez les identifiants admin fournis lors de la création de l'instance.

**Exemple** :

```plaintext
https://myapp.pockethost.io/_
```

_Pour plus de détails, consultez [Accéder à l'interface admin](#)._

## 4. Explorer le dashboard de l'instance

PocketHost fournit un dashboard simple pour gérer vos instances.

- **Accès au dashboard** : connectez-vous à votre compte PocketHost et allez au [Dashboard](https://pockethost.io/dashboard).
- **Gestion des instances** : depuis le dashboard, vous pouvez :
  - Démarrer, arrêter ou relancer les instances.
  - Voir l'état des instances et l'utilisation des ressources.
  - Configurer les paramètres et accéder aux logs.

_Consultez [Utiliser le dashboard PocketHost](#) pour un guide détaillé._

## 5. Gérer les fichiers d'instance (SFTP)

Pour les hooks, migrations et sauvegardes, utilisez **SFTP** avec une clé SSH Ed25519.

1. Créez une clé avec `ssh-keygen -t ed25519` et enregistrez la clé publique dans **[Compte → Clés](/account/keys)**.
2. Connectez-vous à `ftp.pockethost.io` sur le port **2222** avec votre email comme nom d'utilisateur et votre clé privée.

L'ancien FTPS sur le port 21 est déprécié. Consultez **[Accès fichiers SFTP](/docs/ftp)** pour les instructions macOS, Windows, Linux et par client.

Pour surveiller et déployer depuis votre dossier projet, utilisez **[phio](/docs/phio)** (`phio link`, `phio dev`, `phio deploy`).

---

**Étapes suivantes** :

Maintenant que tout est prêt, vous pouvez commencer à construire votre application.

- **Configurer votre base** : créez collections et enregistrements dans l'interface admin.
- **Intégrer votre app** : connectez votre instance PocketBase à votre application web ou mobile.
- **Déployer des mises à jour** : utilisez [phio](/docs/phio), [SFTP](/docs/ftp) ou l'interface admin pour gérer les fichiers de votre instance.

---

_Bon développement avec PocketHost !_
