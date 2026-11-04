"""Model paketleri: runtime açılınca HuggingFace'ten yerel diske indirilir.

HF (xet, yüksek performans modu) L4 runtime'ında ~340 MB/s veriyor; Drive'dan kopya ~70-100 MB/s
ve sık duraksıyor. Paketler sırayla iner: önce görsel (açılış bunu bekler), sonra diğerleri arka
planda. Her paket bitince /content/packs/<paket>.done, hata olursa <paket>.error yazılır.
İlerleme (paket başına toplam ve inen bayt) birkaç saniyede bir /content/packs/progress.json'a yazılır.

Kullanım: python3 models.py <paket> [<paket> ...]
"""
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

from huggingface_hub import get_hf_file_metadata, hf_hub_url

MODELS = Path("/content/models")
PACKS_DIR = Path("/content/packs")
# Model dosyaları buraya iner; disk koruması (check_disk) ve ENOSPC mesajları bu kökü kullanır.
DISK_ROOT = Path("/content")
# Metadata isteği HF'de geçici olarak düşebilir; paket bu yüzden kalıcı olarak zehirlenmesin.
META_ATTEMPTS = 4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog  # noqa: E402

# Sunucunun gönderdiği katalog (/content/station/models.toml); yoksa varsayılanlar.
CATALOG_FILE = next(p for p in (Path(__file__).with_name("models.toml"), Path(__file__).with_name("models.default.toml")) if p.exists())
# paket (iş türü) -> [(HF repo, repo içindeki dosya, models altındaki klasör)]
PACKS = catalog.packs(catalog.load(CATALOG_FILE))


# paket -> {"total": bayt, "done": inmiş bayt}; ana iş parçacığı günceller, yazıcı dosyaya döker.
progress = {}
current = {"pack": None, "base": 0, "bytes": 0}  # inen dosya: paketin önceki dosyaları + bu dosyada inen
_bytes_lock = threading.Lock()
_meta = {}  # (repo, filename) -> (size, url, etag)


def _fmt(n):
    """Baytı okunur biçime çevirir (disk mesajları için)."""
    for unit, factor in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if n >= factor:
            return f"{n / factor:.1f} {unit}"
    return f"{n} B"


def disk_error(what, err=None):
    """ENOSPC için tek satırlık net hata: ne yazılamadı, /content'te ne kadar boş kaldı."""
    try:
        free = _fmt(shutil.disk_usage(DISK_ROOT).free)
    except OSError:
        free = "ölçülemedi"
    detail = f" ({err})" if err else ""
    return SystemExit(f"disk doldu: {what} yazılamıyor, {DISK_ROOT} boş {free}{detail}")


def check_disk(packs):
    """İndirilecek toplam baytı /content'in boş alanıyla karşılaştırır.
    Yetmiyorsa hangi paketin ne kadar yer istediğini söyleyip SystemExit verir: sessiz ENOSPC ve
    dokuz tur yeniden deneme yerine tek ve açık bir hata."""
    need = {pack: progress[pack]["total"] - progress[pack]["done"] for pack in packs}
    total = sum(need.values())
    free = shutil.disk_usage(DISK_ROOT).free
    if total <= free:
        return
    lines = [f"  {pack}: {_fmt(n)}" for pack, n in need.items() if n > 0]
    raise SystemExit(
        f"disk yetersiz: {DISK_ROOT} boş {_fmt(free)}, indirilecek {_fmt(total)} "
        f"(eksik {_fmt(total - free)})\n" + "\n".join(lines))


def clean_state(packs):
    """Aynı runtime'da yeniden koşma: bayat .error dosyaları silinir, .done yalnızca paketin bütün
    dosyaları diskte duruyorsa kalır, yarım kalan .part artıkları toplanır."""
    for pack in packs:
        (PACKS_DIR / f"{pack}.error").unlink(missing_ok=True)
        complete = all((MODELS / folder / os.path.basename(fn)).exists() for _, fn, folder in PACKS[pack])
        if not complete:
            (PACKS_DIR / f"{pack}.done").unlink(missing_ok=True)
    for part in MODELS.rglob("*.part") if MODELS.exists() else ():
        part.unlink(missing_ok=True)


def write_progress():
    snapshot = {pack: dict(p) for pack, p in progress.items()}
    if current["pack"] in snapshot:
        p = snapshot[current["pack"]]
        p["done"] = min(p["total"], current["base"] + current["bytes"])
    tmp = PACKS_DIR / "progress.json.tmp"
    tmp.write_text(json.dumps(snapshot))
    os.replace(tmp, PACKS_DIR / "progress.json")


def progress_writer():
    """İlerlemeyi 2 sn'de bir diske döker. /content dolduysa (ENOSPC) yazmayı bırakır ve nedenini
    indirme tarafıyla aynı netlikte yazar."""
    while True:
        try:
            write_progress()
        except OSError as e:
            print(str(disk_error("progress.json", e)), file=sys.stderr, flush=True)
            return
        time.sleep(2)


def _fetch_meta(item):
    """HF dosya bilgisi: (boyut, imzalı indirme adresi, etag). Geçici HF hatalarında kısa backoff'la
    yeniden dener; böylece tek bir 500/429 paketi kalıcı olarak .error'a yazmaz."""
    repo, filename = item
    for attempt in range(1, META_ATTEMPTS + 1):
        try:
            m = get_hf_file_metadata(hf_hub_url(repo, filename))
            return (m.size, m.location, getattr(m, "etag", None))
        except Exception:
            if attempt == META_ATTEMPTS:
                raise
            time.sleep(2 * attempt)


def _try_meta(item):
    """_fetch_meta'nın hata yutan biçimi: başarıda (item, metadata), hatada (item, iz) döner."""
    try:
        return item, _fetch_meta(item)
    except Exception:
        return item, traceback.format_exc()


def _meta_for(repo, filename, attempt):
    """İlk denemede measure()'un önbelleği; sonraki denemelerde taze metadata (imzalı adresin
    süresi dolmuş olabilir)."""
    if attempt == 1 and (repo, filename) in _meta:
        return _meta[(repo, filename)]
    return _fetch_meta((repo, filename))


def _etag_sha256(etag):
    """HF LFS dosyalarının etag'i dosyanın sha256'sıdır (tırnaklı, bazen "sha256:" önekli).
    Küçük (LFS dışı) dosyalarda etag bir git blob kimliği (sha1) olur; o zaman None döner."""
    if not etag:
        return None
    value = etag.strip().strip('"')
    if value.startswith("sha256:"):
        value = value[len("sha256:"):]
    value = value.lower()
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


def _verify(tmp, size, sha256_expected):
    """İndirilen .part dosyasının boyutu ve (varsa) sha256'sı HF'dekiyle aynı mı.
    Akış halinde okunur: 15 GB'lık dosya belleğe alınmaz."""
    got = os.path.getsize(tmp)
    if got != size:
        raise OSError(f"boyut tutmadı: {got} != {size}")
    if not sha256_expected:
        return
    h = hashlib.sha256()
    with open(tmp, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    if h.hexdigest() != sha256_expected:
        raise OSError(f"sha256 tutmadı: {h.hexdigest()} != {sha256_expected}")


def measure(packs):
    """Paketlerin toplam boyutu (HF'deki dosya boyutlarından), inmiş dosyalar sayılarak. Bir paketin
    dosya bilgisi alınamazsa yalnızca o paket düşer: {paket: hata} döner, diğerleri ölçülür."""
    items = {(repo, fn) for pack in packs for repo, fn, _ in PACKS[pack]}
    with ThreadPoolExecutor(min(8, len(items) or 1)) as ex:
        results = dict(ex.map(_try_meta, items))
    failed = {}
    for pack in packs:
        total = done = 0
        for repo, filename, folder in PACKS[pack]:
            meta = results[(repo, filename)]
            if isinstance(meta, str):
                failed[pack] = meta
                break
            _meta[(repo, filename)] = meta
            total += meta[0]
            if (MODELS / folder / os.path.basename(filename)).exists():
                done += meta[0]
        else:
            progress[pack] = {"total": total, "done": done}
    return failed


def download(repo, filename, target, attempts=3):
    """Dosyayı 8 paralel HTTP Range akışıyla doğrudan hedefe indirir.
    hf_xet yüzlerce küçük xorb isteği atarak CDN'in 429 hız sınırına takılıyor (~3 dk duraksama);
    büyük Range istekleri hem 429'a takılmaz hem de ~350-650 MB/s hız verir."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    for attempt in range(1, attempts + 1):
        current["bytes"] = 0
        size = 0
        try:
            size, url, etag = _meta_for(repo, filename, attempt)
            workers = max(1, min(8, size // (32 * 1024 * 1024)))
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
            os.ftruncate(fd, size)
            chunk = (size + workers - 1) // workers

            def part(i):
                lo, hi = i * chunk, min(size, (i + 1) * chunk) - 1
                if lo > hi:
                    return
                pos = lo
                for sub in range(1, 4):
                    try:
                        req = urllib.request.Request(url, headers={"Range": f"bytes={pos}-{hi}"})
                        with urllib.request.urlopen(req, timeout=60) as r:
                            # Range yok sayılırsa (200) gövde baştan gelir, dosyaya yanlış ofsetle yazılırdı.
                            if r.status != 206 or not r.headers.get("Content-Range", "").startswith(f"bytes {pos}-{hi}/"):
                                raise OSError(f"Range yanıtı beklenmedik: HTTP {r.status} {r.headers.get('Content-Range')}")
                            while pos <= hi and (buf := r.read(4 * 1024 * 1024)):
                                view = memoryview(buf)
                                while view:  # disk dolunca pwrite kısa yazabilir
                                    n = os.pwrite(fd, view, pos)
                                    view, pos = view[n:], pos + n
                                    with _bytes_lock:
                                        current["bytes"] += n
                        # Bağlantı erken kapanınca read() hata vermeden b"" döner; kalan kısım yeniden istenir.
                        if pos <= hi:
                            raise OSError(f"bağlantı erken kapandı ({hi + 1 - pos} bayt eksik)")
                        return
                    except Exception as e:
                        # Disk dolduysa yeniden denemek boşuna: hemen net hata.
                        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
                            raise
                        if sub == 3:
                            raise
                        time.sleep(2 * sub)

            try:
                with ThreadPoolExecutor(workers) as ex:
                    for f in [ex.submit(part, i) for i in range(workers)]:
                        f.result()
            finally:
                os.close(fd)
            # Bütünlük: boyut ve (LFS ise) sha256 HF'dekiyle aynı olmalı; değilse .part silinip
            # aynı dosya yeniden indirilir.
            _verify(tmp, size, _etag_sha256(etag))
            os.replace(tmp, target)
            return
        except OSError as e:
            tmp.unlink(missing_ok=True)
            if e.errno == errno.ENOSPC:
                wanted = f" ({_fmt(size)} isteniyor)" if size else ""
                raise disk_error(f"{target}{wanted}", e) from e
            if attempt == attempts:
                raise
            print(f"{filename}: indirme koptu ({type(e).__name__}: {e}), yeniden deneniyor", flush=True)
            time.sleep(5 * attempt)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            if attempt == attempts:
                raise
            print(f"{filename}: indirme koptu ({type(e).__name__}: {e}), yeniden deneniyor", flush=True)
            time.sleep(5 * attempt)


def priority():
    """sunucu, bir iş belirli bir paketi beklerken adını /content/packs/priority'ye yazar."""
    try:
        return (PACKS_DIR / "priority").read_text().strip()
    except OSError:
        return None


def pick(pending):
    """Sıradaki paket: bir iş birini bekliyorsa o, yoksa verilen sıradaki ilk."""
    first = priority()
    return first if first in pending else pending[0]


def fetch(pack, pending):
    """Paketi dosya dosya indirir. Başka bir bekleyen paket öncelik alırsa dosya sınırında bırakır
    (False döner); indirilen dosyalar yerinde kaldığı için sonra kaldığı yerden sürer."""
    current.update(pack=pack, base=progress[pack]["done"], bytes=0)
    for repo, filename, folder in PACKS[pack]:
        target = MODELS / folder / os.path.basename(filename)
        if target.exists():
            continue
        first = priority()
        if first and first != pack and first in pending:
            current["pack"] = None
            return False
        download(repo, filename, target)
        progress[pack]["done"] += os.path.getsize(target)
        current.update(base=progress[pack]["done"], bytes=0)
        print(f"{pack}: {target.name}", flush=True)
    current["pack"] = None
    return True


def wait_comfy(timeout=180):
    """İlk paket indikten sonra ComfyUI açılana kadar diğer paketleri bekletir:
    72 GB arka plan indirmesi diski doyurursa ComfyUI'nin ilk import'ları ~3 dk uzuyor."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(1)


def main():
    PACKS_DIR.mkdir(parents=True, exist_ok=True)
    # Açılışı tetikleyen işin paketi (PRIORITY_PACK) ilk iner; geri kalanı verilen sırayla.
    order = list(dict.fromkeys([p for p in [os.environ.get("PRIORITY_PACK")] + sys.argv[1:] if p]))
    clean_state(order)  # aynı runtime'da yeniden koşma: bayat .error/.done ve .part artıkları
    # Bir paketin HF hatası yalnızca o paketin .error dosyasına yazılır; açılışı tetikleyen paket
    # düşerse açılış da düşer (kurulum betiği onun .error'unu görüp çıkar).
    failed = measure(order)
    for pack, error in failed.items():
        (PACKS_DIR / f"{pack}.error").write_text(error)
        order.remove(pack)
    if failed:
        print(f"ölçülemeyen paketler: {', '.join(failed)}", flush=True)
    if os.environ.get("PRIORITY_PACK") in failed:
        raise SystemExit(f"{os.environ['PRIORITY_PACK']} paketi ölçülemedi:\n{failed[os.environ['PRIORITY_PACK']]}")
    # Disk yetmiyorsa indirmeye hiç başlanmaz: hangi paketin ne kadar istediği tek mesajda.
    check_disk(order)
    threading.Thread(target=progress_writer, daemon=True).start()
    pending, waited = list(order), False
    while pending:
        pack = pick(pending)
        if len(pending) < len(order) and not waited:
            wait_comfy()
            waited = True
        try:
            if fetch(pack, pending):
                # Paket bütünüyle indi: varsa eski hata dosyası kalıcı zehir olmasın.
                (PACKS_DIR / f"{pack}.error").unlink(missing_ok=True)
                (PACKS_DIR / f"{pack}.done").touch()
                pending.remove(pack)
        except Exception:
            (PACKS_DIR / f"{pack}.error").write_text(traceback.format_exc())
            pending.remove(pack)
        # SystemExit (disk doldu) yukarıdaki except'e takılmaz: açık hatayla çıkar.
    try:
        write_progress()
    except OSError as e:
        raise disk_error("progress.json", e) from e


if __name__ == "__main__":
    main()
