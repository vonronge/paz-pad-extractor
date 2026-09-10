#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
export PYTHONPATH="$HERE"
exec python3 -W ignore::RuntimeWarning -m ui.ui_font "$@"
