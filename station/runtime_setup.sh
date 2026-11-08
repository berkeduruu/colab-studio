#!/usr/bin/env bash
# Colab runtime'ında çalışır. Model paketlerini HuggingFace'ten arka planda indirmeye başlar
# (models.py), aynı anda ComfyUI'yi ve ses eklentilerini kurar; görsel paketi inince ComfyUI'yi
# 127.0.0.1:8188'de başlatır. Diğer paketler arka planda inmeye devam eder.
set -Eeuo pipefail

COMFY=/content/ComfyUI
PACKS=/content/packs
STATION=/content/station
# Açılışı tetikleyen işin model paketi (arayüzden gelen iş türü; elle başlatmada image): ilk o iner
# ve hazır sayılmak için yalnızca o beklenir. "none": işin paketi yok (yazıya dökme), hiçbiri beklenmez.
WANTED=${1:-image}
# ComfyUI ve ses eklentileri denenmiş sürümlerine sabit (2026-10-23'te çalışan açılışın master'ı):
# master'ı her açılışta çekmek, bir gün kırılan bir değişikliği tüm açılışları bozacak şekilde getirir.
COMFY_COMMIT=bc5f880dbdd43e426077f42ccec53bb577ffee9f
TTS_SUITE_COMMIT=f7693bc1965a0d7daa0addfc5dfcef834a507d6b
WHISPER_COMMIT=006a709848259ed3d69e2f9fcfd7a75f6c860ddd
# PyPI'da wheel'i olmayan ses paketlerinin ve L4 için derlenmiş SageAttention'ın hazır arşivi (GitHub
# Release dosyası). Arşivi yeniden üretince URL'yi ve sağlamayı birlikte güncelle.
WHEELS_URL=https://github.com/berkeduruu/colab-studio/releases/download/wheels-1/wheels.tar.gz
WHEELS_SHA256=b99eb95f71b294326d3bef5e70302f0ebe07707acfa102ea0ebe768996cd7efc
# Sabit sürümler: indirme hattı get_hf_file_metadata/hf_hub_url/.location davranışına bağlı, bu yüzden
# huggingface_hub audio_requirements.txt'deki sürümle aynı olmalı (aşağıdaki test bunu doğrular).
HUGGINGFACE_HUB_VERSION=1.33.0
# uv'nin sürümü ölçüldüğü sürüme sabit: `uv venv`/`uv pip install` davranışı sürümle değişiyor.
UV_VERSION=0.13.4
# ComfyUI, eklentiler ve ses paketleri Colab'ın kendi Python'una/torch'una değil, bu betiğin kurduğu
# ayrı bir ortama (venv) kurulur: Colab imajı değişince (Python, torch, CUDA kütüphaneleri, ön kurulu
# paketler) kurulumumuz etkilenmez. Python'u uv indirir (sistemdekine bakmaz). Paket sürümleri
# requirements.lock'ta (station/lock.sh üretir); torch ailesi PyTorch'un cu130 deposundan gelir ve
# SageAttention wheel'inin derlendiği torch ile aynıdır. Biri değişirse wheels arşivi de yeniden üretilmeli.
PYTHON_VERSION=3.13
TORCH_VERSION=2.11.0
VENV=/content/venv

# Kurulumun hangi aşamada olduğu arayüzde görünür (sunucu bu dosyayı 3 sn'de bir okur). Yazma atomik:
# önce .tmp'ye yazılıp yerine taşınır, yoksa sunucu boş/yarım aşama okur.
phase() { printf '%s\n' "$1" > /content/setup_phase.tmp && mv /content/setup_phase.tmp /content/setup_phase; }

phase "1/5 İndirme ortamı hazırlanıyor"
uv --version 2>/dev/null | grep -q "^uv ${UV_VERSION}" || pip install -q "uv==${UV_VERSION}"
# İndirme ayrı bir ortamda: ses eklentileri sistemdeki huggingface_hub sürümünü indirme sürerken
# değiştiriyor. --clear: aynı runtime'da yeniden koşulabilsin (var olan ortamda uv venv hata veriyordu).
uv venv -q --clear /content/hfenv && uv pip install -q -p /content/hfenv "huggingface_hub==${HUGGINGFACE_HUB_VERSION}"
# </dev/null: arka plan işi ssh'ın stdin'ini tutmasın (bu bağlantı yüzünden ssh dönmüyordu).
PRIORITY_PACK=$([[ ${WANTED} == none ]] || echo "${WANTED}") nohup /content/hfenv/bin/python ${STATION}/models.py image speech enhance model3d video music >/content/models.log 2>&1 </dev/null &
models_pid=$!

phase "2/5 ComfyUI ve eklentiler indiriliyor"
# PyPI'da wheel'i olmayan ses paketlerinin (pyworld, diffq vb.) ve L4 için derlenmiş SageAttention
# 2.2.0'ın hazır wheel arşivi arka planda indirilir (~30 sn kaynak derlemesini önler).
rm -rf /tmp/tts /tmp/whisper
mkdir -p /content/wheels /tmp/tts /tmp/whisper
# GitHub'ın arşiv sunucusu zaman zaman 504 döndürüyor (tek bir 504 GPU açılışını düşürüyordu): her
# indirme yeniden denenir. Arşiv önce dosyaya iner; yarıda kalıp yeniden başlayan bir aktarım tar'a
# yarım arşiv beslemesin.
CURL=(curl -LsSf --retry 6 --retry-delay 5 --retry-all-errors)
fetch() {  # fetch URL DIR: GitHub arşivini DIR'e açar (üst klasör atlanır)
  local file rc
  file=$(mktemp /tmp/archive.XXXXXX)
  "${CURL[@]}" "$1" -o "${file}" && tar -C "$2" -xzf "${file}" --strip-components=1 && rc=0 || rc=$?
  rm -f "${file}"
  return "${rc}"
}
("${CURL[@]}" "${WHEELS_URL}" -o /tmp/wheels.tar.gz
 echo "${WHEELS_SHA256}  /tmp/wheels.tar.gz" | sha256sum -c --quiet -
 tar -C /content/wheels -xzf /tmp/wheels.tar.gz) &
wheels_pid=$!
fetch "https://github.com/diodiogod/TTS-Audio-Suite/archive/${TTS_SUITE_COMMIT}.tar.gz" /tmp/tts &
tts_pid=$!
fetch "https://github.com/yuvraj108c/ComfyUI-Whisper/archive/${WHISPER_COMMIT}.tar.gz" /tmp/whisper &
whisper_pid=$!
# Eski ağaç kalmaz: arşiv var olan dizinin üzerine açılırsa silinmiş dosyalar geride kalıyordu.
rm -rf "${COMFY}"
mkdir -p "${COMFY}"
fetch "https://github.com/Comfy-Org/ComfyUI/archive/${COMFY_COMMIT}.tar.gz" "${COMFY}"

phase "3/5 Python ortamı ve paketler kuruluyor"
# --clear: aynı runtime'da yeniden koşulabilsin. only-managed: Colab'ın Python'una bağlı kalma.
uv venv -q --clear --python "${PYTHON_VERSION}" --python-preference only-managed "${VENV}"
# Kilit dosyası tüm paketleri (torch ailesi ve CUDA kütüphaneleri dahil) sürümüyle sabitler: çözümleme yok.
uv pip install -q -p "${VENV}" --no-deps --index-strategy unsafe-best-match -r ${STATION}/requirements.lock
wait ${wheels_pid}
# TTS-Audio-Suite'in install.py'si paketleri tek tek pip ile kurar (~6.5 dk) ve aralarındaki
# çakışmaları yok sayar. Vardığı son durum audio_requirements.txt'de (PyPI'da olmayan, kullanmadığımız
# motorların paketleri hariç); aynı durum çözümleme yapmadan birebir kurulur. Kilit dosyası bu
# paketlerin bağımlılıklarını zaten içerir.
uv pip install -q -p "${VENV}" --no-deps --find-links /content/wheels -r ${STATION}/audio_requirements.txt /content/wheels/sageattention-*.whl
# Ortam doğrulaması ComfyUI'den ve eklentilerden önce: sürümler beklenenle aynı mı, ikili paketler
# (sageattention, numba, ...) içe aktarılabiliyor mu. Eklenti kodu çalıştırılmaz; /content dışında bir
# dizinde çalışmaz, çünkü orada eklenti klasörleri import yolunu gölgeleyebilirdi. Sürümler
# /content/setup_info.json'a yazılır (arayüzdeki kayıtla birlikte ilk bakılacak yer).
(cd /content && "${VENV}/bin/python" "${STATION}/envcheck.py" "${TORCH_VERSION}" "${PYTHON_VERSION}" /content/setup_info.json) \
  || { echo "Python ortamı beklenen gibi değil (torch ${TORCH_VERSION}, Python ${PYTHON_VERSION}); yukarıdaki satır hangi paketin sorunlu olduğunu söyler" >&2; exit 1; }
# Tek tek beklenir: "wait a b" yalnızca sonuncunun çıkış kodunu döndürür, ilkinin hatası yutulurdu.
wait ${tts_pid}
wait ${whisper_pid}
rm -rf "${COMFY}/custom_nodes/TTS-Audio-Suite" "${COMFY}/custom_nodes/ComfyUI-Whisper"
mv /tmp/tts "${COMFY}/custom_nodes/TTS-Audio-Suite"
mv /tmp/whisper "${COMFY}/custom_nodes/ComfyUI-Whisper"
# Eklentinin "NumPy 2 uyumluluğu" satırları NumPy 2'nin np.bool'unu Python'un bool'u ile eziyor; numba
# librosa'nın sıfır geçiş fonksiyonunu derleyemiyor, Chatterbox motoru kurulamayıp sessiz ses dönüyor.
NP_BOOL_FILES=("${COMFY}/custom_nodes/TTS-Audio-Suite/nodes/audio/vocal_removal_node.py"
  "${COMFY}/custom_nodes/TTS-Audio-Suite/engines/rvc/impl/pitch_extraction.py")
sed -i '/np\.bool = bool/d' "${NP_BOOL_FILES[@]}"
# sed desen bulamazsa da 0 döner: yama gerçekten kalktı mı doğrulanır, yoksa sessiz ses bozukluğu
# yerine açık hata verilir (eklenti commit'i değişmiş olabilir).
if grep -q 'np\.bool = bool' "${NP_BOOL_FILES[@]}"; then
  echo "np.bool = bool yaması uygulanamadı: ${NP_BOOL_FILES[*]} içinde satır duruyor (TTS-Audio-Suite commit'i değişmiş olabilir)" >&2
  exit 1
fi

# ComfyUI modelleri bir iş gelince arar; açılışı Qwen3-TTS ortamı ve model indirmesiyle üst üste
# binsin diye hemen başlatılır.
mkdir -p /content/models
rm -rf "${COMFY}/models"
ln -s /content/models "${COMFY}/models"
cd "${COMFY}"
# comfy.log her açılışta sıfırdan yazılır: yoksa sınırsız büyüyordu. Uzun oturumlarda döndürmek için
# ayrıca logrotate ya da `--log-file` ile ayrı dosya kullanılabilir; şimdilik açılışta truncate.
: > /content/comfy.log
nohup "${VENV}/bin/python" main.py --listen 127.0.0.1 --port 8188 --disable-partner-nodes --use-sage-attention >/content/comfy.log 2>&1 </dev/null &
echo "ComfyUI başlatıldı (pid $!)"
cd /content

# Qwen3-TTS transformers 4 ister; eklenti onu ayrı bir sanal ortamda ("Shared Runtime") çalıştırır.
# Eklentinin install_runtime fonksiyonu pip ve VibeVoice derlemesiyle ~38 sn sürüyor; uv ile ~3 sn.
phase "4/5 Qwen3-TTS ortamı kuruluyor"
SHARED_RT="${COMFY}/custom_nodes/TTS-Audio-Suite/runtimes/shared_legacy_t4"
# --clear: aynı runtime'da yeniden koşma (var olan ortamda uv venv hata veriyordu).
uv venv -q --clear --python "${VENV}/bin/python" "${SHARED_RT}"
# .pth yolu python'dan türetilir: sabit "lib/python3.13" sürüm değişince sessizce yanlış yere yazıyordu.
SHARED_SITE=$("${SHARED_RT}/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
BASE_SITE=$("${VENV}/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
echo "${BASE_SITE}" > "${SHARED_SITE}/_tts_audio_suite_base_runtime.pth"
uv pip install -q --no-deps -p "${SHARED_RT}" transformers==4.57.3 huggingface_hub==0.36.2 tokenizers==0.22.2 diffusers==0.39.0 kernels==0.9.0
# Hata log dosyasına gittiği için görünmüyordu: düşerse log ekrana basılır.
(cd "${COMFY}/custom_nodes/TTS-Audio-Suite" && BASE_SITE="${BASE_SITE}" "${VENV}/bin/python" -c "
import json, os, sys
from dataclasses import asdict
from utils.runtimes.profiles import get_runtime_profile
from utils.runtimes.bootstrap import BOOTSTRAP_STRATEGY, resolve_runtime_dir, runtime_is_ready
p = get_runtime_profile('vibevoice_transformers4_shared')
(resolve_runtime_dir(p) / 'runtime_metadata.json').write_text(json.dumps({
    'profile': asdict(p), 'bootstrap_strategy': BOOTSTRAP_STRATEGY, 'install_complete': True,
    'inherited_base_site_packages': os.environ['BASE_SITE'],
    'source_python': os.path.realpath(sys.executable),
}))
assert runtime_is_ready(p)") >/content/shared_runtime.log 2>&1 || { cat /content/shared_runtime.log >&2; exit 1; }


if [[ ${WANTED} == none ]]; then
  echo "paket beklenmiyor"; exit 0
fi
phase "5/5 Model paketi inmesi bekleniyor (${WANTED})"
until [[ -f ${PACKS}/${WANTED}.done ]]; do
  [[ -f ${PACKS}/${WANTED}.error ]] && { cat ${PACKS}/${WANTED}.error >&2; exit 1; }
  # İndirici öldüyse (OOM, kill) ne .done ne .error yazılır; zaman aşımına kadar beklenmez.
  if ! kill -0 ${models_pid} 2>/dev/null && [[ ! -f ${PACKS}/${WANTED}.done ]]; then
    echo "model indirici beklenmedik şekilde durdu:" >&2; tail -n 20 /content/models.log >&2; exit 1
  fi
  sleep 2
done
echo "${WANTED} modelleri hazır"
