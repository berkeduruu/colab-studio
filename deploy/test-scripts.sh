#!/usr/bin/env bash
# Tests the shell scripts that need neither network nor a GPU: install.sh argument checks,
# --doctor on an empty checkout and the uninstall safety guard. Runs in a temporary copy with a
# throw-away HOME. Called by ./test.sh and CI.
set -Eeuo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "${TMP}"' EXIT
mkdir -p "${TMP}/repo/deploy" "${TMP}/home"
cp "${SRC}/install.sh" "${SRC}/VERSION" "${SRC}/.env.example" "${TMP}/repo/"
cp "${SRC}/deploy/doctor.sh" "${SRC}/deploy/uninstall.sh" "${TMP}/repo/deploy/"
cd "${TMP}/repo"
export HOME=${TMP}/home
fails=0
check() {  # check "name" expected_exit "grep pattern" command...
  local name=$1 want=$2 pattern=$3 out code=0
  shift 3
  out=$("$@" 2>&1 </dev/null) || code=$?
  if [[ ${code} == "${want}" ]] && grep -qE -- "${pattern}" <<<"${out}"; then echo "ok    ${name}"; else
    echo "FAIL  ${name} (exit ${code}, wanted ${want}; pattern: ${pattern})"; echo "${out}" | sed 's/^/      /'; fails=$((fails + 1)); fi
}

check "bad port"            1 "--port must be a number"      ./install.sh --port abc
check "port out of range"   1 "--port must be a number"      ./install.sh --port 70000
check "missing port value"  1 "--port needs a value"         ./install.sh --port
check "bad host"            1 "--host must be 127.0.0.1"     ./install.sh --host 1.2.3.4
check "unknown option"      1 "unknown option"               ./install.sh --bogus
check "help lists --doctor" 0 "--doctor"                     ./install.sh --help
check "doctor on an empty checkout fails" 1 "\.env is missing" ./install.sh --doctor

# Uninstall must never delete a home directory because of a bad STUDIO_DATA.
mkdir -p "${HOME}/keep-me"
echo "STUDIO_DATA=~" > .env
check "uninstall refuses STUDIO_DATA=~" 0 "does not look like a data folder" ./install.sh --uninstall --purge
[[ -d ${HOME}/keep-me ]] && echo "ok    home directory untouched" || { echo "FAIL  home directory was deleted"; fails=$((fails + 1)); }
# The same home folder (or a parent of it, or of this checkout) spelled so that a plain string
# comparison misses it, and a folder that is not a studio data folder.
ln -sfn "${HOME}" "${TMP}/home-link"
mkdir -p "${HOME}/not-studio" && touch "${HOME}/not-studio/precious"
for bad in "${HOME}/../home" "${HOME}/" "${HOME}/." "${TMP}/home-link" "${TMP}" "${HOME}/not-studio"; do
  echo "STUDIO_DATA=${bad}" > .env
  check "uninstall --purge refuses STUDIO_DATA=${bad#"${TMP}"/}" 0 "does not look like a data folder" ./install.sh --uninstall --purge
done
[[ -d ${HOME}/keep-me && -f ${HOME}/not-studio/precious && -d ${TMP}/repo ]] && echo "ok    home, other folders and the checkout untouched" \
  || { echo "FAIL  a protected folder was deleted"; fails=$((fails + 1)); }

mkdir -p "${HOME}/data/media" && touch "${HOME}/data/studio.db"
echo "STUDIO_DATA=${HOME}/data" > .env
check "uninstall --yes keeps data and .env" 0 "kept|keeping|Done" ./install.sh --uninstall --yes
[[ -d ${HOME}/data && -f .env ]] && echo "ok    data and .env kept" || { echo "FAIL  data or .env deleted by --yes"; fails=$((fails + 1)); }
check "uninstall --purge deletes both" 0 "deleted" ./install.sh --uninstall --purge
[[ ! -d ${HOME}/data && ! -f .env ]] && echo "ok    data and .env deleted" || { echo "FAIL  --purge left data or .env"; fails=$((fails + 1)); }

# colab-stop-all.sh (systemd ExecStopPost) must take the session name, the colab CLI and the data folder
# from .env like the studio does: systemd does not load .env, and the default "studio" may be someone
# else's session on the same account.
mkdir -p "${TMP}/repo/server" "${HOME}/data2" "${TMP}/bin"
cp "${SRC}/deploy/colab-stop-all.sh" "${TMP}/repo/deploy/"
cp "${SRC}/server/config.py" "${TMP}/repo/server/"
printf '#!/usr/bin/env bash\necho "$*" >> "%s/colab.log"\n' "${TMP}" > "${TMP}/bin/colab-pinned"
chmod +x "${TMP}/bin/colab-pinned"
printf 'STUDIO_DATA=%s\nCOLAB_BIN=%s\nSTUDIO_SESSION=mine\n' "${HOME}/data2" "${TMP}/bin/colab-pinned" > .env
echo '{"1": {"name": "mine-2", "port": 8200}, "2": {"name": "mine-3", "port": 8210}}' > "${HOME}/data2/sessions.json"
check "stop-all reads .env" 0 "session=mine" env -u STUDIO_SESSION -u COLAB_BIN -u STUDIO_DATA deploy/colab-stop-all.sh --print-settings
env -u STUDIO_SESSION -u COLAB_BIN -u STUDIO_DATA deploy/colab-stop-all.sh
if [[ $(cat "${TMP}/colab.log" 2>/dev/null) == $'stop -s mine\nstop -s mine-2\nstop -s mine-3' && ! -f ${HOME}/data2/sessions.json ]]; then
  echo "ok    stop-all stops the .env session and the registry's sessions with the pinned CLI"
else
  echo "FAIL  stop-all: $(tr '\n' '|' < "${TMP}/colab.log" 2>/dev/null)"; fails=$((fails + 1))
fi
rm -f .env

(( fails == 0 )) || { echo "${fails} script test(s) failed"; exit 1; }
echo "all script tests passed"
