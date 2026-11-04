#!/usr/bin/env bash
# Optional: put the studio behind nginx with HTTPS (Let's Encrypt) and a password, for a server with
# a domain name pointing at it (any DNS name works, e.g. a free <ip>.sslip.io name).
#
#   sudo ./deploy/setup-nginx.sh studio.example.com [you@example.com]
#
# Debian/Ubuntu (apt). Ports 80 and 443 must be reachable from the internet for the certificate.
# Keep STUDIO_HOST=127.0.0.1 in .env so the studio itself is only reachable through nginx.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
DOMAIN=${1:?usage: sudo $0 <domain> [email]}
EMAIL=${2:-}
SITE=/etc/nginx/sites-available/colab-studio.conf
HTPASSWD=/etc/nginx/colab-studio.htpasswd
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ ${DOMAIN} =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid domain: ${DOMAIN}"
port=$(grep -E '^STUDIO_PORT=' .env 2>/dev/null | cut -d= -f2 || true)
port=${port:-8190}
host=$(grep -E '^STUDIO_HOST=' .env 2>/dev/null | cut -d= -f2 || true)
[[ ${host:-127.0.0.1} == 127.0.0.1 ]] || echo "WARNING: STUDIO_HOST=${host} in .env; set it to 127.0.0.1 so the studio is only reachable through nginx, then restart it."

missing=()
command -v nginx >/dev/null || missing+=(nginx)
command -v certbot >/dev/null || missing+=(certbot)
command -v htpasswd >/dev/null || missing+=(apache2-utils)
if (( ${#missing[@]} )); then
  command -v apt-get >/dev/null || die "install ${missing[*]} with your package manager, then re-run"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
fi

# 1. Certificate: a temporary HTTP-only site answers the ACME challenge.
mkdir -p /var/www/acme
if [[ ! -d /etc/letsencrypt/live/${DOMAIN} ]]; then
  cat > "${SITE}" <<CONF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/acme; }
}
CONF
  ln -sfn "${SITE}" /etc/nginx/sites-enabled/colab-studio.conf
  nginx -t && systemctl reload nginx
  if [[ -n ${EMAIL} ]]; then account=(--email "${EMAIL}"); else account=(--register-unsafely-without-email); fi
  certbot certonly -n --agree-tos "${account[@]}" --webroot -w /var/www/acme -d "${DOMAIN}"
fi
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/20-nginx.sh
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/20-nginx.sh

# 2. Password
if [[ ! -f ${HTPASSWD} ]]; then
  read -r -p "Username for the studio [studio]: " user </dev/tty || true
  user=${user:-studio}
  read -r -s -p "Password (Enter = generate one): " pass </dev/tty || true
  echo
  generated=0
  if [[ -z ${pass:-} ]]; then pass=$(openssl rand -base64 18); generated=1; fi
  htpasswd -bcB "${HTPASSWD}" "${user}" "${pass}" >/dev/null
  chown root:www-data "${HTPASSWD}"
  chmod 640 "${HTPASSWD}"
  [[ ${generated} == 1 ]] && echo "Generated password for ${user}: ${pass}  (shown once; change it with: sudo htpasswd -B ${HTPASSWD} ${user})"
  unset pass
fi

# 3. HTTPS site. If the new config fails the test, the previous one is restored.
[[ -f ${SITE} ]] && cp -a "${SITE}" "${SITE}.previous"
sed -e "s|@DOMAIN@|${DOMAIN}|g" -e "s|@PORT@|${port}|g" deploy/nginx.conf.template > "${SITE}"
ln -sfn "${SITE}" /etc/nginx/sites-enabled/colab-studio.conf
if ! nginx -t; then
  if [[ -f ${SITE}.previous ]]; then mv "${SITE}.previous" "${SITE}"; else rm -f /etc/nginx/sites-enabled/colab-studio.conf; fi
  die "nginx configuration test failed; previous configuration restored"
fi
rm -f "${SITE}.previous"
systemctl reload nginx

# 4. The studio only answers to IP addresses, localhost and the names in STUDIO_ALLOWED_HOSTS (DNS
# rebinding protection), so the domain is added there. The studio must be restarted to pick it up.
if [[ -f .env ]]; then
  python3 - "${DOMAIN}" <<'PY'
import re, sys
domain = sys.argv[1].lower().rstrip(".")
text = open(".env").read()
m = re.search(r"^STUDIO_ALLOWED_HOSTS=(.*)$", text, re.M)
hosts = [h.strip() for h in m.group(1).split(",") if h.strip()] if m else []
if domain not in hosts:
    line = "STUDIO_ALLOWED_HOSTS=" + ",".join([*hosts, domain])
    new, n = re.subn(r"^#?\s*STUDIO_ALLOWED_HOSTS=.*$", lambda _: line, text, count=1, flags=re.M)
    open(".env", "w").write(new if n else text.rstrip("\n") + "\n" + line + "\n")
PY
  echo "Added ${DOMAIN} to STUDIO_ALLOWED_HOSTS in .env."
else
  echo "WARNING: no .env found; add STUDIO_ALLOWED_HOSTS=${DOMAIN} to it (run ./install.sh first)."
fi
echo "Done: https://${DOMAIN}/"
echo "Restart the studio so it accepts the new name: systemctl --user restart colab-studio (or restart ./run.sh)"
