#!/usr/bin/env bash
# Tüm otomatik testler (GPU gerekmez): lint, backend (pytest) ve arayüz (Playwright, sahte API).
# Deploy'dan önce çalıştır:  ./test.sh
set -Eeuo pipefail
cd "$(dirname "$0")"
./lint.sh
deploy/test-scripts.sh
cd server/web
npm ci --silent
npm run build --silent
npx playwright install chromium  # temiz makinede tarayıcı yoktur; kuruluysa hızlıca geçer
npx playwright test
cd ..
uv run -q --python 3.12 --with-requirements requirements.txt --with pytest --with pytest-aiohttp --with pytest-asyncio pytest -q
