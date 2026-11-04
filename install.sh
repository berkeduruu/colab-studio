#!/usr/bin/env bash
# Colab Studio installer for Linux (and WSL2). Safe to re-run: it only adds what is missing.
#
#   ./install.sh            install, ask a few questions, sign in to Colab
#   ./install.sh --service  also run the studio as a systemd user service (starts on boot)
#   ./install.sh --yes      no questions: keep defaults (sign-in still needed once)
set -Eeuo pipefail
cd "$(dirname "$0")"
REPO=$(pwd)
REPO_SLUG=${STUDIO_REPO:-berkeduruu/colab-studio}
VERSION=$(cat VERSION)
SERVICE=0
YES=0

say() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case $arg in
    --service) SERVICE=1 ;;
    -y|--yes) YES=1 ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $arg (see ./install.sh --help)" ;;
  esac
done
[[ -t 0 ]] || YES=1  # no terminal (piped, CI): never wait for an answer

# ask "question" default -> answer on stdout
ask() {
  local answer
  if [[ ${YES} == 1 ]]; then echo "$2"; return; fi
  read -r -p "    $1 [$2]: " answer </dev/tty
  echo "${answer:-$2}"
}

# set_env KEY VALUE: replaces (or uncomments) KEY=... in .env
set_env() {
  python3 - "$1" "$2" <<'PY'
import re, sys
key, value = sys.argv[1], sys.argv[2]
text = open(".env").read()
line = f"{key}={value}"
new, n = re.subn(rf"^#?\s*{key}=.*$", lambda _: line, text, count=1, flags=re.M)
open(".env", "w").write(new if n else text.rstrip("\n") + "\n" + line + "\n")
PY
}

[[ $(uname -s) == Linux ]] || die "Colab Studio runs on Linux (on Windows use WSL2, on macOS it is untested)."
missing=()
for cmd in curl ssh ssh-keygen tar python3; do command -v "${cmd}" >/dev/null || missing+=("${cmd}"); done
if (( ${#missing[@]} )); then
  die "missing: ${missing[*]}. On Debian/Ubuntu: sudo apt install curl openssh-client tar python3"
fi
export PATH="$HOME/.local/bin:$PATH"

say "1/6 uv (Python package manager)"
if command -v uv >/dev/null; then
  note "found $(uv --version)"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
  command -v uv >/dev/null || die "uv was installed but is not on PATH; open a new terminal and re-run ./install.sh"
fi

say "2/6 Google Colab CLI"
if command -v colab >/dev/null; then
  note "found $(command -v colab)"
else
  uv tool install --python 3.12 google-colab-cli
fi
COLAB=$(command -v colab) || die "colab CLI is not on PATH after installing it"

say "3/6 Python environment (.venv)"
uv venv -q --allow-existing --python 3.12 .venv
uv pip install -q --python .venv/bin/python -r server/requirements.txt
note "ok"

say "4/6 Web UI"
if [[ -f server/web/dist/index.html ]]; then
  note "already built (delete server/web/dist to rebuild)"
elif command -v npm >/dev/null && [[ $(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0) -ge 20 ]]; then
  note "building with $(node --version)"
  (cd server/web && npm ci --silent --no-audit --no-fund && npm run build --silent)
else
  url="https://github.com/${REPO_SLUG}/releases/download/v${VERSION}/web-dist.tar.gz"
  note "Node.js 20+ not found, downloading the prebuilt UI: ${url}"
  mkdir -p server/web/dist
  curl -fLsS --retry 3 "${url}" | tar -xz -C server/web/dist \
    || die "could not download the UI. Install Node.js 20+ and re-run, or check ${url}"
  # The prebuilt UI matches the v${VERSION} release. Code pulled from main may have newer UI changes.
  if git rev-parse -q --verify "refs/tags/v${VERSION}" >/dev/null 2>&1 \
     && ! git diff --quiet "v${VERSION}" HEAD -- server/web/src server/web/index.html server/web/package.json 2>/dev/null; then
    note "WARNING: this checkout has UI changes newer than v${VERSION}; the downloaded UI is the v${VERSION} one."
    note "Install Node.js 20+, then run: rm -rf server/web/dist && ./install.sh"
  fi
fi

say "5/6 Settings (.env)"
if [[ -f .env ]]; then
  note ".env exists, keeping it"
else
  cp .env.example .env
  chmod 600 .env
  note "Who should be able to open the studio?"
  note "  1) only this computer (recommended)"
  note "  2) other devices that can reach this machine by IP (no password! see README)"
  case $(ask "choice" 1) in
    2) set_env STUDIO_HOST 0.0.0.0 ;;
    *) set_env STUDIO_HOST 127.0.0.1 ;;
  esac
  set_env STUDIO_PORT "$(ask "port" 8190)"
  if [[ ${YES} == 0 ]]; then
    note "Hugging Face read token (optional, Enter to skip; input is hidden):"
    read -r -s -p "    HF_TOKEN: " token </dev/tty || true
    echo
    [[ -n ${token:-} ]] && set_env HF_TOKEN "${token}"
    unset token
  fi
  note "written to .env (edit it any time, then restart the studio)"
fi

say "6/6 Google sign-in for the Colab CLI"
if [[ -f ~/.config/colab-cli/token.json ]]; then
  note "already signed in"
elif [[ ${YES} == 1 && ! -t 0 ]]; then
  note "skipped (no terminal). Run 'colab usage' once and follow the link to sign in."
else
  note "A link opens in your terminal: open it in any browser, approve, paste the code back."
  note "Your Google account needs Colab compute units (Colab Pro or pay-as-you-go) for an L4 GPU."
  "${COLAB}" usage || note "sign-in did not finish; run 'colab usage' later"
fi

port=$(grep -E '^STUDIO_PORT=' .env | cut -d= -f2 || true)
host=$(grep -E '^STUDIO_HOST=' .env | cut -d= -f2 || true)
if [[ ${SERVICE} == 1 ]]; then
  say "systemd user service"
  session=$(grep -E '^STUDIO_SESSION=' .env | cut -d= -f2 || true)
  mkdir -p ~/.config/systemd/user
  sed -e "s|@REPO@|${REPO}|g" -e "s|@PATH@|${PATH}|g" -e "s|@COLAB@|${COLAB}|g" -e "s|@SESSION@|${session:-studio}|g" \
    deploy/colab-studio.service > ~/.config/systemd/user/colab-studio.service
  systemctl --user daemon-reload
  systemctl --user enable -q colab-studio
  systemctl --user restart colab-studio
  # Without lingering a user service stops when you log out (e.g. closing the SSH session on a server).
  loginctl enable-linger "$USER" 2>/dev/null || note "run 'sudo loginctl enable-linger $USER' so the studio keeps running after you log out"
  note "running: systemctl --user status colab-studio · logs: journalctl --user -u colab-studio -f"
fi

say "Done"
if [[ ${host} == 0.0.0.0 ]]; then
  note "Open http://<this machine's IP>:${port}/ from your devices (no password: keep it on a trusted network)."
else
  note "Open http://127.0.0.1:${port}/"
fi
[[ ${SERVICE} == 1 ]] || note "Start the studio with: ./run.sh"
