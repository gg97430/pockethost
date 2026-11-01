#!/usr/bin/env bash
# Historical installer retired: never upgrade/remove Docker or provision old domains.
set -euo pipefail
cat >&2 <<'MESSAGE'
L'ancien installateur Kolab a été retiré : il ne correspond plus à la plateforme.
Aucune modification n'a été effectuée.
Suivre docs/hosting/nouveau-serveur-kolab.md depuis une révision Git identifiée.
Prérequis : sudo bash packages/pockethost/scripts/install-server-prerequisites.sh
Préparation hébergement : packages/pockethost/scripts/setup-web-hosting.sh
Réception bloquante : packages/pockethost/scripts/check-server-readiness.py --help
MESSAGE
exit 1
