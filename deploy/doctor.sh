#!/usr/bin/env bash
# Checks the installation and says what is missing. Changes nothing. Exit code 1 if something failed.
#   ./install.sh --doctor        (or ./deploy/doctor.sh)
set -Eeuo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
fails=0
warns=0
ok() { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; warns=$((warns + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fails=$((fails + 1)); }
envget() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

echo "Colab Studio $(cat VERSION) in $(pwd)"

echo "System"
if [[ $(uname -s) == Linux ]]; then ok "Linux"; else fail "Colab Studio runs on Linux (on Windows use WSL2 or Docker)"; fi
for cmd in curl ssh ssh-keygen tar python3; do
  if command -v "${cmd}" >/dev/null; then ok "${cmd}"; else fail "${cmd} is missing: sudo apt install curl openssh-client tar python3"; fi
done

echo "Tools installed by install.sh"
if command -v uv >/dev/null; then ok "$(uv --version)"; else fail "uv is missing: run ./install.sh"; fi
colab=${COLAB_BIN:-$(envget COLAB_BIN)}
colab=${colab:-$(command -v colab || true)}
if [[ -n ${colab} && -x ${colab} ]]; then ok "colab CLI: ${colab}"; else fail "colab CLI not found: run ./install.sh (or set COLAB_BIN in .env)"; fi
if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import aiohttp' 2>/dev/null; then
  ok "Python environment (.venv, $(.venv/bin/python -c 'import platform; print(platform.python_version())'))"
else
  fail "Python environment is missing or broken: run ./install.sh"
fi
if [[ -f server/web/dist/index.html ]]; then
  ok "web UI is built"
else
  fail "web UI is missing: run ./install.sh (builds it with Node.js 20+, or downloads the prebuilt release)"
fi
if command -v node >/dev/null; then
  node_major=$(node -p 'process.versions.node.split(".")[0]')
  if (( node_major >= 22 )); then ok "Node.js $(node --version) (also enough for the MCP server)"
  elif (( node_major >= 20 )); then ok "Node.js $(node --version) (builds the UI; the MCP server needs 22+)"
  else warn "Node.js $(node --version) is too old to build the UI (20+) or run the MCP server (22+)"; fi
else
  warn "Node.js not found: fine for the studio (prebuilt UI), needed only to build the UI or run the MCP server"
fi

echo "Settings (.env)"
if [[ ! -f .env ]]; then
  fail ".env is missing: run ./install.sh"
  host=127.0.0.1
  port=8190
else
  ok ".env exists"
  host=$(envget STUDIO_HOST)
  host=${host:-127.0.0.1}
  port=$(envget STUDIO_PORT)
  port=${port:-8190}
  [[ ${host} == 127.0.0.1 || ${host} == 0.0.0.0 ]] && ok "STUDIO_HOST=${host}" || warn "STUDIO_HOST=${host} (usually 127.0.0.1 or 0.0.0.0)"
  if [[ ${host} == 0.0.0.0 ]]; then
    warn "anyone who can reach this machine by IP can start the GPU and spend your compute units: use a firewall, an SSH tunnel or deploy/setup-nginx.sh"
  fi
  if [[ ${port} =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )); then ok "STUDIO_PORT=${port}"; else fail "STUDIO_PORT=${port} is not a port number"; port=8190; fi
  [[ -n $(envget HF_TOKEN) ]] && ok "HF_TOKEN is set" || warn "HF_TOKEN is empty: model downloads work but are slower and can hit rate limits"
  [[ $(stat -c %a .env) == 600 ]] || warn ".env is readable by other users (it may hold your token): chmod 600 .env"
fi

echo "Port ${port}"
runtime=$(curl -fsS --max-time 3 "http://127.0.0.1:${port}/api/runtime" 2>/dev/null || true)
if [[ -n ${runtime} ]]; then
  phase=$(printf '%s' "${runtime}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("phase","?"))' 2>/dev/null || echo "?")
  ok "the studio is running here (GPU phase: ${phase})"
elif python3 - "${host}" "${port}" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.bind((sys.argv[1], int(sys.argv[2])))
PY
then
  ok "free (the studio is not running: start it with ./run.sh or the service)"
else
  fail "port ${port} is used by something else: pick another with ./install.sh --port <number>"
fi

echo "Google sign-in (Colab CLI)"
if [[ -f ~/.config/colab-cli/token.json ]]; then
  ok "signed in"
else
  warn "not signed in: run 'colab usage' and follow the link (the studio shows the same hint when you start the GPU)"
fi

if [[ -f ~/.config/systemd/user/colab-studio.service ]]; then
  echo "systemd user service"
  if systemctl --user is-active --quiet colab-studio 2>/dev/null; then ok "colab-studio is running"; else warn "colab-studio is installed but not running: systemctl --user start colab-studio"; fi
  systemctl --user is-enabled --quiet colab-studio 2>/dev/null && ok "starts on boot" || warn "not enabled: systemctl --user enable colab-studio"
  me=${USER:-$(id -un)}
  if [[ $(loginctl show-user "${me}" -p Linger --value 2>/dev/null || true) == yes ]]; then
    ok "lingering is on (keeps running after you log out)"
  else
    warn "lingering is off, the service stops when you log out: sudo loginctl enable-linger ${me}"
  fi
fi

echo
if (( fails )); then
  echo "${fails} problem(s) to fix, ${warns} warning(s)."
  exit 1
fi
echo "All good${warns:+ (${warns} warning(s))}."
