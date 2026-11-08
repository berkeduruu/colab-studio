#!/usr/bin/env bash
# Optional: put the studio behind nginx with HTTPS (Let's Encrypt) and a password, for a server with
# a domain name pointing at it (e.g. your own domain, or a free <ip-with-dashes>.sslip.io name).
#
#   sudo ./deploy/setup-nginx.sh studio.example.com [you@example.com]
#   (optional: STUDIO_USER and STUDIO_PASSWORD in the environment instead of the questions)
#
# Debian/Ubuntu (apt) with systemd. Ports 80 and 443 must be free for nginx and reachable from the
# internet for the certificate. Other nginx sites are left alone. The script manages ONE studio site:
# running it again with another domain replaces the previous domain's site.
# Keep STUDIO_HOST=127.0.0.1 in .env so the studio itself is only reachable through nginx.
# If anything fails (certificate, nginx test, reload, Ctrl+C), the previous nginx configuration is put back.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
DOMAIN=$(tr '[:upper:]' '[:lower:]' <<<"${1:?usage: sudo $0 <domain> [email]}")
DOMAIN=${DOMAIN%.}
EMAIL=${2:-}
SITE=/etc/nginx/sites-available/colab-studio.conf
LINK=/etc/nginx/sites-enabled/colab-studio.conf
# The temporary HTTP-only site for the certificate challenge lives in its own file, so the working
# site is never overwritten before a certificate exists.
ACME_SITE=/etc/nginx/sites-available/colab-studio-acme.conf
ACME_LINK=/etc/nginx/sites-enabled/colab-studio-acme.conf
HTPASSWD=/etc/nginx/colab-studio.htpasswd
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
label='[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?'
[[ ${DOMAIN} =~ ^(${label}\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]] \
  || die "invalid domain: ${DOMAIN} (expected a DNS name such as studio.example.com; an IP address cannot get a certificate here)"
command -v systemctl >/dev/null && [[ -d /run/systemd/system ]] \
  || die "this script needs systemd to manage nginx. In a container or without systemd, put your own reverse proxy in front of the studio."
port=$(grep -E '^STUDIO_PORT=' .env 2>/dev/null | cut -d= -f2 || true)
port=${port:-8190}
host=$(grep -E '^STUDIO_HOST=' .env 2>/dev/null | cut -d= -f2 || true)
[[ ${host:-127.0.0.1} == 127.0.0.1 ]] || echo "WARNING: STUDIO_HOST=${host} in .env; set it to 127.0.0.1 so the studio is only reachable through nginx, then restart it."
timeout 5 getent hosts "${DOMAIN}" >/dev/null || echo "WARNING: ${DOMAIN} does not resolve yet; the certificate request will fail until its DNS record points at this server."

missing=()
command -v nginx >/dev/null || missing+=(nginx)
command -v certbot >/dev/null || missing+=(certbot)
command -v htpasswd >/dev/null || missing+=(apache2-utils)
command -v openssl >/dev/null || missing+=(openssl)
command -v python3 >/dev/null || missing+=(python3)
if (( ${#missing[@]} )); then
  command -v apt-get >/dev/null || die "install ${missing[*]} with your package manager, then re-run"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
fi
systemctl is-active --quiet nginx || systemctl start nginx \
  || die "nginx is not running and does not start (is another program using port 80 or 443? see: systemctl status nginx)"

# Every exit path (error, Ctrl+C) removes the temporary challenge site and, unless the new site went
# live, puts the previous studio site back.
acme_on=0
site_written=0
finished=0
had_site=0; [[ -f ${SITE} ]] && had_site=1
had_link=0; [[ -L ${LINK} || -e ${LINK} ]] && had_link=1
cleanup() {
  local code=$? changed=0
  if [[ ${acme_on} == 1 ]]; then rm -f "${ACME_LINK}" "${ACME_SITE}"; changed=1; fi
  if [[ ${finished} == 0 && ${site_written} == 1 ]]; then
    if [[ ${had_site} == 1 ]]; then mv -f "${SITE}.previous" "${SITE}"; else rm -f "${SITE}"; fi
    [[ ${had_link} == 1 ]] || rm -f "${LINK}"
    changed=1
    echo "The previous nginx configuration was put back." >&2
  fi
  rm -f "${SITE}.previous"
  if (( changed )) && nginx -t >/dev/null 2>&1; then systemctl reload nginx >/dev/null 2>&1 || true; fi
  exit "${code}"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

reload_nginx() {
  nginx -t || die "nginx configuration test failed (see above)"
  systemctl reload nginx || die "nginx did not reload (see: systemctl status nginx)"
}

# 1. Certificate: a temporary HTTP-only site answers the ACME challenge.
mkdir -p /var/www/acme
if [[ ! -d /etc/letsencrypt/live/${DOMAIN} ]]; then
  cat > "${ACME_SITE}" <<CONF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/acme; }
}
CONF
  ln -sfn "${ACME_SITE}" "${ACME_LINK}"
  acme_on=1
  reload_nginx
  if [[ -n ${EMAIL} ]]; then account=(--email "${EMAIL}"); else account=(--register-unsafely-without-email); fi
  certbot certonly -n --agree-tos "${account[@]}" --webroot -w /var/www/acme -d "${DOMAIN}" \
    || die "certbot could not get a certificate for ${DOMAIN} (see above): the name must point at this server and port 80 must be reachable from the internet"
  rm -f "${ACME_LINK}" "${ACME_SITE}"
  acme_on=0
fi
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/20-nginx.sh
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/20-nginx.sh

# 2. Password. STUDIO_USER / STUDIO_PASSWORD skip the questions (pass them through sudo:
# sudo STUDIO_PASSWORD=... ./deploy/setup-nginx.sh ...). Without them the script asks on the
# terminal; with no terminal (pipe, CI) it uses the user "studio" and generates a password.
if [[ ! -f ${HTPASSWD} ]]; then
  user=${STUDIO_USER:-}
  pass=${STUDIO_PASSWORD:-}
  if (: </dev/tty) 2>/dev/null; then
    [[ -n ${user} ]] || read -r -p "Username for the studio [studio]: " user </dev/tty
    [[ -n ${pass} ]] || { read -r -s -p "Password (Enter = generate one): " pass </dev/tty; echo; }
  fi
  user=${user:-studio}
  generated=0
  if [[ -z ${pass} ]]; then pass=$(openssl rand -base64 18); generated=1; fi
  htpasswd -bcB "${HTPASSWD}" "${user}" "${pass}" >/dev/null
  chown root:www-data "${HTPASSWD}"
  chmod 640 "${HTPASSWD}"
  [[ ${generated} == 1 ]] && echo "Generated password for ${user}: ${pass}  (shown once; change it with: sudo htpasswd -B ${HTPASSWD} ${user})"
  unset pass
fi

# 3. The studio only answers to IP addresses, localhost and the names in STUDIO_ALLOWED_HOSTS (DNS
# rebinding protection), so the domain is added there before the site goes live.
env_note=
if [[ -f .env ]]; then
  added=$(python3 - "${DOMAIN}" <<'PY'
import re, sys
domain = sys.argv[1]
text = open(".env").read()
m = re.search(r"^STUDIO_ALLOWED_HOSTS=(.*)$", text, re.M)
hosts = [h.strip().strip("'\"").lower().rstrip(".") for h in m.group(1).strip().strip("'\"").split(",") if h.strip()] if m else []
if domain in hosts or "*" in hosts:
    print("no")
else:
    line = "STUDIO_ALLOWED_HOSTS=" + ",".join([*hosts, domain])
    new, n = re.subn(r"^#?\s*STUDIO_ALLOWED_HOSTS=.*$", lambda _: line, text, count=1, flags=re.M)
    open(".env", "w").write(new if n else text.rstrip("\n") + "\n" + line + "\n")
    print("yes")
PY
)
  [[ ${added} == yes ]] && echo "Added ${DOMAIN} to STUDIO_ALLOWED_HOSTS in .env."
else
  env_note="no .env found: add STUDIO_ALLOWED_HOSTS=${DOMAIN} to the studio's .env (run ./install.sh first), otherwise it answers 403."
fi

# 4. HTTPS site.
[[ ${had_site} == 1 ]] && cp -a "${SITE}" "${SITE}.previous"
site_written=1
sed -e "s|@DOMAIN@|${DOMAIN}|g" -e "s|@PORT@|${port}|g" deploy/nginx.conf.template > "${SITE}"
ln -sfn "${SITE}" "${LINK}"
reload_nginx
finished=1

if [[ -n ${env_note} ]]; then
  echo "nginx is ready at https://${DOMAIN}/, but the studio is not reachable yet: ${env_note}"
  exit 1
fi
echo "Done: https://${DOMAIN}/"
echo "Restart the studio so it accepts the new name: systemctl --user restart colab-studio, docker compose up -d, or Ctrl+C and ./run.sh"
