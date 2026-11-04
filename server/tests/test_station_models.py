"""station/models.py: indirme sırası (bir işin beklediği paket öne geçer, yarım kalan paket indirilen
dosyaları kaybetmeden sonra kaldığı yerden sürer), Range indiricisinin bozuk yanıtlara dayanıklılığı,
indirme bütünlüğü (boyut + LFS sha256), disk koruması ve .error dosyasının kalıcı zehirlenmemesi.
huggingface_hub GPU runtime'ında olduğu için burada sahte bir modülle yüklenir."""
import hashlib
import http.server
import importlib.util
import sys
import threading
import types
from pathlib import Path

import pytest

MODELS_PY = Path(__file__).resolve().parent.parent.parent / "station" / "models.py"


@pytest.fixture
def models(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub",
                        types.SimpleNamespace(get_hf_file_metadata=None, hf_hub_url=None))
    spec = importlib.util.spec_from_file_location("station_models", MODELS_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.MODELS, m.PACKS_DIR = tmp_path / "models", tmp_path / "packs"
    m.DISK_ROOT = tmp_path
    m.PACKS_DIR.mkdir()
    m.PACKS = {
        "image": [("r", "a.bin", "img"), ("r", "b.bin", "img")],
        "speech": [("r", "s1.bin", "tts"), ("r", "s2.bin", "tts")],
    }
    m.progress.update({p: {"total": 20, "done": 0} for p in m.PACKS})
    downloaded = []

    def fake_download(repo, filename, target, attempts=3):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 10)
        downloaded.append(filename)

    m.real_download = m.download
    monkeypatch.setattr(m, "download", fake_download)
    m.downloaded = downloaded
    return m


def test_pick_prefers_the_pack_a_job_waits_for(models):
    assert models.pick(["image", "speech"]) == "image"
    (models.PACKS_DIR / "priority").write_text("speech\n")
    assert models.pick(["image", "speech"]) == "speech"
    assert models.pick(["image"]) == "image"  # öncelikli paket zaten inmişse (bekleyenlerde yok) sıra bozulmaz


def test_fetch_yields_at_a_file_boundary_and_resumes_without_redownloading(models):
    pending = ["image", "speech"]
    assert models.fetch("image", pending) is True and models.downloaded == ["a.bin", "b.bin"]
    models.downloaded.clear()
    models.progress["image"]["done"] = 0
    for f in ("a.bin", "b.bin"):
        (models.MODELS / "img" / f).unlink()
    # a.bin iner, sonra ses paketi öncelik alır: image yarıda bırakılır.
    original = models.download

    def download_then_prioritise(repo, filename, target, attempts=3):
        original(repo, filename, target)
        (models.PACKS_DIR / "priority").write_text("speech")

    models.download = download_then_prioritise
    assert models.fetch("image", pending) is False
    assert models.downloaded == ["a.bin"] and models.progress["image"]["done"] == 10
    models.download = original
    assert models.fetch("speech", pending) is True and models.downloaded == ["a.bin", "s1.bin", "s2.bin"]
    # image kaldığı yerden sürer: a.bin yeniden inmez.
    assert models.fetch("image", ["image"]) is True
    assert models.downloaded[-1] == "b.bin" and models.downloaded.count("a.bin") == 1
    assert models.progress["image"]["done"] == 20


def test_download_retries_short_and_rangeless_responses_instead_of_keeping_a_holey_file(models, tmp_path, monkeypatch):
    body = bytes(range(256)) * 4096  # 1 MiB
    responses = iter(["short", "ignore_range", "ok"])

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            lo = int(self.headers["Range"].removeprefix("bytes=").split("-")[0])
            kind = next(responses)
            if kind == "ignore_range":  # Range'i yok sayan sunucu: tüm gövde 200 ile
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {lo}-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(body) - lo))
            self.end_headers()
            # "short": bağlantı başlıkta söz verilen gövdenin yarısında kapanır
            self.wfile.write(body[lo:lo + (len(body) - lo) // 2] if kind == "short" else body[lo:])
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    models._meta[("r", "f.bin")] = (len(body), f"http://127.0.0.1:{server.server_port}/f.bin",
                                    f'"sha256:{hashlib.sha256(body).hexdigest()}"')
    try:
        models.real_download("r", "f.bin", tmp_path / "f.bin")
    finally:
        server.shutdown()
    # Özetle karşılaştırılır: pytest 1 MiB'lık iki bayt dizisinin farkını dakikalarca hesaplıyor.
    assert hashlib.sha256((tmp_path / "f.bin").read_bytes()).hexdigest() == hashlib.sha256(body).hexdigest()


def _range_server(body, corrupt_first=0, etag=None):
    """Gövdeyi Range ile sunan küçük sunucu. corrupt_first > 0 ise ilk o kadar yanıt aynı boyutta
    ama bozuk gövde döner (sha256 doğrulaması bunu yakalamalı)."""
    state = {"left": corrupt_first, "url": None}
    bad = body[:-1] + bytes([body[-1] ^ 0xFF])  # aynı boyut, farklı içerik

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            lo = int(self.headers["Range"].removeprefix("bytes=").split("-")[0])
            payload = bad if state["left"] > 0 else body
            state["left"] -= 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {lo}-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(payload) - lo))
            self.end_headers()
            self.wfile.write(payload[lo:])
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/f.bin"
    state["server"] = server
    state["meta"] = (len(body), state["url"], etag if etag is not None else f"sha256:{hashlib.sha256(body).hexdigest()}")
    return state


def test_download_redownloads_when_the_lfs_sha256_does_not_match(models, tmp_path, monkeypatch):
    """LFS etag'i sha256 taşır: bozuk inen dosya .part'ta bırakılıp aynı denemede yeniden indirilir."""
    body = bytes(range(256)) * 4096  # 1 MiB
    state = _range_server(body, corrupt_first=1)
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    # İlk denemede önbellek, ikinci denemede taze metadata kullanılır: ikisi de aynı sunucuyu göstersin.
    monkeypatch.setattr(models, "_fetch_meta", lambda item: state["meta"])
    models._meta[("r", "f.bin")] = state["meta"]
    try:
        models.real_download("r", "f.bin", tmp_path / "f.bin")
    finally:
        state["server"].shutdown()
    assert hashlib.sha256((tmp_path / "f.bin").read_bytes()).hexdigest() == hashlib.sha256(body).hexdigest()
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_a_file_that_stays_corrupt_and_leaves_no_partial_file(models, tmp_path, monkeypatch):
    """Bozuk gövde 3 denemede de düzelmezse hata verilir; .part ve hedef dosya kalmaz."""
    body = bytes(range(256)) * 4096
    state = _range_server(body, corrupt_first=99)
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    monkeypatch.setattr(models, "_fetch_meta", lambda item: state["meta"])
    models._meta[("r", "f.bin")] = state["meta"]
    try:
        with pytest.raises(OSError, match="sha256 tutmadı"):
            models.real_download("r", "f.bin", tmp_path / "f.bin")
    finally:
        state["server"].shutdown()
    assert not (tmp_path / "f.bin").exists() and not list(tmp_path.glob("*.part"))


def test_verify_checks_size_and_sha256(models, tmp_path):
    """Boyut ve sha256 ayrı ayrı doğrulanır; LFS olmayan (sha1) etag'de sha256 atlanır."""
    target = tmp_path / "v.bin"
    target.write_bytes(b"abcdef")
    models._verify(target, 6, hashlib.sha256(b"abcdef").hexdigest())
    models._verify(target, 6, None)  # sha1 etag: yalnızca boyut
    with pytest.raises(OSError, match="boyut tutmadı"):
        models._verify(target, 7, None)
    with pytest.raises(OSError, match="sha256 tutmadı"):
        models._verify(target, 6, hashlib.sha256(b"abcdeg").hexdigest())


def test_fetch_meta_retries_transient_hf_errors_with_backoff(models, monkeypatch):
    """measure() tek bir HF 500 yüzünden paketi kalıcı olarak .error'a yazmaz: kısa backoff'la yeniden dener."""
    calls, sleeps = [], []

    def flaky(url):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("HF 500")
        return types.SimpleNamespace(size=42, location="http://hf/x", etag='"sha256:' + "0" * 64 + '"')

    monkeypatch.setattr(models, "get_hf_file_metadata", flaky)
    monkeypatch.setattr(models, "hf_hub_url", lambda repo, fn: f"http://hf/{repo}/{fn}")
    monkeypatch.setattr(models.time, "sleep", sleeps.append)
    assert models._fetch_meta(("r", "f.bin")) == (42, "http://hf/x", '"sha256:' + "0" * 64 + '"')
    assert len(calls) == 3 and sleeps == [2, 4]


def test_disk_guard_refuses_to_start_and_names_the_pack(models, monkeypatch):
    """İndirilecek bayt /content'in boş alanını aşarsa hangi paketin ne kadar istediği yazılır."""
    models.progress.clear()
    models.progress.update({"image": {"total": 17 * 1024 ** 3, "done": 0},
                            "speech": {"total": 3 * 1024 ** 3, "done": 0}})
    monkeypatch.setattr(models, "shutil", types.SimpleNamespace(disk_usage=lambda p: types.SimpleNamespace(free=10 ** 9)))
    with pytest.raises(SystemExit) as e:
        models.check_disk(["image", "speech"])
    message = str(e.value)
    assert "disk yetersiz" in message and "image: 17.0 GiB" in message and "speech: 3.0 GiB" in message
    assert "eksik 19.1 GiB" in message
    # Yer varken sessiz kalır.
    monkeypatch.setattr(models, "shutil", types.SimpleNamespace(disk_usage=lambda p: types.SimpleNamespace(free=10 ** 11)))
    models.check_disk(["image", "speech"])


def test_download_reports_disk_full_instead_of_retrying(models, tmp_path, monkeypatch):
    """ENOSPC'de yeniden denemek yerine /content'in boş alanını söyleyen SystemExit verilir."""
    body = b"x" * 4096
    state = _range_server(body)
    models._meta[("r", "f.bin")] = state["meta"]
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    monkeypatch.setattr(models, "shutil", types.SimpleNamespace(disk_usage=lambda p: types.SimpleNamespace(free=1234)))

    def enospc_open(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(models.os, "open", enospc_open)
    try:
        with pytest.raises(SystemExit) as e:
            models.real_download("r", "f.bin", tmp_path / "f.bin")
    finally:
        state["server"].shutdown()
    message = str(e.value)
    assert "disk doldu" in message and "4.0 KiB isteniyor" in message and "1.2 KiB" in message
    assert not (tmp_path / "f.bin").exists()


def test_progress_writer_stops_with_a_clear_message_when_the_disk_is_full(models, monkeypatch, capsys):
    def boom():
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(models, "write_progress", boom)
    monkeypatch.setattr(models, "shutil", types.SimpleNamespace(disk_usage=lambda p: types.SimpleNamespace(free=0)))
    models.progress_writer()  # ENOSPC'de döngüden çıkar (sonsuz dönmez)
    assert "disk doldu" in capsys.readouterr().err


def test_main_clears_stale_error_and_part_files_at_start(models, monkeypatch):
    """Aynı runtime'da yeniden koşma: bayat .error/.done ve .part artıkları indirmeyi zehirlemez."""
    stale = models.PACKS_DIR / "speech.error"
    stale.write_text("eski HF hatası")
    (models.PACKS_DIR / "speech.done").touch()
    part = models.MODELS / "tts" / "s1.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"yarim")
    monkeypatch.setattr(models, "_fetch_meta", lambda item: (10, "http://hf/x", None))
    monkeypatch.setattr(models, "wait_comfy", lambda: None)
    monkeypatch.setattr(models.sys, "argv", ["models.py", "image", "speech"])
    monkeypatch.setattr(models, "progress_writer", lambda: None)
    monkeypatch.setenv("PRIORITY_PACK", "image")
    models.main()
    assert not stale.exists() and not part.exists()
    assert (models.PACKS_DIR / "speech.done").exists()  # yeniden indi → .done tazelendi


def test_main_deletes_the_error_file_once_the_pack_downloads(models, monkeypatch):
    """Bir kez yazılan <paket>.error paket başarıyla inince silinir: mod runtime kapanana kadar kilitli kalmaz."""
    monkeypatch.setattr(models, "clean_state", lambda packs: None)  # başlangıç temizliğini kapat
    error = models.PACKS_DIR / "speech.error"
    error.write_text("geçici HF hatası")
    monkeypatch.setattr(models, "_fetch_meta", lambda item: (10, "http://hf/x", None))
    monkeypatch.setattr(models, "wait_comfy", lambda: None)
    monkeypatch.setattr(models.sys, "argv", ["models.py", "speech"])
    monkeypatch.setattr(models, "progress_writer", lambda: None)
    monkeypatch.setenv("PRIORITY_PACK", "speech")
    models.main()
    assert not error.exists() and (models.PACKS_DIR / "speech.done").exists()


def test_measure_failure_of_one_pack_does_not_drop_the_others(models, monkeypatch):
    """Bir paketin dosya bilgisi alınamazsa (HF hatası) yalnızca o paket düşer."""
    models.progress.clear()

    def fake_meta(item):
        _, filename = item
        if filename == "s2.bin":
            raise OSError("HF 500")
        return (10, f"http://hf/{filename}", None)

    monkeypatch.setattr(models, "_fetch_meta", fake_meta)
    failed = models.measure(["image", "speech"])
    assert list(failed) == ["speech"] and "HF 500" in failed["speech"]
    assert models.progress == {"image": {"total": 20, "done": 0}}
    assert models._meta[("r", "a.bin")] == (10, "http://hf/a.bin", None)


def test_main_writes_error_only_for_the_failed_pack_and_aborts_when_it_is_the_trigger(models, monkeypatch):
    def fake_meta(item):
        if item[1] == "s2.bin":
            raise OSError("HF 500")
        return (10, "http://hf/x", None)

    monkeypatch.setattr(models, "_fetch_meta", fake_meta)
    monkeypatch.setattr(models, "wait_comfy", lambda: None)
    monkeypatch.setattr(models.sys, "argv", ["models.py", "image", "speech"])
    monkeypatch.setattr(models, "progress_writer", lambda: None)  # ilerleme yazıcısı gerekmez
    for trigger, aborts in (("image", False), ("speech", True)):
        for f in models.PACKS_DIR.iterdir():
            f.unlink()
        models.progress.clear()
        monkeypatch.setenv("PRIORITY_PACK", trigger)
        if aborts:
            with pytest.raises(SystemExit):
                models.main()
        else:
            models.main()
        assert "HF 500" in (models.PACKS_DIR / "speech.error").read_text()
        assert not (models.PACKS_DIR / "image.error").exists()
        assert (models.PACKS_DIR / "image.done").exists() is (not aborts)
