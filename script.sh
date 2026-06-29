#!/usr/bin/env bash
set -Eeuo pipefail

# Fresh Ubuntu installer for the self-hosted Gestion PocketBase stack.
#
# Typical use on a new VPS:
#   DOMAIN=monappli.re \
#   SERVER_IP=141.94.92.92 \
#   ADMIN_EMAIL=admin@monappli.re \
#   ADMIN_PASSWORD='change-me' \
#   bash script.sh
#
# TLS options:
#   TLS_CERT_B64="$(base64 -w0 tls.cert)" TLS_KEY_B64="$(base64 -w0 tls.key)" bash script.sh
#   TLS_CERT_PATH=/root/tls.cert TLS_KEY_PATH=/root/tls.key bash script.sh
#   SELF_SIGNED_TLS=1 bash script.sh

log() {
  printf '\n\033[1;32m==>\033[0m %s\n' "$*"
}

warn() {
  printf '\n\033[1;33mWARN:\033[0m %s\n' "$*" >&2
}

die() {
  printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2
  exit 1
}

as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

q() {
  printf '%q' "$1"
}

run_as_install_user() {
  local command="$1"

  if command -v sudo >/dev/null 2>&1; then
    as_root sudo -H -u "${INSTALL_USER}" bash -lc "${command}"
    return
  fi

  if [[ "$(id -un)" == "${INSTALL_USER}" ]]; then
    bash -lc "${command}"
  elif command -v runuser >/dev/null 2>&1; then
    as_root runuser -u "${INSTALL_USER}" -- bash -lc "${command}"
  else
    die "Impossible d'executer une commande en tant que ${INSTALL_USER}"
  fi
}

read_dotenv_value() {
  local file="$1"
  local key="$2"
  local line

  [[ -f "${file}" ]] || return 1
  line="$(grep -E "^${key}=" "${file}" | tail -n 1 || true)"
  [[ -n "${line}" ]] || return 1

  local value="${line#*=}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "${value}"
}

dotenv_quote() {
  local value="$1"
  value="${value//$'\r'/}"
  value="${value//$'\n'/}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "${value}"
}

write_dotenv_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  local line tmp

  line="${key}=$(dotenv_quote "${value}")"
  tmp="$(mktemp)"

  if [[ -f "${file}" ]]; then
    awk -v key="${key}" -v line="${line}" '
      BEGIN { done = 0 }
      $0 ~ "^" key "=" {
        if (!done) {
          print line
          done = 1
        }
        next
      }
      { print }
      END {
        if (!done) print line
      }
    ' "${file}" >"${tmp}"
  else
    printf '%s\n' "${line}" >"${tmp}"
  fi

  mv "${tmp}" "${file}"
}

random_hex() {
  openssl rand -hex "$1"
}

bool_enabled() {
  [[ "${1:-}" == "1" || "${1:-}" == "true" || "${1:-}" == "yes" ]]
}

DOMAIN="${DOMAIN:-monappli.re}"
APP_SUBDOMAIN="${APP_SUBDOMAIN:-app}"
APP_HOST="${APP_HOST:-${APP_SUBDOMAIN}.${DOMAIN}}"
SERVER_IP="${SERVER_IP:-}"
INSTALL_USER="${INSTALL_USER:-ubuntu}"
REPO_URL="${REPO_URL:-https://github.com/gg97430/pockethost.git}"
BRANCH="${BRANCH:-self-host-install-fixes}"
TIMEZONE="${TIMEZONE:-Indian/Reunion}"
NODE_MAJOR="${NODE_MAJOR:-24}"
PNPM_VERSION="${PNPM_VERSION:-11.6.0}"

FORCE_ENV="${FORCE_ENV:-0}"
RUN_APT_UPGRADE="${RUN_APT_UPGRADE:-0}"
RUN_BUILD="${RUN_BUILD:-1}"
RUN_TYPECHECK="${RUN_TYPECHECK:-1}"
PRELOAD_POCKETBASE="${PRELOAD_POCKETBASE:-1}"
START_PM2="${START_PM2:-1}"
RUN_PM2_STARTUP="${RUN_PM2_STARTUP:-1}"
ENABLE_UFW="${ENABLE_UFW:-1}"
INSTALL_LITESTREAM="${INSTALL_LITESTREAM:-1}"
SELF_SIGNED_TLS="${SELF_SIGNED_TLS:-0}"
BOOTSTRAP_ADMIN_USER="${BOOTSTRAP_ADMIN_USER:-1}"
RESET_ADMIN_PASSWORD="${RESET_ADMIN_PASSWORD:-0}"

ADMIN_EMAIL="${ADMIN_EMAIL:-admin@${DOMAIN}}"
ADMIN_PASSWORD_INPUT="${ADMIN_PASSWORD:-}"
PH_SECRET_INPUT="${PH_SECRET:-}"
DEFAULT_USER_QUOTA="${DEFAULT_USER_QUOTA:-250}"
DEFAULT_SUBSCRIPTION="${DEFAULT_SUBSCRIPTION:-free}"
BOOTSTRAP_USERNAME="${BOOTSTRAP_USERNAME:-${ADMIN_EMAIL%@*}}"
BOOTSTRAP_USERNAME="$(printf '%s' "${BOOTSTRAP_USERNAME}" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
BOOTSTRAP_USERNAME="${BOOTSTRAP_USERNAME:-admin}"

NODE_ENV_VALUE="${NODE_ENV_VALUE:-development}"
PH_DEBUG="${PH_DEBUG:-false}"
MOTHERSHIP_PORT="${MOTHERSHIP_PORT:-8091}"
DAEMON_PORT="${DAEMON_PORT:-3000}"
DAEMON_PB_IDLE_TTL="${DAEMON_PB_IDLE_TTL:-5000}"
MOTHERSHIP_SEMVER="${MOTHERSHIP_SEMVER:-0.39.*}"
PH_AUTO_VERIFY_SIGNUPS="${PH_AUTO_VERIFY_SIGNUPS:-true}"
PH_PUBLIC_SIGNUP_ENABLED="${PH_PUBLIC_SIGNUP_ENABLED:-false}"
PH_SIGNUP_SUBSCRIPTION_QUANTITY="${PH_SIGNUP_SUBSCRIPTION_QUANTITY:-${DEFAULT_USER_QUOTA}}"

PH_FTP_PORT="${PH_FTP_PORT:-21}"
PH_SFTP_PORT="${PH_SFTP_PORT:-2222}"
PH_FTP_PASV_PORT_MIN="${PH_FTP_PASV_PORT_MIN:-10000}"
PH_FTP_PASV_PORT_MAX="${PH_FTP_PASV_PORT_MAX:-20000}"

INSTANCE_BACKUP_CPU_LIMIT_PERCENT="${INSTANCE_BACKUP_CPU_LIMIT_PERCENT:-50}"
INSTANCE_RESTORE_CPU_LIMIT_PERCENT="${INSTANCE_RESTORE_CPU_LIMIT_PERCENT:-50}"
INSTANCE_BACKUP_GZIP_LEVEL="${INSTANCE_BACKUP_GZIP_LEVEL:-1}"
INSTANCE_BACKUP_NICE_LEVEL="${INSTANCE_BACKUP_NICE_LEVEL:-19}"
INSTANCE_RESTORE_NICE_LEVEL="${INSTANCE_RESTORE_NICE_LEVEL:-19}"
INSTANCE_BACKUP_IONICE_CLASS="${INSTANCE_BACKUP_IONICE_CLASS:-3}"
INSTANCE_RESTORE_IONICE_CLASS="${INSTANCE_RESTORE_IONICE_CLASS:-3}"
INSTANCE_BACKUP_IONICE_PRIORITY="${INSTANCE_BACKUP_IONICE_PRIORITY:-7}"
INSTANCE_RESTORE_IONICE_PRIORITY="${INSTANCE_RESTORE_IONICE_PRIORITY:-7}"
INSTANCE_BACKUP_UPLOAD_LIMIT_BYTES="${INSTANCE_BACKUP_UPLOAD_LIMIT_BYTES:-12884901888}"
INSTANCE_BACKUP_CHUNK_LIMIT_BYTES="${INSTANCE_BACKUP_CHUNK_LIMIT_BYTES:-67108864}"

ensure_install_user() {
  if id "${INSTALL_USER}" >/dev/null 2>&1; then
    return
  fi

  [[ "${EUID}" -eq 0 ]] || die "L'utilisateur ${INSTALL_USER} n'existe pas. Lance le script en root, ou cree l'utilisateur avant."
  log "Creation de l'utilisateur ${INSTALL_USER}"
  useradd -m -s /bin/bash "${INSTALL_USER}"
  usermod -aG sudo "${INSTALL_USER}" || true
}

install_system_packages() {
  log "Installation des paquets systeme"
  export DEBIAN_FRONTEND=noninteractive
  as_root apt-get update
  if bool_enabled "${RUN_APT_UPGRADE}"; then
    as_root apt-get upgrade -y
  fi
  as_root apt-get install -y \
    awscli \
    build-essential \
    ca-certificates \
    cpulimit \
    curl \
    docker.io \
    git \
    gnupg \
    htop \
    jq \
    libcap2-bin \
    openssl \
    rsync \
    sqlite3 \
    sudo \
    tar \
    ufw \
    unzip \
    util-linux \
    xz-utils
}

install_node() {
  local current_major=""
  if command -v node >/dev/null 2>&1; then
    current_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
  fi

  if [[ "${current_major}" != "${NODE_MAJOR}" ]]; then
    log "Installation de Node ${NODE_MAJOR}"
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" -o /tmp/nodesource_setup.sh
    as_root bash /tmp/nodesource_setup.sh
    as_root apt-get install -y nodejs
  else
    log "Node ${NODE_MAJOR} deja installe"
  fi

  log "Installation de pnpm ${PNPM_VERSION} et PM2"
  as_root npm install -g "pnpm@${PNPM_VERSION}" pm2

  local node_path
  node_path="$(readlink -f "$(command -v node)")"
  as_root setcap 'cap_net_bind_service=+ep' "${node_path}" || warn "Impossible d'appliquer setcap sur ${node_path}"
}

install_litestream() {
  bool_enabled "${INSTALL_LITESTREAM}" || return 0
  if command -v litestream >/dev/null 2>&1; then
    log "Litestream deja installe"
    return
  fi

  log "Installation de Litestream"
  local arch litestream_arch url
  arch="$(dpkg --print-architecture)"
  case "${arch}" in
    amd64) litestream_arch="x86_64" ;;
    arm64) litestream_arch="arm64" ;;
    armhf) litestream_arch="armv7" ;;
    *) warn "Architecture ${arch} non geree pour Litestream"; return ;;
  esac

  url="$(
    curl -fsSL https://api.github.com/repos/benbjohnson/litestream/releases/latest |
      jq -r --arg suffix "linux-${litestream_arch}.deb" '.assets[] | select(.name | endswith($suffix)) | .browser_download_url' |
      head -n 1
  )"
  [[ -n "${url}" ]] || die "Impossible de trouver le paquet Litestream pour ${arch}"

  curl -fL "${url}" -o /tmp/litestream.deb
  as_root dpkg -i /tmp/litestream.deb || as_root apt-get install -f -y
}

configure_system() {
  log "Configuration systeme"
  as_root systemctl enable --now docker
  as_root usermod -aG docker "${INSTALL_USER}"
  as_root timedatectl set-timezone "${TIMEZONE}"

  if bool_enabled "${ENABLE_UFW}"; then
    as_root ufw allow OpenSSH
    as_root ufw allow 80/tcp
    as_root ufw allow 443/tcp
    as_root ufw allow "${PH_FTP_PORT}/tcp"
    as_root ufw allow "${PH_SFTP_PORT}/tcp"
    as_root ufw allow "${PH_FTP_PASV_PORT_MIN}:${PH_FTP_PASV_PORT_MAX}/tcp"
    as_root ufw --force enable
  fi
}

detect_server_ip() {
  if [[ -n "${SERVER_IP}" ]]; then
    return
  fi

  SERVER_IP="$(curl -fsS https://api.ipify.org 2>/dev/null || true)"
  if [[ -z "${SERVER_IP}" ]]; then
    SERVER_IP="$(hostname -I | awk '{print $1}')"
  fi
  [[ -n "${SERVER_IP}" ]] || die "Impossible de detecter SERVER_IP. Relance avec SERVER_IP=x.x.x.x"
}

prepare_directories() {
  log "Preparation des repertoires"
  as_root mkdir -p \
    "${PH_HOME}/ssl" \
    "${PH_HOME}/data" \
    "${PH_HOME}/backups/instances" \
    "${PH_HOME}/imports" \
    "${PH_HOME}/ssh" \
    "${INSTALL_DIR}"
  as_root chown -R "${INSTALL_USER}:${INSTALL_USER}" "${PH_HOME}" "${INSTALL_DIR}"
}

install_tls() {
  log "Configuration TLS"
  local cert_file="${PH_HOME}/ssl/tls.cert"
  local key_file="${PH_HOME}/ssl/tls.key"

  if [[ -n "${TLS_CERT_B64:-}" && -n "${TLS_KEY_B64:-}" ]]; then
    printf '%s' "${TLS_CERT_B64}" | base64 -d >"${cert_file}"
    printf '%s' "${TLS_KEY_B64}" | base64 -d >"${key_file}"
  elif [[ -n "${TLS_CERT_PATH:-}" && -n "${TLS_KEY_PATH:-}" ]]; then
    cp "${TLS_CERT_PATH}" "${cert_file}"
    cp "${TLS_KEY_PATH}" "${key_file}"
  elif [[ -f "${cert_file}" && -f "${key_file}" ]]; then
    log "Certificat TLS existant conserve"
  elif bool_enabled "${SELF_SIGNED_TLS}"; then
    warn "Generation d'un certificat self-signed. A remplacer par un certificat Cloudflare Origin en production."
    openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
      -keyout "${key_file}" \
      -out "${cert_file}" \
      -subj "/CN=${DOMAIN}" \
      -addext "subjectAltName=DNS:${DOMAIN},DNS:*.${DOMAIN},DNS:${APP_HOST}"
  else
    warn "Aucun certificat TLS fourni. Le firewall ne servira pas HTTPS tant que ${cert_file} et ${key_file} manquent."
    return
  fi

  as_root chown -R "${INSTALL_USER}:${INSTALL_USER}" "${PH_HOME}/ssl"
  chmod 600 "${key_file}"
  chmod 644 "${cert_file}"
}

clone_or_update_repo() {
  log "Recuperation du projet"
  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    run_as_install_user "cd $(q "${INSTALL_DIR}") && git fetch origin $(q "${BRANCH}") && git checkout $(q "${BRANCH}") && git pull --ff-only origin $(q "${BRANCH}")"
  else
    run_as_install_user "git clone -b $(q "${BRANCH}") $(q "${REPO_URL}") $(q "${INSTALL_DIR}")"
  fi
}

resolve_secrets() {
  local env_file="${INSTALL_DIR}/.env"
  local existing_admin_password=""
  local existing_secret=""

  if [[ -f "${env_file}" && "${FORCE_ENV}" != "1" ]]; then
    existing_admin_password="$(read_dotenv_value "${env_file}" MOTHERSHIP_ADMIN_PASSWORD || true)"
    existing_secret="$(read_dotenv_value "${env_file}" PH_SECRET || true)"
  fi

  if [[ -n "${existing_admin_password}" ]]; then
    ADMIN_PASSWORD="${existing_admin_password}"
  elif [[ -n "${ADMIN_PASSWORD_INPUT}" ]]; then
    ADMIN_PASSWORD="${ADMIN_PASSWORD_INPUT}"
  else
    ADMIN_PASSWORD="$(random_hex 18)"
  fi

  if [[ -n "${existing_secret}" ]]; then
    PH_SECRET="${existing_secret}"
  elif [[ -n "${PH_SECRET_INPUT}" ]]; then
    PH_SECRET="${PH_SECRET_INPUT}"
  else
    PH_SECRET="$(random_hex 32)"
  fi
}

write_env_files() {
  local env_file="${INSTALL_DIR}/.env"
  local dashboard_env="${INSTALL_DIR}/packages/dashboard/.env"

  if [[ -f "${env_file}" && "${FORCE_ENV}" != "1" ]]; then
    log ".env existant conserve (${env_file})"
  else
    log "Ecriture de ${env_file}"
    cat >"${env_file}" <<EOF
NODE_ENV=${NODE_ENV_VALUE}
PH_DEBUG=${PH_DEBUG}

APEX_DOMAIN=${DOMAIN}
HTTP_PROTOCOL=https:
APP_URL=https://${APP_HOST}
BLOG_URL=https://${APP_HOST}
MOTHERSHIP_URL=http://127.0.0.1:${MOTHERSHIP_PORT}

PH_SECRET=${PH_SECRET}
MOTHERSHIP_ADMIN_USERNAME=${ADMIN_EMAIL}
MOTHERSHIP_ADMIN_PASSWORD=${ADMIN_PASSWORD}
TEST_EMAIL=${ADMIN_EMAIL}
PH_SERVER_TIMEZONE=${TIMEZONE}

DAEMON_PORT=${DAEMON_PORT}
MOTHERSHIP_PORT=${MOTHERSHIP_PORT}
DAEMON_PB_IDLE_TTL=${DAEMON_PB_IDLE_TTL}

PH_HOME=${PH_HOME}
DATA_ROOT=${DATA_ROOT}

PH_FTP_PORT=${PH_FTP_PORT}
PH_SFTP_PORT=${PH_SFTP_PORT}
PH_FTP_PASV_IP=${SERVER_IP}
PH_FTP_PASV_PORT_MIN=${PH_FTP_PASV_PORT_MIN}
PH_FTP_PASV_PORT_MAX=${PH_FTP_PASV_PORT_MAX}

MOTHERSHIP_SEMVER=${MOTHERSHIP_SEMVER}
PH_AUTO_VERIFY_SIGNUPS=${PH_AUTO_VERIFY_SIGNUPS}
PH_PUBLIC_SIGNUP_ENABLED=${PH_PUBLIC_SIGNUP_ENABLED}
PH_SIGNUP_SUBSCRIPTION_QUANTITY=${PH_SIGNUP_SUBSCRIPTION_QUANTITY}
PH_ENABLE_FIREWALL_RATE_LIMIT=1

INSTANCE_BACKUP_ROOT=${PH_HOME}/backups/instances
INSTANCE_BACKUP_CPU_LIMIT_PERCENT=${INSTANCE_BACKUP_CPU_LIMIT_PERCENT}
INSTANCE_RESTORE_CPU_LIMIT_PERCENT=${INSTANCE_RESTORE_CPU_LIMIT_PERCENT}
INSTANCE_BACKUP_GZIP_LEVEL=${INSTANCE_BACKUP_GZIP_LEVEL}
INSTANCE_BACKUP_NICE_LEVEL=${INSTANCE_BACKUP_NICE_LEVEL}
INSTANCE_RESTORE_NICE_LEVEL=${INSTANCE_RESTORE_NICE_LEVEL}
INSTANCE_BACKUP_IONICE_CLASS=${INSTANCE_BACKUP_IONICE_CLASS}
INSTANCE_RESTORE_IONICE_CLASS=${INSTANCE_RESTORE_IONICE_CLASS}
INSTANCE_BACKUP_IONICE_PRIORITY=${INSTANCE_BACKUP_IONICE_PRIORITY}
INSTANCE_RESTORE_IONICE_PRIORITY=${INSTANCE_RESTORE_IONICE_PRIORITY}
INSTANCE_BACKUP_UPLOAD_LIMIT_BYTES=${INSTANCE_BACKUP_UPLOAD_LIMIT_BYTES}
INSTANCE_BACKUP_CHUNK_LIMIT_BYTES=${INSTANCE_BACKUP_CHUNK_LIMIT_BYTES}

INSTANCE_BACKUP_S3_ENABLED=${INSTANCE_BACKUP_S3_ENABLED:-false}
INSTANCE_BACKUP_S3_ENDPOINT=${INSTANCE_BACKUP_S3_ENDPOINT:-}
INSTANCE_BACKUP_S3_BUCKET=${INSTANCE_BACKUP_S3_BUCKET:-}
INSTANCE_BACKUP_S3_PREFIX=${INSTANCE_BACKUP_S3_PREFIX:-instances}
AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-auto}
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-}

SMTP_ENABLED=${SMTP_ENABLED:-false}
SMTP_HOST=${SMTP_HOST:-}
SMTP_PORT=${SMTP_PORT:-587}
SMTP_USERNAME=${SMTP_USERNAME:-}
SMTP_PASSWORD=${SMTP_PASSWORD:-}
SMTP_AUTH_METHOD=${SMTP_AUTH_METHOD:-PLAIN}
SMTP_TLS=${SMTP_TLS:-false}
SMTP_LOCAL_NAME=${SMTP_LOCAL_NAME:-}
SMTP_SENDER_NAME="${SMTP_SENDER_NAME:-Gestion PocketBase}"
SMTP_SENDER_ADDRESS=${SMTP_SENDER_ADDRESS:-${ADMIN_EMAIL}}
PH_SUPPORT_EMAIL=${PH_SUPPORT_EMAIL:-${ADMIN_EMAIL}}

MOTHERSHIP_CLOUDFLARE_API_TOKEN=${MOTHERSHIP_CLOUDFLARE_API_TOKEN:-}
MOTHERSHIP_CLOUDFLARE_ZONE_ID=${MOTHERSHIP_CLOUDFLARE_ZONE_ID:-}
MOTHERSHIP_CLOUDFLARE_ACCOUNT_ID=${MOTHERSHIP_CLOUDFLARE_ACCOUNT_ID:-}
EOF
  fi

  write_dotenv_value "${env_file}" PH_SECRET "${PH_SECRET}"
  write_dotenv_value "${env_file}" MOTHERSHIP_ADMIN_USERNAME "${ADMIN_EMAIL}"
  write_dotenv_value "${env_file}" MOTHERSHIP_ADMIN_PASSWORD "${ADMIN_PASSWORD}"
  write_dotenv_value "${env_file}" TEST_EMAIL "${ADMIN_EMAIL}"

  log "Ecriture de ${dashboard_env}"
  cat >"${dashboard_env}" <<EOF
PUBLIC_APEX_DOMAIN=${DOMAIN}
PUBLIC_APP_URL=https://${APP_HOST}
PUBLIC_MOTHERSHIP_URL=https://${APP_HOST}
EOF

  as_root chown "${INSTALL_USER}:${INSTALL_USER}" "${env_file}" "${dashboard_env}"
  chmod 600 "${env_file}"
}

write_credentials_note() {
  local credentials_file="${CREDENTIALS_FILE:-${PH_HOME}/install-credentials.txt}"
  if [[ -f "${credentials_file}" && "${FORCE_ENV}" != "1" ]]; then
    return
  fi

  cat >"${credentials_file}" <<EOF
Gestion PocketBase install

Dashboard: https://${APP_HOST}/login
PocketBase admin: https://${APP_HOST}/_/

Admin email: ${ADMIN_EMAIL}
Admin password: ${ADMIN_PASSWORD}

Project dir: ${INSTALL_DIR}
Data dir: ${PH_HOME}
Created: $(date -Is)
EOF
  chmod 600 "${credentials_file}"
  as_root chown "${INSTALL_USER}:${INSTALL_USER}" "${credentials_file}"
}

install_project_dependencies() {
  log "Installation des dependances JS"
  run_as_install_user "cd $(q "${INSTALL_DIR}") && pnpm install --frozen-lockfile"
}

build_project() {
  bool_enabled "${RUN_BUILD}" || return 0

  if bool_enabled "${RUN_TYPECHECK}"; then
    log "Verification TypeScript"
    run_as_install_user "cd $(q "${INSTALL_DIR}") && pnpm --filter pockethost check:types"
  fi

  log "Build mothership hooks"
  run_as_install_user "cd $(q "${INSTALL_DIR}") && pnpm --filter pockethost-mothership-app build"

  log "Build dashboard"
  run_as_install_user "cd $(q "${INSTALL_DIR}") && pnpm --filter @pockethost/dashboard build"
}

preload_pocketbase_binaries() {
  bool_enabled "${PRELOAD_POCKETBASE}" || return 0

  log "Telechargement des binaires PocketBase"
  run_as_install_user "cd $(q "${INSTALL_DIR}") && pnpm prod:cli pocketbase update"
}

start_pm2_stack() {
  bool_enabled "${START_PM2}" || return 0

  log "Demarrage PM2"
  run_as_install_user "cd $(q "${INSTALL_DIR}") && pm2 delete firewall dashboard edge-daemon edge-ftp edge-sftp mothership pocketbase-update health-check edge-vacuum edge-purge-orphans >/dev/null 2>&1 || true"
  run_as_install_user "cd $(q "${INSTALL_DIR}") && pm2 start ecosystem.config.cjs"
  run_as_install_user "pm2 save"

  if bool_enabled "${RUN_PM2_STARTUP}"; then
    as_root env PATH="${PATH}" pm2 startup systemd -u "${INSTALL_USER}" --hp "${INSTALL_HOME}" >/dev/null || warn "pm2 startup a echoue; relance manuellement: sudo pm2 startup systemd -u ${INSTALL_USER} --hp ${INSTALL_HOME}"
  fi
}

wait_for_mothership() {
  bool_enabled "${START_PM2}" || return 0
  local url="http://127.0.0.1:${MOTHERSHIP_PORT}/api/health"
  log "Attente de la mothership (${url})"

  for _ in $(seq 1 90); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return
    fi
    sleep 2
  done

  die "La mothership ne repond pas. Consulte: sudo -u ${INSTALL_USER} pm2 logs mothership"
}

matches_pocketbase_range() {
  local version="${1#v}"
  local range="${2#v}"
  local prefix

  if [[ "${range}" == *"*" ]]; then
    prefix="${range%\*}"
    [[ "${version}" == "${prefix}"* ]]
    return
  fi

  if [[ "${range}" == *".x" ]]; then
    prefix="${range%.x}."
    [[ "${version}" == "${prefix}"* ]]
    return
  fi

  [[ "${version}" == "${range}" ]]
}

resolve_mothership_pocketbase_binary() {
  local root="${PH_HOME}/pocketbase"
  local candidate version

  [[ -d "${root}" ]] || return 1

  while IFS= read -r candidate; do
    version="$(basename "$(dirname "$(dirname "${candidate}")")")"
    if matches_pocketbase_range "${version}" "${MOTHERSHIP_SEMVER}"; then
      printf '%s %s\n' "${version}" "${candidate}"
    fi
  done < <(find "${root}" -mindepth 3 -maxdepth 3 -type f -name pocketbase 2>/dev/null) |
    sort -Vr |
    head -n 1 |
    cut -d' ' -f2-
}

pb_upsert_superuser_cli() {
  local bin
  bin="$(resolve_mothership_pocketbase_binary || true)"
  [[ -n "${bin}" ]] || die "Aucun binaire PocketBase local ne correspond a ${MOTHERSHIP_SEMVER}. Relance avec PRELOAD_POCKETBASE=1."

  log "Creation/mise a jour du superuser PocketBase via CLI"

  if bool_enabled "${START_PM2}"; then
    run_as_install_user "pm2 stop mothership >/dev/null 2>&1 || true"
  fi

  if ! run_as_install_user "cd $(q "${INSTALL_DIR}") && PH_SECRET=$(q "${PH_SECRET}") $(q "${bin}") --dir $(q "${DATA_ROOT}/mothership/pb_data") --encryptionEnv PH_SECRET superuser upsert $(q "${ADMIN_EMAIL}") $(q "${ADMIN_PASSWORD}")"; then
    if bool_enabled "${START_PM2}"; then
      run_as_install_user "pm2 restart mothership >/dev/null 2>&1 || true"
    fi
    die "Impossible de creer/mettre a jour le superuser PocketBase ${ADMIN_EMAIL}"
  fi

  if bool_enabled "${START_PM2}"; then
    run_as_install_user "pm2 restart mothership >/dev/null"
    wait_for_mothership
  fi
}

pb_auth_superuser() {
  local base="$1"
  local payload response token

  payload="$(jq -cn --arg identity "${ADMIN_EMAIL}" --arg password "${ADMIN_PASSWORD}" '{identity:$identity,password:$password}')"

  response="$(curl -fsS -H 'Content-Type: application/json' -d "${payload}" "${base}/api/collections/_superusers/auth-with-password" 2>/dev/null || true)"
  token="$(printf '%s' "${response}" | jq -r '.token // empty' 2>/dev/null || true)"
  if [[ -n "${token}" ]]; then
    printf '%s' "${token}"
    return
  fi

  response="$(curl -fsS -H 'Content-Type: application/json' -d "${payload}" "${base}/api/admins/auth-with-password" 2>/dev/null || true)"
  token="$(printf '%s' "${response}" | jq -r '.token // empty' 2>/dev/null || true)"
  if [[ -n "${token}" ]]; then
    printf '%s' "${token}"
  fi
}

pb_create_superuser() {
  local base="$1"
  local payload response id

  payload="$(jq -cn --arg email "${ADMIN_EMAIL}" --arg password "${ADMIN_PASSWORD}" '{email:$email,password:$password,passwordConfirm:$password}')"

  response="$(curl -fsS -H 'Content-Type: application/json' -d "${payload}" "${base}/api/collections/_superusers/records" 2>/dev/null || true)"
  id="$(printf '%s' "${response}" | jq -r '.id // empty' 2>/dev/null || true)"
  if [[ -n "${id}" ]]; then
    return
  fi

  curl -fsS -H 'Content-Type: application/json' -d "${payload}" "${base}/api/admins" >/dev/null 2>&1 || true
}

ensure_superuser_token() {
  local base="$1"
  local token

  token="$(pb_auth_superuser "${base}")"
  if [[ -n "${token}" ]]; then
    printf '%s' "${token}"
    return
  fi

  pb_upsert_superuser_cli

  for _ in $(seq 1 30); do
    token="$(pb_auth_superuser "${base}")"
    if [[ -n "${token}" ]]; then
      printf '%s' "${token}"
      return
    fi
    sleep 2
  done

  die "Impossible d'authentifier le superuser PocketBase ${ADMIN_EMAIL}"
}

ensure_dashboard_admin() {
  bool_enabled "${BOOTSTRAP_ADMIN_USER}" || return 0
  bool_enabled "${START_PM2}" || return 0

  [[ "${DEFAULT_USER_QUOTA}" =~ ^[0-9]+$ ]] || die "DEFAULT_USER_QUOTA doit etre un entier"

  local base="http://127.0.0.1:${MOTHERSHIP_PORT}"
  local token records user_id payload response created_id create_username
  token="$(ensure_superuser_token "${base}")"

  log "Creation/mise a jour du premier superadmin dashboard"
  records="$(
    curl -fsS \
      -H "Authorization: Bearer ${token}" \
      --get \
      --data-urlencode "filter=email=\"${ADMIN_EMAIL}\"" \
      "${base}/api/collections/users/records" 2>/dev/null || true
  )"
  user_id="$(printf '%s' "${records}" | jq -r '.items[0].id // empty' 2>/dev/null || true)"

  if [[ -n "${user_id}" ]]; then
    payload="$(jq -cn --arg subscription "${DEFAULT_SUBSCRIPTION}" --argjson quota "${DEFAULT_USER_QUOTA}" '{verified:true,superAdmin:true,subscription:$subscription,subscription_quantity:$quota}')"
    if bool_enabled "${RESET_ADMIN_PASSWORD}"; then
      payload="$(printf '%s' "${payload}" | jq --arg password "${ADMIN_PASSWORD}" '. + {password:$password,passwordConfirm:$password}')"
    fi
    curl -fsS -X PATCH -H 'Content-Type: application/json' -H "Authorization: Bearer ${token}" -d "${payload}" "${base}/api/collections/users/records/${user_id}" >/dev/null
    return
  fi

  create_username="${BOOTSTRAP_USERNAME}"
  payload="$(jq -cn --arg username "${create_username}" --arg email "${ADMIN_EMAIL}" --arg password "${ADMIN_PASSWORD}" --arg subscription "${DEFAULT_SUBSCRIPTION}" --argjson quota "${DEFAULT_USER_QUOTA}" '{username:$username,email:$email,password:$password,passwordConfirm:$password,verified:true,superAdmin:true,subscription:$subscription,subscription_quantity:$quota}')"
  response="$(curl -fsS -H 'Content-Type: application/json' -H "Authorization: Bearer ${token}" -d "${payload}" "${base}/api/collections/users/records" 2>/dev/null || true)"
  created_id="$(printf '%s' "${response}" | jq -r '.id // empty' 2>/dev/null || true)"
  if [[ -n "${created_id}" ]]; then
    return
  fi

  create_username="${BOOTSTRAP_USERNAME}$(random_hex 3)"
  payload="$(jq -cn --arg username "${create_username}" --arg email "${ADMIN_EMAIL}" --arg password "${ADMIN_PASSWORD}" --arg subscription "${DEFAULT_SUBSCRIPTION}" --argjson quota "${DEFAULT_USER_QUOTA}" '{username:$username,email:$email,password:$password,passwordConfirm:$password,verified:true,superAdmin:true,subscription:$subscription,subscription_quantity:$quota}')"
  response="$(curl -fsS -H 'Content-Type: application/json' -H "Authorization: Bearer ${token}" -d "${payload}" "${base}/api/collections/users/records" 2>/dev/null || true)"
  created_id="$(printf '%s' "${response}" | jq -r '.id // empty' 2>/dev/null || true)"
  [[ -n "${created_id}" ]] || die "Impossible de creer l'utilisateur dashboard ${ADMIN_EMAIL}: ${response}"
}

print_summary() {
  cat <<EOF

Installation terminee.

Dashboard:
  https://${APP_HOST}/login

Compte initial:
  email: ${ADMIN_EMAIL}
  password: voir ${CREDENTIALS_FILE:-${PH_HOME}/install-credentials.txt}

Fichiers importants:
  projet: ${INSTALL_DIR}
  donnees: ${PH_HOME}
  env: ${INSTALL_DIR}/.env
  credentials: ${CREDENTIALS_FILE:-${PH_HOME}/install-credentials.txt}

Commandes utiles:
  sudo -u ${INSTALL_USER} pm2 status
  sudo -u ${INSTALL_USER} pm2 logs
  sudo -u ${INSTALL_USER} bash -lc 'cd ${INSTALL_DIR} && git pull --ff-only && pnpm install --frozen-lockfile && pnpm --filter pockethost-mothership-app build && pnpm --filter @pockethost/dashboard build && pm2 restart all'

DNS requis:
  A ${APP_HOST} ${SERVER_IP}
  A *.${DOMAIN} ${SERVER_IP}
  A ftp.${DOMAIN} ${SERVER_IP}
EOF
}

main() {
  ensure_install_user
  INSTALL_HOME="$(getent passwd "${INSTALL_USER}" | cut -d: -f6)"
  [[ -n "${INSTALL_HOME}" ]] || die "Impossible de trouver le home de ${INSTALL_USER}"

  INSTALL_DIR="${INSTALL_DIR:-${INSTALL_HOME}/pockethost}"
  PH_HOME="${PH_HOME:-${INSTALL_HOME}/.local/share/pockethost}"
  DATA_ROOT="${DATA_ROOT:-${PH_HOME}/data}"

  detect_server_ip
  install_system_packages
  install_node
  install_litestream
  configure_system
  prepare_directories
  install_tls
  clone_or_update_repo
  resolve_secrets
  write_env_files
  write_credentials_note
  install_project_dependencies
  build_project
  preload_pocketbase_binaries
  start_pm2_stack
  wait_for_mothership
  ensure_dashboard_admin
  print_summary
}

main "$@"
