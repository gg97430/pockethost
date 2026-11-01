#!/usr/bin/env bash
# Compatibility entrypoint; pins have one source of truth in web_app_railpack.py.
set -euo pipefail
if (($# == 0)); then
  : "${RAILPACK_BINARY_SOURCE:?Supply the uncompressed pinned Railpack binary.}"
  : "${MISE_BINARY_SOURCE:?Supply the uncompressed pinned mise binary.}"
  set -- --railpack "$RAILPACK_BINARY_SOURCE" --mise "$MISE_BINARY_SOURCE"
fi
exec python3 "$(dirname "$0")/install-railpack.py" "$@"
