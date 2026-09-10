#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
REPO="$(cd "$HERE/.." && pwd)"
CLIENT="${BDO_CLIENT_ROOT:-$REPO/BDO Client}"
export PYTHONPATH="$HERE"
exec python3 -W ignore::RuntimeWarning -m audio.wwise_extract --client-root "$CLIENT" "$@" 2>/dev/null
