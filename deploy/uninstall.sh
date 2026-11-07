#!/usr/bin/env bash
# Removes what install.sh created. Your chats and media (STUDIO_DATA) and .env are kept unless you
# say otherwise. uv, the colab CLI and your Google sign-in are shared tools and are left alone.
#   ./install.sh --uninstall            asks before deleting data and .env
#   ./install.sh --uninstall --yes      no questions, keeps data and .env
#   ./install.sh --uninstall --purge    also deletes data and .env, no questions
set -Eeuo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
YES=0
PURGE=0
for arg in "$@"; do
  case $arg in
    -y|--yes) YES=1 ;;
    --purge) PURGE=1 ;;
    *) echo "unknown option: ${arg} (use --yes or --purge)" >&2; exit 1 ;;
  esac
done
[[ -t 0 ]] || YES=1  # no terminal: never wait for an answer, never delete data without --purge
say() { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
yes_no() {  # yes_no "question" -> true when the answer is y
  [[ ${PURGE} == 1 ]] && return 0
  [[ ${YES} == 1 ]] && return 1
  local a
  read -r -p "    $1 [y/N]: " a </dev/tty
  [[ ${a} == [yY]* ]]
}
envget() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

data=$(envget STUDIO_DATA)
data=${data:-~/.local/share/colab-studio}
data=${data/#\~/$HOME}
unit=~/.config/systemd/user/colab-studio.service

say "Service"
if [[ -f ${unit} ]]; then
  note "stopping it (a running GPU is released first, this can take a few minutes)"
  systemctl --user disable --now colab-studio 2>/dev/null || true
  rm -f "${unit}"
  systemctl --user daemon-reload
  note "removed ${unit}"
else
  note "no systemd service installed"
fi

say "Installed files"
if [[ -d .venv ]]; then rm -rf .venv; note "removed .venv"; fi
if [[ -d server/web/dist ]]; then rm -rf server/web/dist; note "removed server/web/dist (the built UI)"; fi
if [[ -d mcp/dist || -d mcp/node_modules ]]; then rm -rf mcp/dist mcp/node_modules; note "removed the built MCP server"; fi

say "Your data"
# Refuse anything that is not clearly a studio data folder: a typo in .env must not delete a home directory.
if [[ ${data} != /* || ${data} == / || ${data} == "${HOME}" || $(grep -o / <<<"${data}" | wc -l) -lt 2 ]]; then
  note "STUDIO_DATA=${data} does not look like a data folder; leaving it alone"
elif [[ -d ${data} ]]; then
  if yes_no "Delete your chats and generated media in ${data} ($(du -sh "${data}" 2>/dev/null | cut -f1))?"; then
    rm -rf "${data}"; note "deleted ${data}"
  else
    note "kept ${data}"
  fi
else
  note "no data folder at ${data}"
fi
if [[ -f .env ]]; then
  if yes_no "Delete .env (your settings and Hugging Face token)?"; then rm -f .env; note "deleted .env"; else note "kept .env"; fi
fi

say "Done"
note "This folder (${REPO}) is still here: delete it yourself if you do not need it."
note "Left alone because other tools use them: uv, the colab CLI ('uv tool uninstall google-colab-cli') and your Google sign-in (~/.config/colab-cli)."
