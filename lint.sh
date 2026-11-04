#!/usr/bin/env bash
# Tek lint kaynağı: test.sh, CI ve elle kullanım aynı komutu çalıştırır.
# Seçimler bilinçli: E501 (uzun satır) ve RUF001-003 (Türkçe harfler) kapalı; B023/B905 gibi
# testlerde kasıtlı desenler yok sayılır. Yeni bir kural açmadan önce burayı güncelle.
set -Eeuo pipefail
cd "$(dirname "$0")"
exec uv run -q --python 3.12 --with ruff ruff check server station \
  --select F,E9,B,SIM,C4,PIE,RET,ASYNC,RUF,E402 \
  --ignore E501,RUF001,RUF002,RUF003,SIM105,SIM114,SIM117,B008,B023,B905,RUF005,RUF006,ASYNC109,ASYNC110,ASYNC240,RET503,C420 \
  "$@"
