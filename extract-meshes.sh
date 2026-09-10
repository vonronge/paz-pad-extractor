#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
export PYTHONPATH="$HERE"
exec python3 -m mesh.extract_meshes "$@"
