"""İlerleme ve kalan süre: öğrenilmiş düğüm sürelerinden tahmin, adım hızı, önbellekten gelen
düğümler, birden çok grafik ve model indirme ilerlemesi."""
import json

import pytest

import runtime
import tracker

JOB = {"kind": "model3d", "params": {"resolution": 1024, "texture": "pbr"}, "inputs": ["a.png"]}
GRAPH = {
    "unet": {"class_type": "UNETLoader"},
    "sample": {"class_type": "KSampler"},
    "unwrap": {"class_type": "UnwrapMesh"},
    "save": {"class_type": "SaveGLB"},
}
P = "model3d|1024-pbr"


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(tracker.time, "time", lambda: now["t"])
    return now


def test_unknown_job_has_no_eta_and_uses_sampler_steps(clock):
    t = tracker.Tracker({}, JOB, [GRAPH], cold=True)
    t.start_graph(0)
    t.executing("unet")
    assert t.snapshot()["eta"] is None and t.snapshot()["progress"] == 0
    t.executing("sample")
    assert t.snapshot()["loading"] is True
    t.progress(5, 10)
    s = t.snapshot()
    assert s["progress"] == 0.5 and not s["loading"]


def test_eta_from_learned_nodes_and_measured_step_rate(clock):
    learned = {f"{P}|cold|unet": 30, f"{P}|cold|sample": 100, f"{P}|cold|unwrap": 140, f"{P}|cold|save": 2}
    t = tracker.Tracker(learned, JOB, [GRAPH], cold=True)
    t.start_graph(0)
    t.executing("unet")
    assert t.snapshot()["eta"] == 272
    clock["t"] += 10
    assert t.snapshot()["eta"] == 262 and t.snapshot()["stage_seconds"] == 10 and t.snapshot()["stage_expected"] == 30
    clock["t"] += 50  # yükleme beklenenden uzun: kalan süre eksiye düşmez, çubuk geri gitmez
    p = t.snapshot()["progress"]
    assert t.snapshot()["eta"] == 242
    t.executing("sample")
    t.progress(1, 21)
    clock["t"] += 20
    t.progress(11, 21)  # adım 2 sn: kalan 10 adım 20 sn
    s = t.snapshot()
    assert s["eta"] == 20 + 140 + 2 and s["progress"] >= p


def test_progress_never_goes_back_and_caps_below_done(clock):
    t = tracker.Tracker({f"{P}|cold|sample": 10}, JOB, [GRAPH], cold=True)
    t.start_graph(0)
    t.executing("sample")
    seen = []
    for _ in range(30):
        clock["t"] += 1
        seen.append(t.snapshot()["progress"])
    assert seen == sorted(seen) and max(seen) <= 0.99


def test_cached_nodes_do_not_count(clock):
    learned = {f"{P}|warm|unet": 30, f"{P}|warm|sample": 100, f"{P}|warm|unwrap": 140, f"{P}|warm|save": 2}
    t = tracker.Tracker(learned, JOB, [GRAPH], cold=False)
    t.start_graph(0)
    t.cached(["unet"])
    t.executing("sample")
    assert t.snapshot()["eta"] == 242


def test_warm_job_falls_back_to_cold_timings(clock):
    t = tracker.Tracker({f"{P}|cold|sample": 50}, JOB, [GRAPH], cold=False)
    t.start_graph(0)
    t.executing("sample")
    assert t.snapshot()["eta"] == 50


def test_later_graphs_use_this_jobs_last_graph(clock):
    job = {"kind": "image", "params": {"size": "1K", "steps": 20, "cfg": 3}, "inputs": []}
    g = {"unet": {"class_type": "UNETLoader"}, "sampler": {"class_type": "KSampler"}}
    pre = f"image|{tracker.variant(job)}"
    t = tracker.Tracker({f"{pre}|cold|unet": 40, f"{pre}|cold|sampler": 30, f"{pre}|graph|warm": 31}, job, [g, g, g], cold=True)
    t.start_graph(0)
    t.executing("unet")
    assert t.snapshot()["eta"] == 40 + 30 + 31 + 31
    clock["t"] += 40
    t.executing("sampler")
    clock["t"] += 30
    t.executing(None)
    t.start_graph(1)
    t.cached(["unet"])
    t.executing("sampler")
    # 2. grafik başlarken 3. grafik için öğrenilmiş sıcak grafik süresi (31) kullanılır;
    # 2. grafik bitince bu işte ölçülen süre (25) onun yerine geçer.
    assert t.snapshot()["eta"] == 30 + 31
    clock["t"] += 25
    t.executing(None)
    t.start_graph(2)
    t.cached(["unet"])
    t.executing("sampler")
    assert t.snapshot()["eta"] == 30  # sıcak örnekleyici ölçümü yok, soğuk ölçüm (30)
    clock["t"] += 25
    t.finish()
    learned = t.learned()
    assert learned[f"{pre}|graph|cold"] == 70 and learned[f"{pre}|graph|warm"] == 25
    assert learned[f"{pre}|cold|unet"] == 40 and learned[f"{pre}|warm|sampler"] == 25
    assert learned[f"{pre}|total"] == 120


def test_variant_tells_apart_settings_that_change_duration():
    image = {"kind": "image", "params": {"size": "1K", "steps": 20, "cfg": 1, "edit_resolution": "1080p"}, "inputs": []}
    assert tracker.variant(image) != tracker.variant({**image, "params": {**image["params"], "cfg": 3}})
    assert tracker.variant(image) != tracker.variant({**image, "inputs": ["a.png"]})
    assert tracker.variant(JOB) != tracker.variant({**JOB, "params": {"resolution": 2048, "texture": "pbr"}})
    assert tracker.variant({"kind": "image", "params": {}, "inputs": []})  # eski kayıtlar da çalışır


# --- model indirme ilerlemesi ---

def test_pack_status_rate_and_eta_counts_packs_ahead(data_dir, monkeypatch):
    runtime.packs.clear()
    runtime._pack_samples.clear()
    now = {"t": 0.0}
    monkeypatch.setattr(runtime.time, "time", lambda: now["t"])
    gb = 2**30
    runtime.update_packs(json.dumps({"image": {"total": 10 * gb, "done": 2 * gb}, "video": {"total": 20 * gb, "done": 0}}))
    assert runtime.pack_status("image")["rate"] is None
    now["t"] = 10.0
    runtime.update_packs(json.dumps({"image": {"total": 10 * gb, "done": 3 * gb}, "video": {"total": 20 * gb, "done": 0}}))
    image, video = runtime.pack_status("image"), runtime.pack_status("video")
    assert image["rate"] == gb / 10 and image["fraction"] == 0.3
    assert image["eta"] == 70 and video["eta"] == 270  # görsel bitmeden video başlamaz
    runtime.update_packs("")  # dosya henüz yokken boş gelir
    assert runtime.pack_status("image")["done"] == 3 * gb
    assert runtime.pack_status("music") is None


def test_startup_install_step_follows_download(data_dir, monkeypatch):
    runtime.packs.clear()
    runtime._pack_samples.clear()
    now = {"t": 100.0}
    monkeypatch.setattr(runtime.time, "time", lambda: now["t"])
    runtime.state["startup"] = {"step": 2, "started_at": 0.0, "step_started_at": 90.0}
    gb = 2**30
    runtime.update_packs(json.dumps({"image": {"total": 16 * gb, "done": 4 * gb}}))
    now["t"] = 110.0
    runtime.update_packs(json.dumps({"image": {"total": 16 * gb, "done": 6 * gb}}))
    runtime.setup_phase["text"] = "3/5 ComfyUI ve ses paketleri kuruluyor"
    s = runtime.startup_progress()
    exp = runtime.expected_durations()
    assert s["download"]["fraction"] == 6 / 16 and s["setup"].startswith("3/5")
    # İndirmenin kalanı (50 sn) öğrenilmiş kurulum kalanından kısaysa kurulum belirler; uzunsa indirme.
    assert s["remaining_seconds"] == round(max(exp["install"] - 20, 50) + exp["comfy"])
    assert 0 < s["progress"] < 1 and s["step_elapsed_seconds"] == 20
    # İndirme duraksarsa (xet dosyayı diske yazıyor) kalan süre büyür ama çubuk geri gitmez.
    first = s["progress"]
    now["t"] = 400.0
    runtime.update_packs(json.dumps({"image": {"total": 16 * gb, "done": 6 * gb}}))
    s = runtime.startup_progress()
    assert s["progress"] >= first and s["remaining_seconds"] < 3600
    runtime.state["startup"] = None
    runtime.setup_phase["text"] = None


def test_stalled_download_keeps_a_sane_eta(data_dir, monkeypatch):
    runtime.packs.clear()
    runtime._pack_samples.clear()
    now = {"t": 0.0}
    monkeypatch.setattr(runtime.time, "time", lambda: now["t"])
    gb = 2**30
    for t, done in [(0, 0), (30, 15 * gb), (150, 15 * gb)]:  # 30 sn'de 15 GB, sonra 2 dk duraksama
        now["t"] = t
        runtime.update_packs(json.dumps({"image": {"total": 17 * gb, "done": done}}))
    st = runtime.pack_status("image")
    assert st["rate"] == 15 * gb / 150 and st["eta"] == 20
