#!/bin/sh
# ssh ProxyCommand: colab_proxy.sh SESSION SESSIONS_JSON COLAB_BIN SSH_KEY
# "colab ssh -s NAME" opens a brand-new runtime when no session NAME exists, so an ssh command aimed
# at a session that was already stopped would create an orphan machine. If the session is not
# registered, no connection is made at all.
grep -q "\"$1\"" "$2" 2>/dev/null || { echo "no colab session: $1" >&2; exit 1; }
exec "$3" ssh --proxy-mode -s "$1" --identity "$4"
