"""Servis durumunun kalıcı ve hesaplanan parçaları: yeniden başlatmada kurtarma, kredi
istatistikleri, açılış ilerlemesi ve hata metinleri."""
import asyncio
import json
import time

import runtime
import studio


async def test_init_recovers_interrupted_jobs(db):
    a = studio.create_job("t", "image", "image", "a", {}, [])
    b = studio.create_job("t", "image", "image", "b", {}, [])
    c = studio.create_job("t", "image", "image", "c", {}, [])
    studio._set_status(a["id"], "running")
    studio._set_status(b["id"], "waiting_gpu")
    while not studio.queue.empty():
        studio.queue.get_nowait()
    studio.db.close()
    studio.init()
    assert studio.get_job(a["id"])["status"] == "error"
    assert studio.get_job(a["id"])["error"] == "Servis yeniden başladı"
    assert studio.get_job(b["id"])["status"] == "queued"
    queued = [studio.queue.get_nowait()[2] for _ in range(studio.queue.qsize())]
    assert queued == [b["id"], c["id"]]


async def test_queue_info_current_is_the_active_job(db):
    a = studio.create_job("t", "image", "image", "a", {}, [])
    b = studio.create_job("t", "image", "image", "b", {}, [])
    assert studio.queue_info()["current"] is None
    studio._set_status(a["id"], "running", stage="Üretiliyor", progress=0.5)
    info = studio.queue_info()
    assert info["order"] == [a["id"], b["id"]]
    assert info["current"]["id"] == a["id"] and info["current"]["stage"] == "Üretiliyor"


async def test_credit_stats_count_only_spending(db, monkeypatch):
    now = time.time()
    for ts, bal in [(now - 3000, 100.0), (now - 2000, 99.0), (now - 1000, 150.0), (now - 10, 149.5)]:
        studio.db.execute("INSERT INTO credit_samples VALUES (?,?)", (ts, bal))
    studio.db.commit()
    s = studio.credit_stats()
    assert round(s["week"], 2) == 1.5  # 100 -> 99 ve 150 -> 149.5; kredi alımı (99 -> 150) sayılmaz
    assert s["per_day"] > 0


async def test_credit_stats_empty(db):
    assert studio.credit_stats() is None


async def test_record_credits_samples_every_five_minutes(db):
    studio.record_credits({"balance": 10.0, "rate": 1})
    studio.record_credits({"balance": 9.0, "rate": 1})
    assert studio.db.execute("SELECT COUNT(*) FROM credit_samples").fetchone()[0] == 1


def test_short_error_keeps_head_and_tail():
    out = "satır\n" * 300 + "RuntimeError: asıl hata burada"
    msg = runtime.short_error(RuntimeError(f"komut başarısız (1): ssh colab bash setup.sh\n{out}"))
    assert len(msg) <= 700
    assert msg.startswith("komut başarısız (1): ssh colab bash setup.sh")
    assert msg.endswith("RuntimeError: asıl hata burada")
    assert runtime.short_error(ValueError("kısa")) == "kısa"
    assert runtime.short_error(TimeoutError()) == "TimeoutError"


def test_parse_usage():
    u = runtime.parse_usage("Current balance: 193.31 compute units\nUsage rate: 0.08 per hour")
    assert u == {"balance": 193.31, "rate": 0.08}


def test_startup_progress_bounds(data_dir, monkeypatch):
    now = time.time()
    runtime.state["startup"] = {"step": 0, "started_at": now, "step_started_at": now}
    p = runtime.startup_progress()
    assert p["progress"] < 0.05 and p["steps"][0] == "GPU ayrılıyor"
    # Adım beklenenden çok uzun sürerse ilerleme bir sonraki adıma taşmaz, kalan süre en az 5 sn.
    runtime.state["startup"] = {"step": 3, "started_at": now - 9999, "step_started_at": now - 9999}
    p = runtime.startup_progress()
    assert 0.9 < p["progress"] < 1 and p["remaining_seconds"] >= 5
    runtime.state["startup"] = None
    assert runtime.startup_progress() is None


def test_learned_durations_are_used(data_dir):
    runtime.DURATIONS_FILE.write_text(json.dumps({"install": 200}))
    exp = runtime.expected_durations()
    assert exp["install"] == 200 and exp["allocate"] == 60


def test_snapshot_session_spending(data_dir):
    runtime.state.update(phase="ready", start_balance=100.0, credits={"balance": 99.5, "rate": 0.08},
                         ready_at=time.time() - 60, last_busy=time.time() - 30, startup=None)
    s = runtime.snapshot()
    assert s["session_spent"] == 0.5 and 55 <= s["session_seconds"] <= 65 and 25 <= s["idle_seconds"] <= 35
    runtime.state.update(phase="off", start_balance=None, ready_at=None, last_busy=None, credits=None)


async def test_cleanup_deletes_only_expired(db):
    old = studio.create_thread("image", "eski")
    new = studio.create_thread("image", "yeni")
    studio.db.execute("UPDATE threads SET created_at=? WHERE id=?", (time.time() - 31 * 86400, old["id"]))
    studio.db.commit()
    # cleanup_loop sonsuz döngü; ilk turu çalıştırıp iptal edilir.
    task = asyncio.create_task(studio.cleanup_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    assert studio.get_thread(old["id"]) is None
    assert studio.get_thread(new["id"]) is not None


async def test_deleted_thread_text_does_not_survive_in_database_file(db):
    """DELETE satırı kaldırır; dosyada kalan sayfa artığından istem/başlık okunamamalı."""
    thread = studio.create_thread("image", "gizli baslik 5591")
    studio.create_job(thread["id"], "image", "image", "gizli istem metni 7712", {"seed": 1}, [])
    studio.delete_thread(thread["id"])
    raw = studio.DB_PATH.read_bytes()
    assert b"gizli istem metni 7712" not in raw
    assert b"gizli baslik 5591" not in raw


async def test_connect_retries_then_succeeds(monkeypatch):
    calls = []

    async def flaky_run(*cmd, **kw):
        calls.append(cmd)
        if len(calls) < 3:
            raise RuntimeError("komut başarısız (255): ssh -fN colab")
        return ""

    monkeypatch.setattr(runtime, "run", flaky_run)
    await runtime.connect(delay=0)
    assert len(calls) == 3


async def test_connect_gives_up_after_attempts(monkeypatch):
    async def failing_run(*cmd, **kw):
        raise RuntimeError("komut başarısız (255)")

    monkeypatch.setattr(runtime, "run", failing_run)
    import pytest

    with pytest.raises(RuntimeError):
        await runtime.connect(delay=0)


async def test_enhance_jumps_ahead_of_queued_jobs(db):
    a = studio.create_job("t", "image", "image", "a", {}, [])
    b = studio.create_job("t", "video", "video", "b", {}, [])
    e = studio.create_job(None, "image", "enhance", "e", {}, [])
    studio._set_status(a["id"], "running")
    assert studio.queue_info()["order"] == [a["id"], e["id"], b["id"]]
    order = [studio.queue.get_nowait()[2] for _ in range(studio.queue.qsize())]
    assert order == [e["id"], a["id"], b["id"]]


async def test_startup_waits_for_the_pack_of_the_job_that_started_the_gpu(data_dir, monkeypatch):
    """Ses işi GPU'yu açtırdıysa açılış görsel paketini değil ses paketini bekler ve göstergesi odur."""
    runtime.packs.clear()
    runtime._pack_samples.clear()
    gb = 2**30
    runtime.update_packs(json.dumps({"speech": {"total": 8 * gb, "done": 2 * gb}, "image": {"total": 17 * gb, "done": 0}}))
    runtime.state["startup"] = {"step": 2, "started_at": 0.0, "step_started_at": 0.0, "pack": "speech"}
    s = runtime.startup_progress()
    assert s["pack"] == "speech" and s["download"]["total"] == 8 * gb and s["download"]["fraction"] == 0.25
    runtime.state["startup"] = None
    runtime.packs.clear()
    runtime._pack_samples.clear()

    async def no_start():
        pass

    monkeypatch.setattr(runtime, "_start", no_start)
    monkeypatch.setattr(runtime, "preflight", lambda: None)  # testlerde colab CLI yok
    runtime.set_phase("off")
    runtime.start("speech")
    assert runtime.state["startup"]["pack"] == "speech"
    runtime.set_phase("off")
    runtime.start("bilinmeyen")  # tanınmayan paket image'a düşer
    assert runtime.state["startup"]["pack"] == "image"
    runtime.set_phase("off")
    runtime.start(None)  # indirilecek paketi olmayan iş (yazıya dökme): hiçbir paket beklenmez
    assert runtime.state["startup"]["pack"] is None
    assert runtime.startup_progress()["download"] is None
    await runtime._start_task
    runtime.set_phase("off")


async def test_wait_comfy_ready_fails_fast_when_process_died(monkeypatch, unused_tcp_port):
    """ComfyUI süreci yoksa 10 dakika /queue yoklanmaz: comfy.log'un sonuyla hemen hata verilir."""
    calls = []

    async def fake_run(*cmd, **kw):
        calls.append(cmd[-1])
        if cmd[-1] == runtime.COMFY_PROCESS:
            return "dead\n"
        return "ImportError: yok\n"

    monkeypatch.setattr(runtime, "COMFY_URL", f"http://127.0.0.1:{unused_tcp_port}")
    monkeypatch.setattr(runtime, "run", fake_run)
    start = time.time()
    try:
        await runtime.wait_comfy_ready()
    except RuntimeError as e:
        assert "ComfyUI süreci öldü" in str(e) and "ImportError: yok" in str(e)
    else:
        raise AssertionError("hata verilmedi")
    assert time.time() - start < 5 and any("comfy.log" in c for c in calls)


async def test_ensure_models_gives_up_when_download_stalls(db, monkeypatch):
    """İndirici yaşıyor ama bayt artmıyorsa bekleme sonsuza dek sürmez (iş "çalışıyor" kalıp GPU'yu açık tutardı)."""
    async def fake_run(*cmd, **kw):
        return ""  # ne .done ne .error: indirici sürüyor

    monkeypatch.setattr(runtime, "run", fake_run)
    monkeypatch.setattr(runtime, "MODEL_POLL_SECONDS", 0.05)
    monkeypatch.setattr(runtime, "MODEL_STALL_SECONDS", 0.3)
    runtime.packs.update({"video": {"total": 100, "done": 10}})
    start = time.time()
    try:
        await runtime.ensure_models("video")
    except RuntimeError as e:
        assert "Video modelleri" in str(e) and "takıldı" in str(e)
    else:
        raise AssertionError("hata verilmedi")
    assert time.time() - start < 3


async def test_ensure_models_keeps_waiting_while_bytes_move(db, monkeypatch):
    n = {"i": 0}

    async def fake_run(*cmd, **kw):
        n["i"] += 1
        runtime.packs["video"]["done"] += 1  # her turda ilerliyor
        return "done" if n["i"] >= 12 else ""

    monkeypatch.setattr(runtime, "run", fake_run)
    monkeypatch.setattr(runtime, "MODEL_POLL_SECONDS", 0.05)
    monkeypatch.setattr(runtime, "MODEL_STALL_SECONDS", 0.2)
    runtime.packs.update({"video": {"total": 100, "done": 10}})
    await runtime.ensure_models("video")
    assert "video" in runtime.local_modes


async def test_prune_timings_drops_keys_of_unknown_variants(db):
    """Gerçek işlerin ürettiği anahtarlar kalır; eskimiş variant'lar silinir."""
    import tracker

    jobs = [
        studio.create_job("t", "image", "image", "a", {"size": "1.5K", "steps": 25, "cfg": 3.0, "turbo": False}, []),
        studio.create_job("t", "video", "video", "v", {"quality": "768p", "seconds": 5.0, "turbo": True}, []),
        studio.create_job("t", "model3d", "model3d", "m", {"resolution": 1024, "texture": "pbr"}, []),
        studio.create_job("t", "music", "music", "m", {"seconds": 120.0}, []),
        studio.create_job("t", "speech", "speech", "s", {"engine": "qwen3"}, []),
        studio.create_job("t", "transcribe", "transcribe", "x", {"language": "auto"}, []),
        studio.create_job(None, "image", "enhance", "e", {}, []),
    ]
    good = {f"{j['kind']}|{tracker.variant(j)}|{t}" for j in jobs for t in ("total", "cold|sampler")}
    good.add("image|edit720px2-s8-cfg1-turbo|warm|sampler")
    bad = {"image|1.5K-s25|total", "video|720p-5s-turbo|total", "model3d|4096-pbr|cold|save", "legacy|x|total", "image"}
    studio.learn_timings({k: 1.0 for k in good | bad})
    assert set(studio.prune_timings()) == bad
    assert set(studio.timings()) == good


async def test_cleanup_loop_survives_a_failing_turn(db, monkeypatch):
    """Tek bir turun hatası (disk, veritabanı) temizlik döngüsünü durdurmamalı: durursa saklama
    süresi hiç işlemez ve medya süpürülmez."""
    calls = []

    def fake_once():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk hatası")

    monkeypatch.setattr(studio, "cleanup_once", fake_once)
    monkeypatch.setattr(studio, "CLEANUP_SECONDS", 0.01)
    task = asyncio.create_task(studio.cleanup_loop())
    await asyncio.sleep(0.2)
    assert not task.done()  # hata döngüyü durdurmadı
    assert len(calls) >= 3  # sonraki turlar da çalıştı
    task.cancel()
    assert any("temizlik turu başarısız" in line for line in runtime.logs)


async def test_corrupt_durations_file_falls_back_to_defaults(db):
    """Yarım yazılmış startup_durations.json arayüzü 500'e düşürmemeli; varsayılanlar kullanılır."""
    runtime.DURATIONS_FILE.write_text('{"allocate": 8.1, "conne')
    assert runtime.expected_durations()["allocate"] == runtime.STEPS[0][2]
    runtime.state["phase"] = "starting"
    runtime.state["startup"] = {"step": 1, "started_at": time.time(), "step_started_at": time.time(), "pack": "image"}
    progress = runtime.startup_progress()
    assert 0 <= progress["progress"] <= 0.99 and progress["remaining_seconds"] >= 5
    runtime.state["startup"] = None
    runtime.set_phase("off")


async def test_learned_duration_written_atomically(db):
    """Süre dosyası yerine atomik yazılır: yarım dosya ve artık .tmp kalmaz."""
    runtime.state["phase"] = "starting"
    runtime.state["startup"] = {"step": 1, "started_at": time.time(), "step_started_at": time.time() - 12, "pack": "image"}
    runtime._learn(0)
    learned = json.loads(runtime.DURATIONS_FILE.read_text())
    assert 6 <= learned["allocate"] <= 36  # (60 + 12) / 2
    assert not runtime.DURATIONS_FILE.with_suffix(".json.tmp").exists()
    runtime.state["startup"] = None
    runtime.set_phase("off")


PREFLIGHT = runtime.preflight  # db fikstürü testlerde bunu etkisizleştiriyor


async def test_missing_colab_cli_is_reported_instead_of_starting(db, monkeypatch, tmp_path):
    """colab CLI yoksa açılış hiç denenmez; arayüz ne yapılacağını söyleyen hatayı gösterir."""
    monkeypatch.setattr(runtime, "preflight", PREFLIGHT)
    monkeypatch.setattr(runtime, "COLAB", str(tmp_path / "yok"))
    calls = []
    monkeypatch.setattr(runtime, "run", lambda *a, **k: calls.append(a))
    runtime.start()
    assert runtime.state["phase"] == "error"
    assert "colab CLI bulunamadı" in runtime.state["detail"] and "install.sh" in runtime.state["detail"]
    assert calls == []


async def test_missing_colab_login_is_reported(db, monkeypatch, tmp_path):
    colab = tmp_path / "colab"
    colab.write_text("#!/bin/sh\n")
    colab.chmod(0o755)
    monkeypatch.setattr(runtime, "COLAB", str(colab))
    monkeypatch.setattr(runtime, "COLAB_TOKEN", tmp_path / "token.json")
    assert "colab usage" in PREFLIGHT()
