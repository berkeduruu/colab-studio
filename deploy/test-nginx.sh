#!/usr/bin/env bash
# Tests deploy/setup-nginx.sh against a real nginx in a throw-away Ubuntu container: the happy path,
# and that every failure (certbot, nginx -t, Ctrl+C) puts back exactly the previous site file, its
# sites-enabled entry (symlink to another file, dangling symlink, regular file, or none) and .env.
# certbot and systemctl are stubs (no network, no systemd in the container); nothing on the host is
# touched. Needs Docker. Called by CI.
#
#   deploy/test-nginx.sh            # Ubuntu 24.04 (nginx 1.24)
#   UBUNTU=22.04 deploy/test-nginx.sh
set -Eeuo pipefail

if [[ ${1:-} != --inside ]]; then
  SRC=$(cd "$(dirname "$0")/.." && pwd)
  exec docker run --rm -v "${SRC}:/repo:ro" "ubuntu:${UBUNTU:-24.04}" bash /repo/deploy/test-nginx.sh --inside
fi

# ---- inside the container (root) ----
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq nginx apache2-utils python3 openssl curl >/dev/null 2>&1
mkdir -p /run/systemd/system
cat > /usr/local/bin/systemctl <<'EOF'
#!/bin/sh
case "$1" in
  is-active) pgrep -x nginx >/dev/null ;;
  start) nginx ;;
  reload) nginx -s reload ;;
  *) exit 0 ;;
esac
EOF
cat > /usr/local/bin/certbot <<'EOF'
#!/bin/sh
# CERTBOT_FAIL: the challenge fails. CERTBOT_SLEEP: wait first (to interrupt it). CERTBOT_CORRUPT:
# write an unusable certificate, so the final nginx -t fails.
[ -n "$CERTBOT_SLEEP" ] && sleep "$CERTBOT_SLEEP"
[ -n "$CERTBOT_FAIL" ] && { echo "stub certbot: challenge failed" >&2; exit 1; }
d=$(echo "$@" | sed 's/.*-d //')
mkdir -p "/etc/letsencrypt/live/$d"
openssl req -x509 -newkey rsa:2048 -nodes -subj "/CN=$d" -days 1 \
  -keyout "/etc/letsencrypt/live/$d/privkey.pem" -out "/etc/letsencrypt/live/$d/fullchain.pem" 2>/dev/null
[ -n "$CERTBOT_CORRUPT" ] && echo garbage > "/etc/letsencrypt/live/$d/fullchain.pem"
exit 0
EOF
chmod +x /usr/local/bin/systemctl /usr/local/bin/certbot
cp -r /repo /work
cd /work
printf 'STUDIO_HOST=127.0.0.1\nSTUDIO_PORT=8190\n' > .env
export STUDIO_PASSWORD=test-only-password
nginx

SITE=/etc/nginx/sites-available/colab-studio.conf
LINK=/etc/nginx/sites-enabled/colab-studio.conf
OTHER=/etc/nginx/sites-available/other.conf
printf 'server { listen 80; server_name other.example.com; return 204; }\n' > "${OTHER}"
fails=0
check() {  # check "name" condition
  if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1"; echo "${out:-}" | tail -12 | sed 's/^/      /'; fails=$((fails + 1)); fi
}
code() { curl -sk -o /dev/null -w '%{http_code}' --resolve "$1:443:127.0.0.1" "https://$1/"; }
state() {  # fingerprint of the site file, the sites-enabled entry and .env
  local link=none
  if [[ -L ${LINK} ]]; then link="symlink:$(readlink "${LINK}")"; elif [[ -f ${LINK} ]]; then link="file:$(md5sum <"${LINK}")"; fi
  echo "site:$( [[ -f ${SITE} ]] && md5sum <"${SITE}" || echo none) link:${link} env:$(md5sum <.env)"
}
run() { out=$("$@" 2>&1) && rc=0 || rc=$?; sleep 1; }  # nginx reloads asynchronously: let new workers start

run ./deploy/setup-nginx.sh A.Example.com.
check "first domain is served with a password (401)" '[[ ${rc} == 0 && $(code a.example.com) == 401 ]]'
check "domain lowercased into .env" 'grep -qx "STUDIO_ALLOWED_HOSTS=a.example.com" .env'
check "HSTS header" 'curl -skI --resolve a.example.com:443:127.0.0.1 https://a.example.com/ | grep -qi strict-transport-security'
check "server version hidden" '! curl -skI --resolve a.example.com:443:127.0.0.1 https://a.example.com/ | grep -qi "server: nginx/"'

before=$(state)
CERTBOT_FAIL=1 run ./deploy/setup-nginx.sh b.example.com
check "certbot failure: error message" '[[ ${rc} == 1 && ${out} == *"certbot could not get a certificate"* ]]'
check "certbot failure: everything as before, old site still served" '[[ $(state) == "${before}" && $(code a.example.com) == 401 ]]'
check "no temporary challenge site left" '[[ ! -e /etc/nginx/sites-enabled/colab-studio-acme.conf && ! -e /etc/nginx/sites-available/colab-studio-acme.conf ]]'
check "no snapshot folder left" '! compgen -G "/etc/nginx/.colab-studio-rollback.*" >/dev/null'

CERTBOT_CORRUPT=1 run ./deploy/setup-nginx.sh c.example.com
check "nginx -t failure: site, link and .env as before" '[[ ${rc} == 1 && $(state) == "${before}" && $(code a.example.com) == 401 ]]'

# Like Ctrl+C in a terminal: SIGINT to the whole process group (the script and certbot) while
# certbot runs. A background job of a non-interactive shell starts with SIGINT ignored (and an
# inherited ignore cannot be trapped), so a small launcher restores the default and makes the
# script its own process group, whose ID is then its PID.
CERTBOT_SLEEP=6 python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
  ./deploy/setup-nginx.sh e.example.com >/dev/null 2>&1 &
pid=$!
for _ in $(seq 50); do pgrep -f /usr/local/bin/certbot >/dev/null && break; sleep 0.2; done
sleep 0.5
kill -INT -- "-${pid}"
wait "${pid}" || true
sleep 1
check "Ctrl+C during certbot: everything as before" '[[ $(state) == "${before}" && $(code a.example.com) == 401 ]] && ! [[ -e /etc/nginx/sites-enabled/colab-studio-acme.conf ]]'

# The sites-enabled entry is put back exactly, whatever it was. c.example.com now has an unusable
# certificate, so each run below fails at the final nginx -t.
ln -sfn "${OTHER}" "${LINK}"; nginx -s reload; before=$(state)
run ./deploy/setup-nginx.sh c.example.com
check "link to another file keeps its target" '[[ ${rc} == 1 && $(state) == "${before}" && $(readlink "${LINK}") == "${OTHER}" ]]'

rm -f "${LINK}"; cp "${OTHER}" "${LINK}"; nginx -s reload; before=$(state)
run ./deploy/setup-nginx.sh c.example.com
check "regular file stays a regular file" '[[ ${rc} == 1 && $(state) == "${before}" && ! -L ${LINK} ]]'

rm -f "${LINK}"; nginx -s reload; before=$(state)
run ./deploy/setup-nginx.sh c.example.com
check "missing link stays missing" '[[ ${rc} == 1 && $(state) == "${before}" && ! -e ${LINK} && ! -L ${LINK} ]]'

mv "${SITE}" /root/site.bak; ln -sfn "${SITE}" "${LINK}"; before=$(state)  # dangling link, no site file
run ./deploy/setup-nginx.sh c.example.com
check "dangling link without a site file stays exactly so" '[[ ${rc} == 1 && $(state) == "${before}" && -L ${LINK} && ! -e ${SITE} ]]'
mv /root/site.bak "${SITE}"; nginx -s reload

run ./deploy/setup-nginx.sh d.example.com
check "another domain replaces the site, .env keeps both" '[[ ${rc} == 0 && $(code d.example.com) == 401 ]] && grep -qx "STUDIO_ALLOWED_HOSTS=a.example.com,d.example.com" .env'
run ./deploy/setup-nginx.sh d.example.com
check "re-run is idempotent" '[[ ${rc} == 0 && ${out} != *"Added"* ]]'
run ./deploy/setup-nginx.sh 1.2.3.4
check "IP address rejected" '[[ ${rc} == 1 && ${out} == *"invalid domain"* ]]'
run ./deploy/setup-nginx.sh -bad.example.com
check "invalid label rejected" '[[ ${rc} == 1 && ${out} == *"invalid domain"* ]]'
mv .env /root/env.bak
run ./deploy/setup-nginx.sh d.example.com
check "no .env: says it is not reachable yet, not Done" '[[ ${rc} == 1 && ${out} == *"not reachable yet"* && ${out} != *"Done:"* ]]'
mv /root/env.bak .env

(( fails == 0 )) || { echo "${fails} nginx test(s) failed"; exit 1; }
echo "all nginx tests passed"
