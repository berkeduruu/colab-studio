#!/usr/bin/env bash
# Colab Studio installer for Linux (and WSL2). Safe to re-run: it only adds what is missing.
#
#   ./install.sh                install, ask a few questions, sign in to Colab
#   ./install.sh --service      also run the studio as a systemd user service (starts on boot)
#   ./install.sh --yes          no questions: keep defaults (sign-in still needed once)
#   ./install.sh --port 8200 --host 0.0.0.0    set STUDIO_PORT / STUDIO_HOST in .env (no question asked)
#                               a Hugging Face token is taken from the HF_TOKEN environment variable
#   ./install.sh --update       git pull, rebuild the UI, restart the service when no GPU is running
#   ./install.sh --mcp          also build the MCP server and print the `claude mcp add` command
#   ./install.sh --doctor       check the installation and say what is missing
#   ./install.sh --uninstall    remove the service and .venv (--purge: also data and .env)
set -Eeuo pipefail
cd "$(dirname "$0")"
REPO=$(pwd)
REPO_SLUG=${STUDIO_REPO:-berkeduruu/colab-studio}
# Google'ın Colab CLI'ı sürüme sabit: giriş akışı ya da komut çıktıları değişirse stüdyo bozulur. Yükseltirken
# Dockerfile'daki sürümle birlikte, gerçek bir GPU işiyle denenerek değiştirilir.
COLAB_CLI_VERSION=0.7.5
VERSION=$(cat VERSION)
SERVICE=0
YES=0
UPDATE=0
MCP=0
PORT_ARG=
HOST_ARG=

say() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }
while (( $# )); do
  arg=$1; shift
  case $arg in
    --service) SERVICE=1 ;;
    -y|--yes) YES=1 ;;
    --update) UPDATE=1 ;;
    --mcp) MCP=1 ;;
    --doctor) exec deploy/doctor.sh ;;
    --uninstall) exec deploy/uninstall.sh $([[ ${YES} == 1 ]] && echo --yes) "$@" ;;
    --port|--host)
      (( $# )) || die "${arg} needs a value (see ./install.sh --help)"
      if [[ ${arg} == --port ]]; then PORT_ARG=$1; else HOST_ARG=$1; fi
      shift ;;
    --port=*) PORT_ARG=${arg#*=} ;;
    --host=*) HOST_ARG=${arg#*=} ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: ${arg} (see ./install.sh --help)" ;;
  esac
done
if [[ -n ${PORT_ARG} ]]; then
  [[ ${PORT_ARG} =~ ^[0-9]+$ ]] && (( PORT_ARG >= 1 && PORT_ARG <= 65535 )) || die "--port must be a number from 1 to 65535 (got: ${PORT_ARG})"
fi
[[ -z ${HOST_ARG} || ${HOST_ARG} == 127.0.0.1 || ${HOST_ARG} == 0.0.0.0 ]] || die "--host must be 127.0.0.1 (this computer only) or 0.0.0.0 (other devices too, no password), got: ${HOST_ARG}"
[[ -t 0 ]] || YES=1  # no terminal (piped, CI): never wait for an answer

# ask "question" default -> answer on stdout
ask() {
  local answer
  if [[ ${YES} == 1 ]]; then echo "$2"; return; fi
  read -r -p "    $1 [$2]: " answer </dev/tty
  echo "${answer:-$2}"
}

# ask_port -> a valid port on stdout (asks again until it gets one)
ask_port() {
  local p
  while :; do
    p=$(ask "port" 8190)
    [[ ${p} =~ ^[0-9]+$ ]] && (( p >= 1 && p <= 65535 )) && { echo "${p}"; return; }
    note "the port must be a number from 1 to 65535" >&2
  done
}

# set_env KEY VALUE: replaces (or uncomments) KEY=... in ${ENV_FILE} (.env, or the draft of a new one)
ENV_FILE=.env
set_env() {
  SET_ENV_VALUE=$2 ENV_FILE=${ENV_FILE} python3 - "$1" <<'PY'
import os, re, sys
key, value, path = sys.argv[1], os.environ["SET_ENV_VALUE"], os.environ["ENV_FILE"]
text = open(path).read()
line = f"{key}={value}"
new, n = re.subn(rf"^#?\s*{key}=.*$", lambda _: line, text, count=1, flags=re.M)
open(path, "w").write(new if n else text.rstrip("\n") + "\n" + line + "\n")
PY
}

# Vite 8 needs Node.js 20.19+ or 22.12+ (older 20.x dies with "does not provide an export named 'styleText'").
node_can_build() {
  command -v npm >/dev/null || return 1
  local v major minor
  v=$(node -p 'process.versions.node' 2>/dev/null) || return 1
  major=${v%%.*}; minor=${v#*.}; minor=${minor%%.*}
  (( (major == 20 && minor >= 19) || (major == 22 && minor >= 12) || major > 22 ))
}

# download_ui: unpacks the prebuilt UI of release v${VERSION} into server/web/dist (all or nothing)
download_ui() {
  local url="https://github.com/${REPO_SLUG}/releases/download/v${VERSION}/web-dist.tar.gz" tmp
  note "downloading the prebuilt UI: ${url}"
  tmp=$(mktemp -d server/web/.dist-download.XXXXXX)
  if ! curl -fLsS --retry 3 "${url}" | tar -xz --no-same-owner -C "${tmp}" 2>/dev/null \
     || [[ ! -f ${tmp}/index.html ]]; then
    rm -rf "${tmp}"
    die "could not download the prebuilt UI of v${VERSION} (${url}). Install Node.js 20.19+ or 22.12+ and re-run ./install.sh to build it instead."
  fi
  chmod 755 "${tmp}"
  rm -rf server/web/dist
  mv "${tmp}" server/web/dist
  # The prebuilt UI matches the v${VERSION} release. The checkout (pulled commits or local edits) may be newer.
  if git rev-parse -q --verify "refs/tags/v${VERSION}" >/dev/null 2>&1; then
    if ! git diff --quiet "v${VERSION}" -- server/web/src server/web/public server/web/index.html server/web/package.json \
         server/web/package-lock.json server/web/vite.config.ts 2>/dev/null; then
      note "WARNING: this checkout has UI changes newer than v${VERSION}; the downloaded UI is the v${VERSION} one."
      note "Install Node.js 20.19+ or 22.12+, then run: rm -rf server/web/dist && ./install.sh"
    fi
  else
    note "(no git tag v${VERSION} here, e.g. a --depth 1 clone or a ZIP download: cannot tell whether your"
    note " checkout has newer UI changes than this release; a full git clone can)"
  fi
}

[[ $(uname -s) == Linux ]] || die "Colab Studio runs on Linux (on Windows use WSL2, on macOS it is untested)."
missing=()
for cmd in curl ssh ssh-keygen tar python3; do command -v "${cmd}" >/dev/null || missing+=("${cmd}"); done
if (( ${#missing[@]} )); then
  die "missing: ${missing[*]}. On Debian/Ubuntu: sudo apt install curl openssh-client tar python3"
fi
export PATH="$HOME/.local/bin:$PATH"

if [[ ${UPDATE} == 1 ]]; then
  say "Updating"
  command -v git >/dev/null && git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "--update needs a git checkout (git clone https://github.com/${REPO_SLUG}.git)"
  git pull --ff-only || die "git pull --ff-only failed (local changes or a diverged branch): resolve it, then re-run ./install.sh --update"
  VERSION=$(cat VERSION)
  rm -rf server/web/dist  # the UI is rebuilt below to match the new code
fi

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
  uv tool install --python 3.12 "google-colab-cli==${COLAB_CLI_VERSION}"
fi
COLAB=$(command -v colab) || die "colab CLI is not on PATH after installing it"

say "3/6 Python environment (.venv)"
uv venv -q --allow-existing --python 3.12 .venv
uv pip install -q --python .venv/bin/python -r server/requirements.txt
note "ok"

say "4/6 Web UI"
rm -rf server/web/.dist-download.*  # left over from an interrupted download
if [[ -f server/web/dist/index.html ]] && compgen -G "server/web/dist/assets/*.js" >/dev/null; then
  note "already built (delete server/web/dist to rebuild)"
else
  [[ -d server/web/dist ]] && { note "server/web/dist is incomplete, replacing it"; rm -rf server/web/dist; }
  if node_can_build; then
    note "building with $(node --version)"
    if ! (cd server/web && npm ci --silent --no-audit --no-fund && npm run build --silent); then
      rm -rf server/web/dist
      note "the build with $(node --version) failed (see above); using the prebuilt UI instead"
      download_ui
    fi
  else
    if command -v node >/dev/null; then
      note "Node.js $(node --version) cannot build the UI (needs 20.19+ or 22.12+)"
    else
      note "Node.js not found"
    fi
    download_ui
  fi
fi

say "5/6 Settings (.env)"
fresh=0
rm -f .env.partial  # left over from interrupted questions: never adopted as .env
if [[ -f .env ]]; then
  note ".env exists, keeping it"
else
  # A new .env is written to a draft and only renamed once every answer is in, so interrupting the
  # questions (Ctrl+C) never leaves a half-answered .env that later runs would keep.
  trap 'rm -f .env.partial' EXIT
  cp .env.example .env.partial
  chmod 600 .env.partial
  ENV_FILE=.env.partial
  fresh=1
fi
if [[ -n ${HOST_ARG} ]]; then
  set_env STUDIO_HOST "${HOST_ARG}"
elif [[ ${fresh} == 1 && ${YES} == 1 ]]; then
  set_env STUDIO_HOST 127.0.0.1
elif [[ ${fresh} == 1 ]]; then
  note "Who should be able to open the studio?"
  note "  1) only this computer (recommended)"
  note "  2) other devices that can reach this machine by IP (no password! see README)"
  while :; do
    case $(ask "choice" 1) in
      1) set_env STUDIO_HOST 127.0.0.1; break ;;
      2) set_env STUDIO_HOST 0.0.0.0; break ;;
      *) note "please answer 1 or 2" ;;
    esac
  done
fi
if [[ -n ${PORT_ARG} ]]; then
  set_env STUDIO_PORT "${PORT_ARG}"
elif [[ ${fresh} == 1 ]]; then
  set_env STUDIO_PORT "$(ask_port)"
fi
if [[ ${fresh} == 1 ]]; then
  if [[ -n ${HF_TOKEN:-} ]]; then
    set_env HF_TOKEN "${HF_TOKEN}"
    note "using the HF_TOKEN from your environment"
  elif [[ ${YES} == 0 ]]; then
    note "Hugging Face read token (optional, Enter to skip; input is hidden):"
    read -r -s -p "    HF_TOKEN: " token </dev/tty || true
    echo
    [[ -n ${token:-} ]] && set_env HF_TOKEN "${token}"
    unset token
  fi
  mv .env.partial .env
  trap - EXIT
  ENV_FILE=.env
  note "written to .env (edit it any time, then restart the studio)"
fi

say "6/6 Google sign-in for the Colab CLI"
if [[ -f ~/.config/colab-cli/token.json ]]; then
  note "already signed in"
elif [[ ${YES} == 1 && ! -t 0 ]]; then
  note "skipped (no terminal). Run '${COLAB} usage' once and follow the link to sign in."
  note "(plain 'colab usage' works once ~/.local/bin is on your PATH, usually in a new terminal)"
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
  me=${USER:-$(id -un)}  # USER docker exec / cron / su altında tanımsız olabilir
  loginctl enable-linger "${me}" 2>/dev/null || note "run 'sudo loginctl enable-linger ${me}' so the studio keeps running after you log out"
  note "running: systemctl --user status colab-studio · logs: journalctl --user -u colab-studio -f"
fi

if [[ ${MCP} == 1 ]]; then
  say "MCP server (Claude Code / Claude Desktop)"
  mcp/install.sh || die "the MCP server did not install (needs Node.js 22+, see above). The studio itself is installed."
fi

if [[ ${UPDATE} == 1 ]] && systemctl --user is-active --quiet colab-studio 2>/dev/null; then
  phase=$(curl -fsS --max-time 5 "http://127.0.0.1:${port:-8190}/api/runtime" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("phase",""))' 2>/dev/null || true)
  if [[ ${phase} == off || ${phase} == error ]]; then  # error: the GPU did not start, no job is running
    systemctl --user restart colab-studio
    note "restarted the service (no job was running)"
  else
    note "NOT restarted: the studio is busy (GPU phase: ${phase:-unknown}), a restart would stop running jobs."
    note "Restart it when you are ready: systemctl --user restart colab-studio"
  fi
elif [[ ${UPDATE} == 1 ]]; then
  note "Restart the studio to use the new code (Ctrl+C and ./run.sh, or systemctl --user restart colab-studio)."
fi

say "Done"
if [[ ${host} == 0.0.0.0 ]]; then
  note "Open http://<this machine's IP>:${port}/ from your devices (no password: keep it on a trusted network)."
else
  note "Open http://127.0.0.1:${port}/"
fi
[[ ${SERVICE} == 1 ]] || note "Start the studio with: ./run.sh"
