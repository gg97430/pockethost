---
title: Accès fichiers SFTP
description: Accéder aux fichiers des instances PocketBase en SFTP avec des clés SSH Ed25519 sur macOS, Windows et Linux
---
# Accès fichiers SFTP

PocketHost fournit un accès **SFTP** aux fichiers de votre instance. Cela remplace l'ancien **FTPS** (FTP over TLS sur le port 21) comme méthode recommandée pour envoyer hooks, migrations et sauvegardes.

L'authentification se fait **uniquement par clés SSH Ed25519**. Il n'y a pas de connexion par mot de passe en SFTP. Gérez les clés dans **[Compte → Clés](/account/keys)** dans le dashboard.

> **FTPS est déprécié.** Le FTPS explicite sur le port 21 fonctionne encore pour le moment avec votre email et mot de passe PocketHost, mais il sera retiré. Utilisez SFTP pour toute nouvelle configuration. Voir [l'annonce SFTP](/blog/sftp-file-access).

## Paramètres de connexion

Utilisez ces valeurs dans chaque client :

| Paramètre | Valeur |
| ------- | ----- |
| Protocole | **SFTP** (SSH File Transfer Protocol). Pas FTP, pas FTPS. |
| Hôte | `ftp.pockethost.io` |
| Port | `2222` |
| Nom d'utilisateur | Votre **adresse email** PocketHost |
| Authentification | **Clé privée SSH** (Ed25519) |
| Mot de passe | Laisser vide (non utilisé) |

Chaque clé SSH peut accéder à **toutes les instances** de votre compte ou à un **sous-ensemble précis** choisi lors de la création de la clé.

## 1. Créer une clé SSH

Générez une clé Ed25519 sur votre machine si vous n'en avez pas encore :

```bash
ssh-keygen -t ed25519 -f ~/.ssh/pockethost_ed25519 -C "you@example.com"
```

Appliquez des permissions restrictives sur macOS et Linux :

```bash
chmod 600 ~/.ssh/pockethost_ed25519
```

Ouvrez **[Compte → Clés](/account/keys)** dans le dashboard :

1. Saisissez un **titre** (par exemple `MacBook` ou `GitHub Actions`).
2. Collez le contenu de `~/.ssh/pockethost_ed25519.pub` (commence par `ssh-ed25519`).
3. Choisissez **toutes les instances** ou des instances spécifiques, puis cliquez sur **Ajouter la clé SSH**.

Gardez le fichier **sans** `.pub` comme clé privée. PocketHost ne stocke que la clé publique.

## 2. Se connecter en ligne de commande

### macOS et Linux

OpenSSH est inclus avec macOS et la plupart des distributions Linux.

Connexion ponctuelle :

```bash
sftp -i ~/.ssh/pockethost_ed25519 -P 2222 you@example.com@ftp.pockethost.io
```

Ajoutez `~/.ssh/config` pour créer un alias court :

```sshconfig
Host pockethost
  HostName ftp.pockethost.io
  Port 2222
  User you@example.com
  IdentityFile ~/.ssh/pockethost_ed25519
  IdentitiesOnly yes
```

Puis connectez-vous avec :

```bash
sftp pockethost
```

Envoyez un hook avec `scp` :

```bash
scp -i ~/.ssh/pockethost_ed25519 -P 2222 ./pb_hooks/myhook.pb.js you@example.com@ftp.pockethost.io:your-instance/pb_hooks/
```

Synchronisez un dossier avec `rsync` (macOS : installez-le via Homebrew s'il manque) :

```bash
rsync -avz -e "ssh -i ~/.ssh/pockethost_ed25519 -p 2222" ./pb_hooks/ you@example.com@ftp.pockethost.io:your-instance/pb_hooks/
```

### Windows

**Option A : OpenSSH (Windows 10/11)** — intégré. Activez *Settings → Apps → Optional features → OpenSSH Client* si nécessaire.

Enregistrez votre clé privée dans `C:\Users\YourName\.ssh\pockethost_ed25519`.

PowerShell ou invite de commandes :

```powershell
sftp -i C:\Users\YourName\.ssh\pockethost_ed25519 -P 2222 you@example.com@ftp.pockethost.io
```

**Option B : PuTTY / PuTTYgen** — si votre clé est au format OpenSSH, utilisez **Conversions → Import key** dans PuTTYgen puis enregistrez un fichier `.ppk`. Dans PuTTY : Connection → SSH → Auth → Credentials → Private key file. Hôte `ftp.pockethost.io`, port `2222`. PuTTY n'inclut pas de navigation fichiers SFTP ; utilisez-le avec **WinSCP** (voir plus bas) ou utilisez OpenSSH `sftp`.

**Option C : WSL** — utilisez les instructions macOS/Linux dans votre distribution Linux.

## 3. Clients graphiques et IDE

Tous les clients utilisent les mêmes hôte, port, nom d'utilisateur et clé privée que dans [Paramètres de connexion](#paramètres-de-connexion). Le protocole doit être **SFTP**, pas FTP ni FTPS.

### Cyberduck (macOS, Windows)

1. **Open Connection** → protocole **SFTP (SSH File Transfer Protocol)**.
2. Server : `ftp.pockethost.io`, Port : `2222`, Username : votre email.
3. Cliquez sur **SSH Private Key** et choisissez votre clé privée (`.pem` ou format OpenSSH sans extension).
4. Connectez-vous. Vous arrivez dans `/` avec un dossier par instance accessible.

### FileZilla (macOS, Windows, Linux)

1. **File → Site Manager → New Site**.
2. Protocol : **SFTP - SSH File Transfer Protocol**.
3. Host : `ftp.pockethost.io`, Port : `2222`, Logon Type : **Key file**.
4. User : votre email. Key file : chemin vers votre clé privée.
5. Connectez-vous.

### WinSCP (Windows)

1. Nouvelle session → protocole fichier **SFTP**.
2. Host : `ftp.pockethost.io`, Port : `2222`, User : votre email.
3. **Advanced → SSH → Authentication** → Private key file (`.ppk` ou OpenSSH ; WinSCP peut convertir à l'import).
4. Enregistrez puis connectez-vous.

### Transmit (macOS)

1. Nouveau serveur → **SFTP**.
2. Adresse : `ftp.pockethost.io:2222`, User : votre email.
3. Onglet Keys → importez ou sélectionnez votre clé privée.
4. Connectez-vous.

### VS Code

Installez une extension SFTP comme [SFTP](https://marketplace.visualstudio.com/items?itemName=Natizyskunk.sftp) ou [SSH FS](https://marketplace.visualstudio.com/items?itemName=Kelvin.vscode-sshfs).

Exemple `sftp.json` (extension SFTP) pour synchroniser `pb_hooks` :

```json
{
  "name": "PocketHost my-instance",
  "host": "ftp.pockethost.io",
  "protocol": "sftp",
  "port": 2222,
  "username": "you@example.com",
  "privateKeyPath": "~/.ssh/pockethost_ed25519",
  "remotePath": "/my-instance/pb_hooks",
  "uploadOnSave": true
}
```

Adaptez `remotePath` au sous-domaine et au dossier de votre instance.

### JetBrains IDEs (IntelliJ, WebStorm, etc.)

1. **Tools → Deployment → Configuration**.
2. Ajoutez un serveur **SFTP** : `ftp.pockethost.io`, port `2222`, user = email.
3. **SSH configuration** → type d'authentification **Key pair**, private key file = votre clé.
4. Mappez le dossier projet local vers `/your-instance/pb_hooks` ou un autre chemin.

## Structure de l'instance

Après connexion, vous voyez un dossier pour chaque instance accessible par votre clé. Les noms de dossiers sont les **sous-domaines** des instances (par exemple `harvest`), pas les UUID. Faites `cd` dans un dossier pour accéder aux dossiers PocketBase habituels :

| Dossier | Description |
| --------- | ----------- |
| `pb_hooks` | Hooks JS PocketBase ([docs](https://pocketbase.io/docs/js-overview/)) |
| `pb_migrations` | Fichiers de migration ([docs](https://pocketbase.io/docs/migrations/)) |
| `pb_public` | Fichiers publics statiques |
| `pb_data` | Base de données et uploads ([docs](https://pocketbase.io/docs/going-to-production/)) |
| `pb_data/backups` | Sauvegardes PocketBase |
| `pb_data/storage` | Fichiers uploadés ([docs](https://pocketbase.io/docs/files-handling/)) |

La racine de l'instance est **virtuelle**. Vous ne voyez que ces dossiers standards, pas de nouveaux dossiers arbitraires au premier niveau.

**Éteignez** votre instance avant de modifier `pb_data` (même règle que dans le dashboard). Les autres dossiers peuvent être édités pendant que l'instance tourne.

## Dépannage

### Permission denied (publickey)

- Vérifiez que la clé publique est enregistrée dans **[Compte → Clés](/account/keys)**.
- Le nom d'utilisateur doit être votre **email**, pas le sous-domaine de l'instance.
- La clé doit être **Ed25519** (`ssh-ed25519`).
- Vérifiez que la clé est autorisée à accéder à l'instance (toutes les instances ou liste spécifique).
- Pointez le client vers la bonne clé privée. Si OpenSSH propose la mauvaise clé, ajoutez `IdentitiesOnly yes` sous l'hôte dans `~/.ssh/config`.

### Connexion refusée ou timeout

- Le port doit être **2222**, pas 21 ni 22.
- L'hôte est `ftp.pockethost.io`, pas `your-instance.pockethost.io`.

### Clé d'hôte inconnue à la première connexion

OpenSSH demande de vérifier la clé d'hôte du serveur lors de la première connexion. C'est normal. Tapez `yes` pour continuer, ou ajoutez l'hôte à `~/.ssh/known_hosts` via le flux de confiance de votre client. Les clients graphiques (Cyberduck, FileZilla, WinSCP) affichent une demande d'empreinte similaire.

### Avertissement post-quantique OpenSSH

Les clients OpenSSH récents peuvent prévenir que la connexion n'utilise pas d'algorithme d'échange de clés post-quantique. Cela concerne le chiffrement de transport, pas votre clé SSH. Vous pouvez vous connecter. Nous mettrons à jour lorsque notre stack SFTP prendra en charge le KEX hybride PQ. Détails dans [l'article SFTP](/blog/sftp-file-access).

## Ancien FTPS

FTPS sur le port 21 (TLS explicite, email + mot de passe) reste disponible pendant la période de migration. Ne l'utilisez pas pour les nouveaux projets. Il sera retiré après une période de fin documentée.

## Déploiement automatisé (phio et CI)

Pour le développement quotidien, utilisez **[phio](/docs/phio)** pour lier un projet, surveiller les fichiers locaux et synchroniser via SFTP :

```bash
phio login
phio link my-instance
phio dev
```

phio gère sa propre clé de déploiement Ed25519 (libellée **`Phio`** dans Compte → Clés). Vous n'avez pas besoin d'une clé séparée pour phio sauf si vous voulez un accès CI restreint.

Pour GitHub Actions, lancez **`phio deploy`** avec les secrets `PHIO_USERNAME` / `PHIO_PASSWORD` (voir [phio CLI](/docs/phio)), ou migrez [SamKirkland/FTP-Deploy-Action](https://github.com/SamKirkland/FTP-Deploy-Action) de FTPS sur le port 21 vers SFTP sur le port 2222 avec une clé privée Ed25519. Voir **[fin de FTPS](/blog/ftps-sunset)** pour le calendrier de migration.
