# Installation serveur de A a Z

Ce fichier documente l'installation du projet sur un VPS Ubuntu pour le domaine `monappli.re`, avec le dashboard sur `https://app.monappli.re` et les instances PocketBase sur `https://<instance>.monappli.re`.

L'architecture actuelle est volontairement simple:

- un seul serveur Ubuntu;
- Docker pour lancer les instances PocketBase;
- PM2 pour garder les services Node actifs;
- le process `firewall` du projet ecoute directement les ports `80` et `443`;
- le dashboard statique ecoute en local sur `127.0.0.1:5174`;
- la mothership PocketBase ecoute sur `8091`;
- le daemon d'instances ecoute sur `3000`;
- les donnees sont sous `/home/ubuntu/.local/share/pockethost`.

## 1. Prerequis

- VPS Ubuntu 24.04 ou 26.04.
- Utilisateur SSH: `ubuntu`.
- Domaine: `monappli.re`.
- IP serveur: `141.94.92.92`.
- Acces GitHub au fork `gg97430/pockethost`.
- Certificat TLS valide pour `monappli.re` et `*.monappli.re`.
- Token Cloudflare si tu veux automatiser les DNS de domaines personnalises.

## 2. DNS Cloudflare

Dans Cloudflare, creer au minimum:

```text
A      app                 141.94.92.92
A      *                   141.94.92.92
A      monappli.re         141.94.92.92
A      pockethost-central  141.94.92.92
A      ftp                 141.94.92.92
```

Notes:

- `app.monappli.re` sert le dashboard interne.
- `*.monappli.re` sert les instances.
- `ftp.monappli.re` doit rester en DNS only si FTP/SFTP est utilise, car Cloudflare ne proxy pas FTP/SFTP.
- Pour HTTPS via Cloudflare, mettre SSL/TLS en `Full (strict)` et installer un certificat Origin Cloudflare couvrant `monappli.re` et `*.monappli.re`.

## 3. Preparation Ubuntu

Se connecter:

```bash
ssh ubuntu@141.94.92.92
```

Installer les paquets systeme:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y \
  git curl ca-certificates build-essential unzip jq rsync htop \
  docker.io ufw
```

Activer Docker:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu
newgrp docker
```

Installer Node 24, pnpm et PM2:

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pnpm@11.6.0 pm2
node -v
pnpm -v
pm2 -v
```

Le projet demande Node `>=24`. Le serveur actuel tourne en Node `v24.17.0`.

Mettre le serveur a l'heure de La Reunion:

```bash
sudo timedatectl set-timezone Indian/Reunion
timedatectl
date
```

Autoriser Node a ecouter les ports bas `80`, `443` et `21` sans lancer PM2 en root:

```bash
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f "$(command -v node)")"
getcap "$(readlink -f "$(command -v node)")"
```

Configurer le firewall systeme:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 21/tcp
sudo ufw allow 2222/tcp
sudo ufw allow 10000:20000/tcp
sudo ufw --force enable
sudo ufw status
```

Les ports FTP/passifs peuvent etre retires si tu n'utilises pas FTP.

## 4. Cloner le projet

```bash
cd /home/ubuntu
git clone -b self-host-install-fixes https://github.com/gg97430/pockethost.git
cd /home/ubuntu/pockethost
```

Si le depot devient prive, utiliser une cle SSH de deploiement:

```bash
git clone -b self-host-install-fixes git@github.com:gg97430/pockethost.git
```

Installer les dependances:

```bash
pnpm install --frozen-lockfile
```

## 5. Variables d'environnement serveur

Creer `/home/ubuntu/pockethost/.env`:

```bash
cd /home/ubuntu/pockethost
nano .env
```

Exemple adapte au serveur actuel:

```env
NODE_ENV=development
PH_DEBUG=false

APEX_DOMAIN=monappli.re
HTTP_PROTOCOL=https:
APP_URL=https://app.monappli.re
BLOG_URL=https://app.monappli.re
MOTHERSHIP_URL=http://127.0.0.1:8091

PH_SECRET=remplacer-par-un-secret-long
MOTHERSHIP_ADMIN_USERNAME=admin@example.com
MOTHERSHIP_ADMIN_PASSWORD=remplacer-par-un-mot-de-passe-fort
TEST_EMAIL=admin@example.com
PH_SERVER_TIMEZONE=Indian/Reunion

DAEMON_PORT=3000
MOTHERSHIP_PORT=8091
DAEMON_PB_IDLE_TTL=5000

PH_HOME=/home/ubuntu/.local/share/pockethost
DATA_ROOT=/home/ubuntu/.local/share/pockethost/data

PH_FTP_PORT=21
PH_SFTP_PORT=2222
PH_FTP_PASV_IP=141.94.92.92
PH_FTP_PASV_PORT_MIN=10000
PH_FTP_PASV_PORT_MAX=20000

MOTHERSHIP_SEMVER=0.39.*
PH_AUTO_VERIFY_SIGNUPS=true
PH_SIGNUP_SUBSCRIPTION_QUANTITY=250

# A activer si le firewall doit limiter les abus.
PH_ENABLE_FIREWALL_RATE_LIMIT=1

# Optionnel: limiter l'acces a certaines IP CIDR.
# Attention: si renseigne, les autres IP peuvent etre bloquees.
# IPCIDR_LIST=
```

Important: `NODE_ENV=development` est utilise dans cette version self-host parce que le firewall route alors `app.monappli.re` vers le dashboard. Si le firewall est modifie pour router `app.monappli.re` aussi en production, on pourra repasser a `NODE_ENV=production`.

## 6. Variables frontend

Creer `packages/dashboard/.env`:

```bash
cat > packages/dashboard/.env <<'EOF'
PUBLIC_APEX_DOMAIN=monappli.re
PUBLIC_APP_URL=https://app.monappli.re
PUBLIC_MOTHERSHIP_URL=https://app.monappli.re
EOF
```

Ces variables doivent etre presentes avant le build du dashboard.

## 7. Certificat TLS

Le process `firewall` attend ces fichiers:

```text
/home/ubuntu/.local/share/pockethost/ssl/tls.key
/home/ubuntu/.local/share/pockethost/ssl/tls.cert
```

Avec Cloudflare, generer un certificat Origin dans:

```text
Cloudflare > SSL/TLS > Origin Server > Create certificate
```

Hostnames a inclure:

```text
monappli.re
*.monappli.re
```

Puis copier le certificat et la cle:

```bash
mkdir -p /home/ubuntu/.local/share/pockethost/ssl
nano /home/ubuntu/.local/share/pockethost/ssl/tls.cert
nano /home/ubuntu/.local/share/pockethost/ssl/tls.key
chmod 600 /home/ubuntu/.local/share/pockethost/ssl/tls.key
chmod 644 /home/ubuntu/.local/share/pockethost/ssl/tls.cert
chown -R ubuntu:ubuntu /home/ubuntu/.local/share/pockethost/ssl
```

Verifier:

```bash
ls -la /home/ubuntu/.local/share/pockethost/ssl
```

## 8. Build

Depuis `/home/ubuntu/pockethost`:

```bash
pnpm --filter pockethost check:types
pnpm --filter pockethost-mothership-app build
pnpm --filter @pockethost/dashboard build
```

Le build mothership genere:

```text
packages/pockethost/src/mothership-app/pb_hooks/mothership.js
packages/pockethost/src/mothership-app/pb_hooks/mothership.pb.js
```

Si ces fichiers changent, il faut les committer et les pousser dans Git.

## 9. Premier demarrage PM2

Demarrer tous les services:

```bash
cd /home/ubuntu/pockethost
pm2 start ecosystem.config.cjs
pm2 save
pm2 startup
```

La commande `pm2 startup` affiche une commande `sudo env PATH=... pm2 startup ...`. Copier/coller cette commande, puis refaire:

```bash
pm2 save
```

Services attendus:

```text
firewall
dashboard
edge-daemon
edge-ftp
edge-sftp
mothership
pocketbase-update
health-check
edge-vacuum
edge-purge-orphans
```

Verifier:

```bash
pm2 status
pm2 logs firewall --lines 50
pm2 logs mothership --lines 50
pm2 logs dashboard --lines 50
```

## 10. Telecharger les versions PocketBase

Le job PM2 `pocketbase-update` le fait automatiquement, mais on peut forcer une premiere synchro:

```bash
cd /home/ubuntu/pockethost
pnpm prod:cli pocketbase update
```

Les binaires sont stockes sous:

```text
/home/ubuntu/.local/share/pockethost/pocketbase
```

## 11. Verification HTTP

Verifier depuis le serveur:

```bash
curl -I http://127.0.0.1:5174
curl -I http://127.0.0.1:8091/api/health
```

Verifier depuis l'exterieur:

```bash
curl -I https://app.monappli.re/login
curl -I https://app.monappli.re/dashboard
curl -I https://app.monappli.re/api/health
```

Une API protegee doit repondre `401` sans session, c'est normal:

```bash
curl -i https://app.monappli.re/api/instance/<id>/overview
```

## 12. Creation du premier compte

Ouvrir:

```text
https://app.monappli.re/login
```

Creer un compte utilisateur normal depuis l'interface si l'inscription est active.

Pour une utilisation interne, il est preferable que le superadmin gere ensuite les utilisateurs depuis le backoffice.

## 13. DNS des instances

Chaque instance est disponible sur:

```text
https://<subdomain>.monappli.re
```

Le wildcard DNS `*.monappli.re` est donc obligatoire.

Le dashboard utilise aussi:

```text
https://app.monappli.re
```

Ce sous-domaine doit etre reserve au dashboard et ne doit pas etre utilise comme nom d'instance.

## 14. Sauvegardes des instances

La gestion de sauvegardes est integree dans la page instance.

Le backend cree des archives:

```text
<date>-<subdomain>-manual-<instanceId>.tar.gz
```

Elles contiennent:

```text
pb_data
pb_public
pb_migrations
pb_hooks
manifest.json
```

Par defaut, elles sont stockees ici:

```text
/home/ubuntu/.local/share/pockethost/data/backups/instances/<instanceId>
```

Le process de sauvegarde:

1. verifie qu'aucune sauvegarde/restauration n'est deja en cours;
2. eteint l'instance si elle tourne;
3. attend que l'instance soit idle;
4. cree une archive `.tar.gz`;
5. calcule la taille et le checksum SHA256;
6. redemarre l'instance si elle etait active.

### Plafonner les ressources des sauvegardes

Les grosses bases peuvent monopoliser le CPU et le disque pendant la compression. Installer `cpulimit` pour activer un
plafond CPU strict:

```bash
sudo apt-get update
sudo apt-get install -y cpulimit
```

Puis ajouter dans `.env`:

```env
INSTANCE_BACKUP_CPU_LIMIT_PERCENT=50
INSTANCE_BACKUP_GZIP_LEVEL=1
INSTANCE_BACKUP_NICE_LEVEL=19
INSTANCE_BACKUP_IONICE_CLASS=3
```

Effets:

- `INSTANCE_BACKUP_CPU_LIMIT_PERCENT=50` limite la compression a 50 % d'un CPU. Mettre `0` pour desactiver.
- `INSTANCE_BACKUP_GZIP_LEVEL=1` reduit fortement le temps CPU, avec des archives un peu plus grosses.
- `INSTANCE_BACKUP_NICE_LEVEL=19` laisse les autres process passer avant la sauvegarde.
- `INSTANCE_BACKUP_IONICE_CLASS=3` met les lectures/ecritures de sauvegarde en priorite disque idle.

Apres modification:

```bash
pm2 restart mothership --update-env
pm2 save
```

### Importer une grosse archive

La page `Sauvegardes` accepte aussi l'import d'une archive existante en `.zip`, `.tgz` ou `.tar.gz`.
L'archive doit contenir au minimum `pb_data`, ou directement le contenu de `pb_data` a la racine du ZIP
(`data.db`, `auxiliary.db`, fichiers `-wal`/`-shm`, etc.). Si `pb_public`, `pb_migrations` ou `pb_hooks`
sont absents, le restore cree les dossiers manquants automatiquement.

Pour les imports, l'interface distingue la taille compressee de l'archive et la taille source decompressee lue dans
l'index ZIP/TGZ. Exemple: un ZIP de 4,8 Go peut etre affiche comme une sauvegarde source de 20 Go. Prevoir assez
d'espace disque pour l'archive, l'extraction temporaire, et l'ancienne base pendant la restauration.

L'upload navigateur affiche une jauge de progression et peut envoyer de gros fichiers, par exemple 10 Go, si tout le
chemin HTTP l'accepte. Attention: si `app.monappli.re` passe par un proxy qui limite la taille des requetes
(Cloudflare, nginx, load balancer, etc.), l'upload sera coupe avant d'arriver a PocketBase. Dans ce cas, passer le
domaine en DNS only le temps de l'import, utiliser une entree directe vers le serveur, ou deposer le fichier sur le
serveur.

La route d'import direct accepte 12 Gio par defaut. Pour changer cette limite, ajouter dans `.env`:

```env
INSTANCE_BACKUP_UPLOAD_LIMIT_BYTES=12884901888
```

Mettre `0` desactive la limite PocketBase pour cette route directe uniquement; a reserver a un reseau interne ou a un
acces admin protege.

Pour les archives de plus de 500 Mio, l'interface utilise automatiquement un upload par morceaux de 32 Mio avec
plusieurs envois en parallele. Jusqu'a 500 Mio inclus, l'interface garde l'upload direct, plus rapide pour les petites
sauvegardes. Chaque requete multipart reste petite, ce qui passe mieux avec Cloudflare/nginx. La limite serveur par
morceau est de 64 Mio par defaut:

```env
INSTANCE_BACKUP_CHUNK_LIMIT_BYTES=67108864
```

Pour les tres gros fichiers et les connexions instables, le plus fiable reste de deposer le fichier sur le serveur:

```bash
sudo mkdir -p /home/ubuntu/.local/share/pockethost/data/imports
sudo chown -R ubuntu:ubuntu /home/ubuntu/.local/share/pockethost/data/imports
scp backup.zip ubuntu@141.94.92.92:/home/ubuntu/.local/share/pockethost/data/imports/
```

Dans l'interface, utiliser ensuite `Importer serveur` avec:

```text
/home/ubuntu/.local/share/pockethost/data/imports/backup.zip
```

Par securite, seuls les superadmins peuvent importer depuis un chemin serveur, et le fichier doit etre dans
`INSTANCE_IMPORT_ROOT`. Sans variable specifique, ce dossier vaut:

```text
/home/ubuntu/.local/share/pockethost/data/imports
```

Pour changer ce dossier, ajouter dans `.env`:

```env
INSTANCE_IMPORT_ROOT=/chemin/autorise/imports
```

Pour activer un stockage distant S3/R2 pour les sauvegardes planifiees, utiliser l'interface:

1. Aller dans `Administration`.
2. Ouvrir `Stockage S3/R2 des sauvegardes`.
3. Renseigner endpoint, bucket, prefixe, region, access key et secret key.
4. Cliquer `Tester S3/R2`.
5. Cliquer `Enregistrer`.

Les variables `.env` ci-dessous restent supportees comme fallback d'initialisation si aucun parametrage n'est encore
enregistre dans l'interface:

```env
INSTANCE_BACKUP_S3_ENABLED=true
INSTANCE_BACKUP_S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
INSTANCE_BACKUP_S3_BUCKET=nom-du-bucket
INSTANCE_BACKUP_S3_PREFIX=instances
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=auto
```

Installer aussi l'AWS CLI:

```bash
sudo apt install -y awscli
```

Pour configurer l'envoi d'emails, utiliser l'interface:

1. Aller dans `Administration`.
2. Ouvrir `SMTP des emails`.
3. Renseigner hote, port, expediteur, utilisateur, mot de passe et methode d'auth.
4. Cliquer `Tester SMTP` avec une adresse de test.
5. Cliquer `Enregistrer`.

Le test SMTP enregistre la configuration avant l'envoi. Le mot de passe n'est pas renvoye au navigateur; laisser le
champ vide conserve le mot de passe deja enregistre.

Les variables `.env` ci-dessous restent supportees comme fallback d'initialisation si aucun parametrage n'est encore
enregistre dans l'interface:

```env
SMTP_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_AUTH_METHOD=PLAIN
SMTP_TLS=false
SMTP_LOCAL_NAME=monappli.re
SMTP_SENDER_NAME=Gestion PocketBase
SMTP_SENDER_ADDRESS=no-reply@monappli.re
```

Redemarrer `mothership` seulement apres installation de l'AWS CLI ou modification du fallback `.env`. Une modification
faite depuis l'interface est prise en compte sans redemarrage.

```bash
pm2 restart mothership
```

## 15. Sauvegarde serveur complete

En plus des sauvegardes par instance, garder une sauvegarde serveur de:

```text
/home/ubuntu/pockethost/.env
/home/ubuntu/.local/share/pockethost/data
/home/ubuntu/.local/share/pockethost/ssl
/home/ubuntu/.local/share/pockethost/ssh
```

Exemple de script `/home/ubuntu/backup-pockethost.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

DATE="$(date +%Y%m%d-%H%M%S)"
DEST="/home/ubuntu/backups"
SRC_DATA="/home/ubuntu/.local/share/pockethost"
SRC_REPO="/home/ubuntu/pockethost"

mkdir -p "$DEST"

tar -czf "$DEST/pockethost-server-$DATE.tar.gz" \
  -C /home/ubuntu \
  pockethost/.env \
  .local/share/pockethost/data \
  .local/share/pockethost/ssl \
  .local/share/pockethost/ssh

find "$DEST" -name 'pockethost-server-*.tar.gz' -mtime +14 -delete
```

Activer:

```bash
chmod +x /home/ubuntu/backup-pockethost.sh
crontab -e
```

Ajouter:

```cron
15 3 * * * /home/ubuntu/backup-pockethost.sh >> /home/ubuntu/backup-pockethost.log 2>&1
```

Important: pour les bases SQLite tres actives, privilegier les sauvegardes applicatives PocketBase/instance plutot qu'une simple copie disque a chaud.

## 16. Mise a jour du code

Depuis le poste local:

```bash
git status
git add .
git commit -m "Message clair"
git push origin self-host-install-fixes
```

Sur le serveur:

```bash
ssh ubuntu@141.94.92.92
cd /home/ubuntu/pockethost
git pull --ff-only origin self-host-install-fixes
pnpm install --frozen-lockfile
pnpm --filter pockethost-mothership-app build
pnpm --filter @pockethost/dashboard build
pm2 restart mothership dashboard firewall edge-daemon
pm2 status
```

Si les hooks generes ont change apres le build serveur:

```bash
git status --short
```

Les fichiers a rapatrier et committer sont:

```text
packages/pockethost/src/mothership-app/pb_hooks/mothership.js
packages/pockethost/src/mothership-app/pb_hooks/mothership.pb.js
```

## 17. Commandes utiles

Etat PM2:

```bash
pm2 status
```

Logs:

```bash
pm2 logs firewall --lines 100
pm2 logs mothership --lines 100
pm2 logs dashboard --lines 100
pm2 logs edge-daemon --lines 100
```

Redemarrer:

```bash
pm2 restart mothership dashboard
pm2 restart ecosystem.config.cjs
```

Ports ecoutes:

```bash
ss -ltnp
```

Espace disque:

```bash
df -h
du -sh /home/ubuntu/.local/share/pockethost/data/*
```

Verifier Docker:

```bash
docker ps
docker logs <container>
```

Verifier Git:

```bash
git status --short --branch
git log --oneline -5
```

## 18. Depannage

### Le site ne repond pas en HTTPS

Verifier:

```bash
pm2 logs firewall --lines 100
ls -la /home/ubuntu/.local/share/pockethost/ssl
sudo ufw status
curl -I http://127.0.0.1
```

Si l'erreur indique `TLS cert missing`, verifier `tls.key` et `tls.cert`.

### Login impossible

Verifier que le dashboard pointe vers la bonne mothership:

```bash
cat packages/dashboard/.env
pm2 logs dashboard --lines 100
pm2 logs mothership --lines 100
curl -I https://app.monappli.re/api/health
```

### Les instances ne se lancent pas

Verifier:

```bash
pm2 logs edge-daemon --lines 200
docker ps -a
du -sh /home/ubuntu/.local/share/pockethost/data/instances
```

### Port 80/443 refuse

Verifier la capacite Linux sur Node:

```bash
getcap "$(readlink -f "$(command -v node)")"
```

Si vide:

```bash
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f "$(command -v node)")"
pm2 restart firewall edge-ftp
```

### Rate limiter

Le firewall a un rate limiter interne. En mode `NODE_ENV=development`, il est desactive par defaut sauf si:

```env
PH_ENABLE_FIREWALL_RATE_LIMIT=1
```

Apres modification:

```bash
pm2 restart firewall
```

## 19. Checklist finale

- [ ] DNS `app`, `*`, `ftp`, `pockethost-central` pointent vers le serveur.
- [ ] Certificat `tls.key` et `tls.cert` installe.
- [ ] Node 24 installe.
- [ ] Docker actif.
- [ ] PM2 actif et sauvegarde.
- [ ] `.env` serveur present.
- [ ] `packages/dashboard/.env` present.
- [ ] `pnpm install --frozen-lockfile` OK.
- [ ] build mothership OK.
- [ ] build dashboard OK.
- [ ] `pm2 status` online.
- [ ] `https://app.monappli.re/login` accessible.
- [ ] creation d'une instance OK.
- [ ] sauvegarde d'une instance testee.
- [ ] cron de sauvegarde serveur configure.
- [ ] dernier commit pousse sur GitHub.
