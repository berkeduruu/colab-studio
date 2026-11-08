#!/usr/bin/env bash
# Tüm otomatik testler (GPU gerekmez): lint, backend (pytest) ve arayüz (Playwright, sahte API).
# Deploy'dan önce çalıştır:  ./test.sh
set -Eeuo pipefail
cd "$(dirname "$0")"
# Gerekenler: uv (./install.sh kurar) ve Node.js 20.19+/22.12+ ile npm.
./lint.sh
deploy/test-scripts.sh
(cd server/web && npm ci --silent && npm run build --silent)  # pytest de derlenmiş arayüzü (dist/assets) ister
# Backend testleri tarayıcı testlerinden önce: tarayıcı tarafı bozulsa da bu sinyal kaybolmasın.
(cd server && uv run -q --python 3.12 --with-requirements requirements.txt --with pytest --with pytest-aiohttp --with pytest-asyncio pytest -q)
cd server/web
npx playwright install chromium  # temiz makinede tarayıcı yoktur; kuruluysa hızlıca geçer
npx playwright test
