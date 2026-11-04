#!/usr/bin/env bash
# Starts Colab Studio in the foreground (install.sh must have run once). Ctrl+C stops it and
# releases the GPU if one is running.
set -Eeuo pipefail
cd "$(dirname "$0")/server"
[[ -x ../.venv/bin/python ]] || { echo "Not installed yet: run ./install.sh first" >&2; exit 1; }
[[ -f web/dist/index.html ]] || { echo "Web UI is missing: run ./install.sh again" >&2; exit 1; }
export PATH="$HOME/.local/bin:$PATH"
exec ../.venv/bin/python app.py
