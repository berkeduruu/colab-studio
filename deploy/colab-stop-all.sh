#!/usr/bin/env bash
# Releases every Colab runtime this studio may still own.
#
# Why: a shutdown that is cut short (SIGKILL, timeout) leaves the sessions in `sessions.json` open on
# the Colab side and they keep burning compute units. systemd runs this as `ExecStopPost`; it can be
# run by hand after a crash (see docs/multi-session.md).
#
# It stops the default session plus every extra session listed in `$STUDIO_DATA/sessions.json`.
# Exit code is always 0: one session that refuses to stop must not abort the others.
set -uo pipefail

DATA=${STUDIO_DATA:-${XDG_DATA_HOME:-${HOME:-/root}/.local/share}/colab-studio}
DATA=${DATA/#\~/${HOME:-/root}}
COLAB=${COLAB_BIN:-$(command -v colab || echo "${HOME:-/root}/.local/bin/colab")}
SESSION=${STUDIO_SESSION:-studio}

[ -x "${COLAB}" ] || exit 0

# The default session is never listed in the registry, so it is stopped unconditionally.
"${COLAB}" stop -s "${SESSION}" >/dev/null 2>&1 || true

# Extra sessions: read the names out of the registry (a broken file simply means "nothing to do").
if [ -f "${DATA}/sessions.json" ]; then
  python=${PYTHON:-$(command -v python3 || echo /usr/bin/python3)}
  names=$("${python}" - "${DATA}/sessions.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
names = {v.get("name", "") for v in data.values() if isinstance(v, dict)}
print(" ".join(sorted(n for n in names if n)))
PY
)
  for name in ${names}; do
    "${COLAB}" stop -s "${name}" >/dev/null 2>&1 || true
  done
  # The assignment is no longer valid once the sessions are gone.
  rm -f "${DATA}/sessions.json" 2>/dev/null || true
fi

exit 0
