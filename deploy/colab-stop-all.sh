#!/usr/bin/env bash
# Releases every Colab runtime this studio may still own.
#
# Why: a shutdown that is cut short (SIGKILL, timeout) leaves the sessions in `sessions.json` open on
# the Colab side and they keep burning compute units. systemd runs this as `ExecStopPost`; it can be
# run by hand after a crash (see docs/multi-session.md).
#
# It stops the default session plus every extra session listed in `$STUDIO_DATA/sessions.json`.
# The data folder, the colab CLI and the session name come from the studio's own settings
# (`server/config.py`, which reads `.env`): systemd does not load `.env`, and guessing them here would
# stop the wrong session (`STUDIO_SESSION`), use an unpinned CLI (`COLAB_BIN`) or miss the registry
# (`STUDIO_DATA`).
# Exit code is always 0: one session that refuses to stop must not abort the others.
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -n ${PYTHON:-} ]]; then
  python=${PYTHON}
elif [[ -x ${REPO}/.venv/bin/python ]]; then
  python=${REPO}/.venv/bin/python
else
  python=$(command -v python3 || echo /usr/bin/python3)
fi

settings=$(cd "${REPO}/server" 2>/dev/null &&
  "${python}" -c 'import config; print(config.DATA_DIR); print(config.COLAB); print(config.SESSION)' 2>/dev/null) || settings=
if [[ -n ${settings} && $(wc -l <<<"${settings}") == 3 ]]; then
  { read -r DATA; read -r COLAB; read -r SESSION; } <<<"${settings}"
else
  # The settings could not be read (no Python, broken .env): fall back to the environment and defaults.
  DATA=${STUDIO_DATA:-${HOME:-/root}/.local/share/colab-studio}
  DATA=${DATA/#\~/${HOME:-/root}}
  COLAB=${COLAB_BIN:-$(command -v colab || echo "${HOME:-/root}/.local/bin/colab")}
  SESSION=${STUDIO_SESSION:-studio}
fi

if [[ ${1:-} == --print-settings ]]; then  # used by deploy/test-scripts.sh
  printf 'data=%s\ncolab=%s\nsession=%s\n' "${DATA}" "${COLAB}" "${SESSION}"
  exit 0
fi

[ -x "${COLAB}" ] || exit 0

# The default session is never listed in the registry, so it is stopped unconditionally.
"${COLAB}" stop -s "${SESSION}" >/dev/null 2>&1 || true

# Extra sessions: read the names out of the registry (a broken file simply means "nothing to do").
if [ -f "${DATA}/sessions.json" ]; then
  names=$("${python}" - "${DATA}/sessions.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
names = {v.get("name", "") for v in data.values() if isinstance(v, dict)}
print("\n".join(sorted(n for n in names if n)))
PY
)
  failed=0
  while IFS= read -r name; do
    [[ -n ${name} ]] || continue
    "${COLAB}" stop -s "${name}" >/dev/null 2>&1 || failed=1
  done <<<"${names}"
  # The assignment is no longer valid once the sessions are gone. If one refused to stop the file is
  # kept: the studio's next start retries it (sessions.Manager.reap_leftovers).
  [[ ${failed} == 0 ]] && rm -f "${DATA}/sessions.json" 2>/dev/null
fi

exit 0
