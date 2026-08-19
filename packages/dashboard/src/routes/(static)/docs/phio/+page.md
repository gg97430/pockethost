---
title: phio CLI
description: Installer et utiliser le CLI phio pour lier, surveiller, déployer, parcourir les fichiers et suivre les logs des instances PocketHost via SFTP
---

# phio CLI

**phio** est l'outil en ligne de commande PocketHost pour synchroniser les fichiers locaux d'un projet PocketBase vers votre instance et suivre ses logs.

Les modes deploy et watch utilisent **SFTP sur le port 2222** avec une clé de déploiement Ed25519. phio crée et enregistre cette clé pour vous à la première utilisation.

## Installation

Nécessite **Node.js 24+**.

```bash
npm install -g phio
```

Depuis le monorepo PocketHost pendant le développement :

```bash
pnpm dev:phio -- --help
```

## Démarrage rapide

```bash
phio login
phio link my-instance
phio dev
```

1. **`phio login`** — connectez-vous avec votre email et mot de passe PocketHost.
2. **`phio link <instance>`** — enregistre l'instance par défaut dans `.phioconfig` dans le dossier courant.
3. **`phio dev`** — surveille les changements locaux et les synchronise vers l'instance distante.

Pour un upload ponctuel sans surveillance :

```bash
phio deploy
```

Passez un nom d'instance à n'importe quelle commande pour remplacer l'instance liée par défaut :

```bash
phio deploy staging
phio logs my-instance
```

## Commandes

| Commande                 | Rôle                                                                     |
| ------------------------ | ------------------------------------------------------------------------ |
| `phio login`             | Connexion à PocketHost                                                   |
| `phio logout`            | Efface la session enregistrée                                            |
| `phio info` (`whoami`)   | Affiche la connexion, l'instance liée et l'état de la clé de déploiement |
| `phio list`              | Liste les instances du compte                                            |
| `phio link [instance]`   | Lie ce dossier à une instance (écrit `.phioconfig`)                      |
| `phio dev [instance]`    | Surveille les fichiers locaux et synchronise à chaque changement         |
| `phio deploy [instance]` | Synchronisation ponctuelle vers le distant                               |
| `phio sftp [instance]`   | Session SFTP interactive vers les fichiers de l'instance                 |
| `phio logs [instance]`   | Suit les logs d'instance via SSE                                         |

`dev` et `deploy` acceptent :

- `-v, --verbose` — sortie de synchronisation détaillée
- `-i, --include <patterns...>` — globs d'inclusion supplémentaires (les valeurs séparées par virgule fonctionnent)
- `-e, --exclude <patterns...>` — globs d'exclusion supplémentaires

**Inclus** par défaut : `pb_*`, `package.json`, `bun.lock`, `bun.lockb`, `patches/**`.

**Exclus** par défaut : `pb_data/**`.

`phio sftp` accepts:

- `--print` — affiche la commande `sftp` sous-jacente au lieu de l'exécuter (utile si les outils client OpenSSH ne sont pas installés)

## Interactive SFTP (`phio sftp`)

Parcourez et modifiez les fichiers d'instance dans une session SFTP interactive. phio utilise la même clé de déploiement **`Phio`** que `dev` et `deploy`.

```bash
phio sftp              # instance liée depuis .phioconfig
phio sftp my-instance  # instance explicite
phio sftp --print      # affiche la commande sftp sans l'exécuter
```

Quand un projet est lié, phio ouvre le dossier de votre instance (`{subdomain}/`). Sans instance liée, phio se connecte à la racine SFTP pour que vous puissiez faire `cd` vers toute instance accessible.

phio lance le client **`sftp`** de votre système lorsqu'il est dans le `PATH` (OpenSSH sur macOS/Linux, OpenSSH Windows optionnel). Si `sftp` manque, phio affiche la commande complète à lancer manuellement. Voir **[Accès fichiers SFTP](/docs/ftp)** pour configurer les clients sur chaque plateforme.

Il s'agit uniquement d'accès fichiers (`ls`, `cd`, `get`, `put`, etc.). Il n'y a pas de shell distant ni de `exec`.

## Lier une instance (`.phioconfig`)

`phio link` écrit un fichier `.phioconfig` à la racine de votre projet :

```json
{
  "instanceName": "all-your-base"
}
```

Commitez ce fichier pour que l'équipe et la CI sachent vers quelle instance le projet déploie.

### Migration des anciennes configs

Les anciens projets peuvent encore contenir :

- `"pockethost": { "instanceName": "..." }` dans `package.json`, ou
- un fichier `pockethost.json` séparé

phio les migre automatiquement vers `.phioconfig` à la première lecture. Les anciennes entrées sont retirées de `package.json`, et `pockethost.json` est supprimé après migration.

## Clé de déploiement (auth SFTP)

phio n'utilise pas votre mot de passe PocketHost pour synchroniser les fichiers. Lors de `login`, `info` et avant chaque `dev`, `deploy` ou `sftp`, phio :

1. Génère ou charge une paire de clés Ed25519 dans le dossier de config phio (par défaut `~/.config/phio/`) :
   - `phio_deploy_ed25519` (clé privée)
   - `phio_deploy_ed25519.pub` (clé publique)
2. Vérifie que **[Compte → Clés](/account/keys)** contient une clé nommée **`Phio`** dont la clé publique correspond au `.pub` local.
3. Crée la clé distante au premier lancement avec accès à **toutes les instances** de votre compte.

Lancez `phio info` pour inspecter les chemins de clés, l'empreinte et l'état d'enregistrement distant.

Si la clé distante **`Phio`** ne correspond pas à votre clé locale, supprimez les fichiers locaux pour régénérer, ou mettez à jour la clé dans le dashboard. Voir **[clés d'accès de compte](/blog/account-access-keys)** pour les clés restreintes en CI.

## Fonctionnement de la synchronisation

phio se connecte à :

| Paramètre         | Valeur                                   |
| ----------------- | ---------------------------------------- |
| Hôte              | `ftp.pockethost.io`                      |
| Port              | `2222`                                   |
| Protocole         | SFTP                                     |
| Nom d'utilisateur | Votre email PocketHost                   |
| Auth              | Clé de déploiement locale **`Phio`**     |
| Dossier distant   | `{instanceName}/` (racine de l'instance) |

La synchronisation est incrémentale. phio écrit `.ftp-deploy-sync-state.json` à la racine de l'instance pour suivre les changements. Ne supprimez ce fichier que si vous voulez une resynchronisation complète.

Utilisez **`phio sftp`** pour une session interactive avec les mêmes identifiants, ou consultez **[Accès fichiers SFTP](/docs/ftp)** pour Cyberduck, FileZilla, extensions VS Code et configuration manuelle `sftp`.

## Variables d'environnement

Remplacez la config enregistrée sans modifier de fichiers :

| Variable              | Rôle                                                      |
| --------------------- | --------------------------------------------------------- |
| `PHIO_USERNAME`       | Email PocketHost (connexion non interactive)              |
| `PHIO_PASSWORD`       | Mot de passe PocketHost (avec `PHIO_USERNAME`)            |
| `PHIO_INSTANCE_NAME`  | Nom d'instance par défaut                                 |
| `PHIO_MOTHERSHIP_URL` | URL API Mothership (production par défaut)                |
| `PHIO_SFTP_HOST`      | Hôte SFTP (`ftp.pockethost.io` par défaut)                |
| `PHIO_SFTP_PORT`      | Port SFTP (`2222` par défaut)                             |
| `PHIO_HOME`           | Dossier de config phio (chemin OS par défaut pour `phio`) |

`PHIO_INSTANCE_NAME` est prioritaire sur `.phioconfig`.
Lancez `phio info` pour vérifier l'endpoint SFTP actif.

Pour une installation PocketHost auto-hébergée, configurez l'API et le
serveur SFTP avant de lancer `phio` :

```bash
export PHIO_MOTHERSHIP_URL=https://app2.monappli.re
export PHIO_SFTP_HOST=ftp.app2.monappli.re
export PHIO_SFTP_PORT=2222

phio login
phio link dekrosh-booking
phio deploy
```

## Exemple CI

Utilisez une connexion via variables d'environnement et un nom d'instance lié en CI :

```yaml
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '24'

      - name: Deploy to PocketHost
        env:
          PHIO_USERNAME: ${{ secrets.POCKETHOST_EMAIL }}
          PHIO_PASSWORD: ${{ secrets.POCKETHOST_PASSWORD }}
          PHIO_INSTANCE_NAME: my-instance
        run: |
          npm install -g phio
          phio deploy -v
```

Stockez votre email et mot de passe PocketHost comme secrets du dépôt. phio enregistre sa clé de déploiement au premier déploiement.

Pour les GitHub Actions qui utilisent déjà [SamKirkland/FTP-Deploy-Action](https://github.com/SamKirkland/FTP-Deploy-Action), migrez de FTPS sur le port 21 vers SFTP sur le port 2222 avec une clé privée Ed25519. Voir **[fin de FTPS](/blog/ftps-sunset)** pour le calendrier.

## Dépannage

### `No instance name provided and none was found in .phioconfig`

Lancez `phio link <instance>` dans le dossier de votre projet, ou passez le nom d'instance en ligne de commande.

### Clé de déploiement incohérente

La clé **`Phio`** dans **[Compte → Clés](/account/keys)** doit correspondre à `~/.config/phio/phio_deploy_ed25519.pub`. Supprimez les fichiers de clé locaux puis lancez `phio info` ou `phio deploy` pour régénérer et réenregistrer.

### Permission denied (publickey) pendant le déploiement

Vérifiez que vous êtes connecté (`phio login` ou `PHIO_USERNAME`/`PHIO_PASSWORD`), puis lancez `phio info` pour vérifier que la clé de déploiement est enregistrée.

### `Could not find 'sftp' on PATH`

Installez les outils client OpenSSH pour votre OS, ou lancez `phio sftp --print` et collez la commande dans votre client SFTP préféré. Voir **[Accès fichiers SFTP](/docs/ftp)**.

### Session expirée

Relancez `phio login`, ou définissez `PHIO_USERNAME` et `PHIO_PASSWORD` pour les environnements non interactifs.
