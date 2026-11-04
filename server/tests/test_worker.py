"""İş kuyruğu uçtan uca: sahte bir ComfyUI sunucusu grafikleri 'çalıştırır', worker işleri alıp
sonuçları indirir. GPU açılışı ve model paketleri burada atlanır (runtime hazır sayılır)."""
import asyncio
import json
import uuid

import pytest
from aiohttp import web

import runtime
import tracker
import studio
import workflows


class FakeComfy:
    """ComfyUI'nin stüdyonun kullandığı uçları: /upload/image, /prompt, /ws, /history, /view."""

    def __init__(self):
        self.prompts = []
        self.uploads = []
        self.fail_with = None
        self.reject = False
        self.sockets = []
        self.slow = False
        self.drop_ws = False  # ilk düğümden sonra websocket bağlantısını keser
        self.done = set()
        self.interrupted = asyncio.Event()

    def app(self):
        app = web.Application()
        app.add_routes([
            web.post("/upload/image", self.upload),
            web.post("/prompt", self.prompt),
            web.get("/ws", self.ws),
            web.get("/history/{pid}", self.history),
            web.get("/view", self.view),
            web.post("/interrupt", self.interrupt),
        ])
        return app

    async def upload(self, request):
        form = await request.post()
        f = form["image"]
        self.uploads.append(f.filename)
        return web.json_response({"name": f.filename, "subfolder": "", "type": "input"})

    async def prompt(self, request):
        body = await request.json()
        if self.reject:
            return web.json_response({"error": {"message": "Prompt outputs failed validation"}}, status=400)
        pid = uuid.uuid4().hex
        self.prompts.append((pid, body["prompt"]))
        asyncio.get_running_loop().create_task(self.run(pid, body["prompt"]))
        return web.json_response({"prompt_id": pid})

    async def interrupt(self, request):
        self.interrupted.set()
        return web.Response()

    async def run(self, pid, graph):
        await asyncio.sleep(0.05)
        if self.slow:  # uzun süren iş; /interrupt gelince kesilir
            await self.send({"type": "executing", "data": {"node": "sampler", "prompt_id": pid}})
            await self.interrupted.wait()
            await self.send({"type": "execution_interrupted", "data": {"prompt_id": pid}})
            return
        for node_id in graph:
            await self.send({"type": "executing", "data": {"node": node_id, "prompt_id": pid}})
            if self.drop_ws:
                for ws in self.sockets:
                    await ws.close()
                return
            if graph[node_id]["class_type"] in ("KSampler", "SamplerCustomAdvanced"):
                for v in (1, 2, 3):
                    await self.send({"type": "progress", "data": {"value": v, "max": 3, "prompt_id": pid}})
        if self.fail_with:
            await self.send({"type": "execution_error", "data": {"prompt_id": pid, "exception_type": "OutOfMemoryError",
                                                                 "exception_message": self.fail_with}})
            return
        self.done.add(pid)
        await self.send({"type": "executing", "data": {"node": None, "prompt_id": pid}})

    async def send(self, ev):
        # Gerçek ComfyUI de olayları yalnızca o istemcinin açık bağlantısına yollar.
        for ws in [w for w in self.sockets if not w.closed]:
            await ws.send_str(json.dumps(ev))

    async def ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        async for _ in ws:
            pass
        return ws

    def outputs(self, graph):
        cls = {n["class_type"] for n in graph.values()}
        if "PreviewAny" in cls:
            return {workflows.TEXT_NODE: {"text": ["  sonuç metni  "]}}
        if "SaveGLB" in cls:  # gerçek SaveGLB çıktısının biçimi (L4'te alındı)
            return {workflows.SAVE_NODE: {"3d": [{"filename": "model3d_00001_.glb", "subfolder": "studio", "type": "output"}],
                                          "camera_info": [None], "model_3d_info": []}}
        if "SaveVideo" in cls:
            return {workflows.SAVE_NODE: {"images": [{"filename": "v_00001.mp4", "subfolder": "studio", "type": "output"}]}}
        if "SaveAudioMP3" in cls:
            return {workflows.SAVE_NODE: {"audio": [{"filename": "a_00001.mp3", "subfolder": "studio", "type": "output"}]}}
        return {workflows.SAVE_NODE: {"images": [{"filename": f"i_{uuid.uuid4().hex[:6]}.png", "subfolder": "studio", "type": "output"}]}}

    async def history(self, request):
        pid = request.match_info["pid"]
        if pid not in self.done:
            return web.json_response({})
        graph = next(g for p, g in self.prompts if p == pid)
        return web.json_response({pid: {"status": {"completed": True}, "outputs": self.outputs(graph)}})

    async def view(self, request):
        return web.Response(body=f"DATA:{request.query['filename']}".encode())


@pytest.fixture
async def comfy(db, aiohttp_server, monkeypatch):
    fake = FakeComfy()
    server = await aiohttp_server(fake.app())
    monkeypatch.setattr(runtime, "COMFY_URL", str(server.make_url("")).rstrip("/"))
    runtime.set_phase("ready", "GPU hazır")
    runtime.local_modes.update(runtime.PACKS)
    task = asyncio.create_task(studio.worker())
    yield fake
    task.cancel()


async def wait_done(jid, timeout=10):
    for _ in range(int(timeout / 0.05)):
        job = studio.get_job(jid)
        if job["status"] in ("done", "error"):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"iş bitmedi: {studio.get_job(jid)}")


def upload(name, data=b"x"):
    return studio.save_upload(data, name)


IMAGE = {"ratio": "1:1", "size": "1K", "width": 1024, "height": 1024, "count": 2, "steps": 3, "cfg": 3.0, "edit_resolution": "1080p", "edit_pixels": 1440,
         "sampler": "euler", "scheduler": "simple", "seed": None, "negative": ""}


async def test_image_job_downloads_every_output_and_records_seed(comfy):
    job = studio.create_job("t", "image", "image", "kahve", IMAGE, [])
    done = await wait_done(job["id"])
    assert done["status"] == "done", done["error"]
    assert len(done["outputs"]) == 2
    for name in done["outputs"]:
        assert (studio.MEDIA_DIR / name).read_bytes().startswith(b"DATA:i_")
    seeds = [g["sampler"]["inputs"]["seed"] for _, g in comfy.prompts]
    assert seeds[1] == seeds[0] + 1 and done["params"]["seed"] == seeds[0]
    assert done["progress"] == 1.0


async def test_image_edit_uploads_references(comfy):
    ref = upload(".png")
    job = studio.create_job("t", "image", "image", "kırmızı yap", {**IMAGE, "count": 1, "seed": 5}, [ref])
    done = await wait_done(job["id"])
    assert done["status"] == "done", done["error"]
    assert comfy.uploads == [ref.split("/")[-1]]
    assert comfy.prompts[0][1]["load1"]["inputs"]["image"] == ref.split("/")[-1]
    assert done["params"]["seed"] == 5


async def test_enhance_and_transcribe_store_text(comfy):
    e = studio.create_job(None, "image", "enhance", "kahve", {}, [])
    t = studio.create_job("t", "transcribe", "transcribe", "Ses kaydı", {"language": "Turkish"}, [upload(".mp3")])
    assert (await wait_done(e["id"]))["text"] == "sonuç metni"
    assert (await wait_done(t["id"]))["text"] == "sonuç metni"


@pytest.mark.parametrize("kind,params,suffix", [
    ("video", {"quality": "480p", "ratio": "16:9", "width": 864, "height": 480, "seconds": 3, "turbo": True, "seed": None}, ".mp4"),
    ("music", {"lyrics": "", "seconds": 30, "seed": 2}, ".mp3"),
    ("speech", {"engine": "chatterbox", "language": "Turkish", "voice_preset": "Vivian", "instruct": "", "reference_text": "",
                "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "seed": None}, ".mp3"),
])
async def test_media_jobs(comfy, kind, params, suffix):
    job = studio.create_job("t", kind, kind, "istem", params, [])
    done = await wait_done(job["id"])
    assert done["status"] == "done", done["error"]
    assert [n[-4:] for n in done["outputs"]] == [suffix]
    assert done["params"]["seed"] is not None


async def test_model3d_job_saves_glb_and_progress_never_goes_back(comfy, monkeypatch):
    """3D grafiğinde dört örnekleyici var: ilerleme çubuğu her birinde başa dönmez, aşama
    metni hangi örnekleyicinin çalıştığını söyler."""
    seen = []
    update = studio._update

    def record(jid, **fields):
        seen.append(fields)
        update(jid, **fields)

    monkeypatch.setattr(studio, "_update", record)
    ref = upload(".png")
    params = {"resolution": 1024, "texture": "vertex", "faces": 50000, "seed": None}
    job = studio.create_job("t", "model3d", "model3d", "3D model", params, [ref])
    done = await wait_done(job["id"])
    assert done["status"] == "done", done["error"]
    assert [n[-4:] for n in done["outputs"]] == [".glb"]
    assert comfy.prompts[0][1]["load"]["inputs"]["image"] == ref.split("/")[-1]
    assert done["params"]["seed"] is not None and done["params"]["texture"] == "vertex"
    progress = [f["progress"] for f in seen if "progress" in f]
    assert progress == sorted(progress) and progress[-1] == 1.0
    stages = [f["stage"] for f in seen if f.get("stage")]
    for title in ("Kaba yapı üretiliyor", "Şekil üretiliyor", "Şekil ayrıntılandırılıyor", "Doku üretiliyor",
                  "Arka plan ayrılıyor", "Renkler boyanıyor"):
        assert any(st.startswith(title) for st in stages), title


async def test_speech_clone_uses_uploaded_voice(comfy):
    voice = upload(".mp3")
    p = {"engine": "chatterbox", "language": "Turkish", "voice_preset": "Vivian", "instruct": "", "reference_text": "",
         "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "seed": 1}
    done = await wait_done(studio.create_job("t", "speech", "speech", "merhaba", p, [voice])["id"])
    assert done["status"] == "done"
    assert comfy.prompts[0][1]["voice_audio"]["inputs"]["audio"] == voice.split("/")[-1]


async def test_comfy_error_is_reported_on_the_job(comfy):
    comfy.fail_with = "CUDA out of memory"
    done = await wait_done(studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 1}, [])["id"])
    assert done["status"] == "error"
    assert "OutOfMemoryError" in done["error"] and "CUDA out of memory" in done["error"]


async def test_rejected_graph_is_reported(comfy):
    comfy.reject = True
    done = await wait_done(studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 1}, [])["id"])
    assert done["status"] == "error" and "ComfyUI işi reddetti" in done["error"]


async def test_jobs_run_in_order_and_queue_info_tracks_them(comfy):
    ids = [studio.create_job("t", "image", "image", f"{i}", {**IMAGE, "count": 1}, [])["id"] for i in range(3)]
    info = studio.queue_info()
    assert info["order"] == ids
    for jid in ids:
        assert (await wait_done(jid))["status"] == "done"
    assert [g["encode"]["inputs"]["prompt"] for _, g in comfy.prompts] == ["0", "1", "2"]
    assert studio.queue_info() == {"order": [], "current": None, "starts_in": {}}
    assert not studio.has_pending_jobs()


async def test_failed_gpu_start_fails_waiting_job(db, monkeypatch):
    """GPU açılamazsa bekleyen iş sonsuza dek beklemez, açılış hatasıyla biter."""
    started_with = []

    def fake_start(pack=None):
        started_with.append(pack)
        runtime.set_phase("error", "komut başarısız (1): kurulum")

    monkeypatch.setattr(runtime, "start", fake_start)
    task = asyncio.create_task(studio.worker())
    try:
        job = studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 1}, [])
        done = await wait_done(job["id"], timeout=10)
    finally:
        task.cancel()
    assert done["status"] == "error"
    assert "GPU açılamadı" in done["error"] and "kurulum" in done["error"]
    assert started_with == ["image"]


async def test_gpu_start_is_prioritised_for_the_job_that_triggers_it(db, monkeypatch):
    """Ses klonlama işi GPU'yu açtırıyorsa görsel paketi değil ses paketi ilk iner."""
    started_with = []

    def fake_start(pack=None):
        started_with.append(pack)
        runtime.set_phase("error", "x")

    monkeypatch.setattr(runtime, "start", fake_start)
    task = asyncio.create_task(studio.worker())
    try:
        p = {"engine": "qwen3", "language": "English", "voice_preset": "Vivian", "instruct": "", "reference_text": "",
             "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.9, "seed": 1}
        await wait_done(studio.create_job("t", "speech", "speech", "merhaba", p, [upload(".mp3")])["id"], timeout=10)
    finally:
        task.cancel()
    assert started_with == ["speech"]


async def test_transcribe_does_not_wait_for_any_model_pack(db, monkeypatch):
    """Yazıya dökme GPU'yu açtırıyorsa 17 GB'lık görsel paketi beklenmez (Whisper'ı eklenti kendisi indirir)."""
    started_with = []

    def fake_start(pack=None):
        started_with.append(pack)
        runtime.set_phase("error", "x")

    monkeypatch.setattr(runtime, "start", fake_start)
    task = asyncio.create_task(studio.worker())
    try:
        await wait_done(studio.create_job("t", "transcribe", "transcribe", "Ses", {"language": "auto"}, [upload(".mp3")])["id"], timeout=10)
    finally:
        task.cancel()
    assert started_with == [None]


async def test_warmup_waits_for_image_pack_downloaded_after_another_job_opened_gpu(comfy, monkeypatch):
    """GPU'yu video işi açtırdıysa görsel paketi sonradan iner; ön ısıtma onu bekler, inince yapar."""
    runtime.warm.clear()
    runtime.local_modes.discard("image")
    monkeypatch.setattr(runtime, "WARMUP_POLL_SECONDS", 0.05)
    monkeypatch.setattr(runtime, "_has_pending", studio.has_pending_jobs)
    checks = []

    async def fake_pack_state(pack):
        checks.append(pack)
        return "done" if len(checks) >= 3 else "pending"  # ilk iki yoklamada paket henüz inmedi

    monkeypatch.setattr(runtime, "pack_state", fake_pack_state)
    await asyncio.wait_for(runtime.warmup(), 5)
    assert checks == ["image"] * 3 and "image" in runtime.warm and "image" in runtime.local_modes


async def test_cancel_while_gpu_starts_stops_it_unless_other_jobs_wait(db, monkeypatch):
    """GPU açılmayı beklerken iş iptal edilirse, başka iş yoksa açılış iptal edilip GPU kapatılır."""
    stops = []

    def fake_start(pack=None):
        runtime.set_phase("starting", "GPU ayrılıyor")

    async def fake_stop(reason):
        stops.append(reason)
        runtime.set_phase("off", reason)

    monkeypatch.setattr(runtime, "start", fake_start)
    monkeypatch.setattr(runtime, "stop", fake_stop)
    task = asyncio.create_task(studio.worker())
    try:
        job = studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 1}, [])
        for _ in range(100):
            if studio.get_job(job["id"])["status"] == "waiting_gpu":
                break
            await asyncio.sleep(0.05)
        await studio.cancel_job(job["id"])
        await asyncio.sleep(2.5)  # worker 2 sn'de bir yoklar
        assert stops == ["iş iptal edildi, GPU kapatılıyor"]
        assert studio.get_job(job["id"])["error"] == studio.CANCELLED

        # Kuyrukta başka iş varsa açılış sürer: GPU o iş için lazım.
        stops.clear()
        runtime.set_phase("off")
        first = studio.create_job("t", "image", "image", "a", {**IMAGE, "count": 1}, [])
        for _ in range(100):
            if studio.get_job(first["id"])["status"] == "waiting_gpu":
                break
            await asyncio.sleep(0.05)
        studio.create_job("t", "image", "image", "b", {**IMAGE, "count": 1}, [])
        await studio.cancel_job(first["id"])
        await asyncio.sleep(2.5)
        assert stops == []
    finally:
        task.cancel()
        runtime.set_phase("off")


async def test_cancel_running_job_interrupts_comfy(comfy):
    comfy.slow = True
    job = studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 1}, [])
    for _ in range(100):
        if studio.get_job(job["id"])["status"] == "running" and comfy.prompts:
            break
        await asyncio.sleep(0.05)
    await studio.cancel_job(job["id"])
    done = await wait_done(job["id"])
    assert comfy.interrupted.is_set()
    assert done["status"] == "error" and done["error"] == studio.CANCELLED
    # Sonraki iş normal çalışır.
    comfy.slow = False
    nxt = await wait_done(studio.create_job("t", "image", "image", "y", {**IMAGE, "count": 1}, [])["id"])
    assert nxt["status"] == "done"


async def test_cancel_while_models_download_ends_the_job_without_running_it(comfy, monkeypatch):
    """Paket inerken iptal: iş paketin bitmesini beklemez, ComfyUI'ye hiç gitmez."""
    runtime.local_modes.discard("video")

    async def pack_never_done(*cmd, **kw):
        return ""

    monkeypatch.setattr(runtime, "run", pack_never_done)
    job = studio.create_job("t", "video", "video", "x", {"quality": "480p", "ratio": "16:9", "width": 864, "height": 480,
                                                          "seconds": 3, "turbo": True, "seed": 1}, [])
    for _ in range(100):
        if "modelleri" in (studio.get_job(job["id"])["stage"] or ""):
            break
        await asyncio.sleep(0.05)
    await studio.cancel_job(job["id"])
    done = await wait_done(job["id"], timeout=5)
    assert done["error"] == studio.CANCELLED and not comfy.prompts


async def test_cancel_between_graphs_skips_the_remaining_images(comfy):
    job = studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 3}, [])
    for _ in range(100):
        if comfy.prompts:
            break
        await asyncio.sleep(0.01)
    await studio.cancel_job(job["id"])  # sahte ComfyUI kesmeyi yok sayar: ilk grafik biter
    done = await wait_done(job["id"])
    assert done["error"] == studio.CANCELLED and len(comfy.prompts) == 1


async def test_cancel_queued_job_is_skipped(comfy):
    comfy.slow = True
    first = studio.create_job("t", "image", "image", "a", {**IMAGE, "count": 1}, [])
    second = studio.create_job("t", "image", "image", "b", {**IMAGE, "count": 1}, [])
    cancelled = await studio.cancel_job(second["id"])
    assert cancelled["status"] == "error" and cancelled["error"] == studio.CANCELLED
    for _ in range(100):
        if comfy.prompts:
            break
        await asyncio.sleep(0.05)
    await studio.cancel_job(first["id"])
    await wait_done(first["id"])
    await asyncio.sleep(0.3)
    assert [g["encode"]["inputs"]["prompt"] for _, g in comfy.prompts] == ["a"]
    assert studio.queue_info()["order"] == []


async def test_dropped_websocket_fails_with_clear_error(comfy):
    """ComfyUI bağlantısı iş bitmeden koparsa KeyError değil, açık bir hata gösterilir."""
    comfy.drop_ws = True
    done = await wait_done(studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 1}, [])["id"])
    assert done["status"] == "error" and done["error"] == "ComfyUI bağlantısı koptu"


async def test_output_of_job_whose_thread_was_deleted_while_running_is_removed(comfy):
    """Çalışan işin sohbeti silinirse biten işin çıktısı media/'da sahipsiz kalmaz."""
    thread = studio.create_thread("image", "x")
    job = studio.create_job(thread["id"], "image", "image", "x", {**IMAGE, "count": 1}, [])
    for _ in range(100):
        if comfy.prompts:
            break
        await asyncio.sleep(0.01)
    studio.delete_thread(thread["id"])
    await asyncio.sleep(0.6)
    assert studio.get_job(job["id"]) is None
    assert list(studio.MEDIA_DIR.glob(f"{job['id']}_*")) == []


async def test_sweep_orphans_removes_only_unreferenced_old_files(db, monkeypatch):
    kept_out, kept_in, orphan, fresh = "a_0.png", "b_0.png", "c_0.png", "d_0.png"
    for name in (kept_out, kept_in, orphan, fresh):
        (studio.MEDIA_DIR / name).write_bytes(b"x")
    job = studio.create_job("t", "image", "image", "x", {}, [kept_in])
    studio._update(job["id"], outputs=json.dumps([kept_out]))
    monkeypatch.setattr(studio, "ORPHAN_GRACE_SECONDS", 3600)
    import os, time
    old = time.time() - 7200
    for name in (kept_out, kept_in, orphan):
        os.utime(studio.MEDIA_DIR / name, (old, old))
    assert studio.sweep_orphans() == [orphan]
    assert sorted(p.name for p in studio.MEDIA_DIR.iterdir() if p.is_file()) == sorted([kept_out, kept_in, fresh])


async def test_timeout_interrupts_comfy_and_explains(comfy, monkeypatch):
    comfy.slow = True
    monkeypatch.setattr(studio, "JOB_TIMEOUT", 0.5)
    done = await wait_done(studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 1}, [])["id"])
    assert done["status"] == "error" and "Süre aşımı" in done["error"]
    assert comfy.interrupted.is_set()


async def test_shutdown_leaves_queued_jobs_queued(db):
    """Servis kapanırken iş işleyici durur; kuyruktaki iş açılışta yeniden çalışsın diye 'queued' kalır."""
    import app as app_module

    runtime.set_phase("off")
    started = []
    runtime_start = runtime.start
    runtime.start = lambda pack=None: started.append(1)
    try:
        app = {"tasks": [asyncio.create_task(studio.worker())]}
        job = studio.create_job("t", "image", "image", "x", {**IMAGE, "count": 1}, [])
        await asyncio.sleep(0.1)
        await app_module.on_shutdown(app)
        assert studio.get_job(job["id"])["status"] == "waiting_gpu"
        studio.db.close()
        studio.init()
        assert studio.get_job(job["id"])["status"] == "queued"
    finally:
        runtime.start = runtime_start


async def test_shutdown_stops_open_gpu(db, monkeypatch):
    """Servis dururken GPU açıksa kapatılır: izleyen kalmayınca kredi harcanmaya devam ederdi."""
    import app as app_module

    stops = []

    async def fake_stop_runtime():
        stops.append(runtime.state["phase"])

    monkeypatch.setattr(runtime, "stop_runtime", fake_stop_runtime)
    runtime.set_phase("ready", "GPU hazır")
    await app_module.on_shutdown({"tasks": []})
    assert stops == ["stopping"] and runtime.state["phase"] == "off"

    stops.clear()
    runtime.set_phase("off")
    await app_module.on_shutdown({"tasks": []})
    assert stops == []


async def test_second_job_knows_its_remaining_time_and_queue_wait(comfy):
    """İlk iş düğüm sürelerini öğretir; aynı ayarlı sonraki işin kalan süresi bilinir ve
    arkasındaki işin ne zaman başlayacağı tahmin edilir."""
    first = studio.create_job("t", "image", "image", "a", {**IMAGE, "count": 1, "seed": 1}, [])
    assert (await wait_done(first["id"]))["status"] == "done"
    assert "image" in runtime.warm
    learned = studio.timings()
    prefix = f"image|{tracker.variant(first)}"
    assert f"{prefix}|cold|sampler" in learned and f"{prefix}|total" in learned
    comfy.slow = True
    second = studio.create_job("t", "image", "image", "b", {**IMAGE, "count": 1, "seed": 2}, [])
    third = studio.create_job("t", "image", "image", "c", {**IMAGE, "count": 1, "seed": 3}, [])
    for _ in range(100):
        job = studio.get_job(second["id"])
        if job["status"] == "running" and job["live"] and job["live"]["stage_seconds"] is not None:
            break
        await asyncio.sleep(0.02)
    assert job["live"]["eta"] is not None and job["live"]["loading"] is True
    assert "başlıyor" in job["stage"]  # sıcak işte model yüklemesi beklenmez
    info = studio.queue_info()
    assert info["starts_in"][third["id"]] is not None
    await studio.cancel_job(second["id"])
    await studio.cancel_job(third["id"])


async def test_waiting_for_models_shows_download_progress(comfy, monkeypatch):
    runtime.local_modes.discard("video")
    runtime.packs.update({"image": {"total": 10, "done": 10}, "video": {"total": 4 * 2**30, "done": 2**30}})
    runtime._pack_samples.extend([(0.0, 0), (10.0, 100 * 2**20)])
    calls = []

    async def fake_ensure(pack, on_wait=None):
        on_wait(runtime.pack_status(pack))
        calls.append(studio.get_job(job["id"]))
        runtime.local_modes.add(pack)

    monkeypatch.setattr(runtime, "ensure_models", fake_ensure)
    job = studio.create_job("t", "video", "video", "x", {"quality": "480p", "ratio": "16:9", "width": 864, "height": 480,
                                                          "seconds": 3, "turbo": True, "seed": 1}, [])
    assert (await wait_done(job["id"]))["status"] == "done"
    waiting = calls[0]
    assert waiting["stage"] == "Video modelleri iniyor · 1.0/4.0 GB · 10 MB/s"
    assert waiting["progress"] == 0.25 and waiting["live"]["eta"] == round(3 * 2**30 / (10 * 2**20))


async def test_warmup_preloads_image_and_yields_to_user_job(comfy, monkeypatch):
    runtime.warm.clear()
    monkeypatch.setattr(runtime, "_has_pending", studio.has_pending_jobs)
    await runtime.warmup()
    assert "image" in runtime.warm
    assert comfy.prompts[-1][1][workflows.SAVE_NODE]["class_type"] == "PreviewImage"

    # Isıtma sürerken kullanıcı iş gönderirse ısıtma kesilir ve kullanıcının işi çalışır.
    runtime.warm.clear()
    comfy.slow = True
    w_task = asyncio.create_task(runtime.warmup())
    for _ in range(50):
        if runtime.warming:
            break
        await asyncio.sleep(0.02)
    assert runtime.warming
    comfy.slow = False
    job = studio.create_job("t", "image", "image", "kullanıcı işi", {**IMAGE, "count": 1}, [])
    done = await wait_done(job["id"])
    await w_task
    assert done["status"] == "done"
    assert comfy.interrupted.is_set()
    assert not runtime.warming


async def test_worker_survives_unexpected_error_and_takes_next_job(db, monkeypatch):
    """İşin satırını okuyamamak gibi beklenmedik bir hata kuyruğu durdurmamalı; sonraki iş çalışır."""
    first = studio.create_job("t", "image", "image", "a", IMAGE, [])
    first_id = first["id"]
    second_id = None
    handled = []

    async def fake_work(jid):
        handled.append(jid)
        if len(handled) == 1:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(studio, "_work", fake_work)
    task = asyncio.create_task(studio.worker())
    second = studio.create_job("t", "image", "image", "b", IMAGE, [])
    second_id = second["id"]
    for _ in range(200):
        if len(handled) == 2:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    assert handled == [first_id, second_id]
    assert studio.get_job(first_id)["status"] == "error"
    assert "database is locked" in studio.get_job(first_id)["error"]


async def test_warmup_gives_up_when_image_pack_failed(comfy, monkeypatch):
    """Görsel paketi inemediyse ön ısıtma sonsuz ssh yoklamasına dönmemeli."""
    runtime.warm.clear()
    runtime.local_modes.discard("image")
    monkeypatch.setattr(runtime, "WARMUP_POLL_SECONDS", 0.01)
    monkeypatch.setattr(runtime, "_has_pending", lambda: False)

    async def failed(pack):
        return "failed"

    monkeypatch.setattr(runtime, "pack_state", failed)
    await asyncio.wait_for(runtime.warmup(), 5)
    assert "image" not in runtime.warm


async def test_warmup_gives_up_after_deadline(comfy, monkeypatch):
    """Paket ne iner ne hata verirse belirli bir süre sonra bırakılır; sonsuz yoklama olmaz."""
    runtime.warm.clear()
    runtime.local_modes.discard("image")
    monkeypatch.setattr(runtime, "WARMUP_POLL_SECONDS", 0.01)
    monkeypatch.setattr(runtime, "WARMUP_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(runtime, "_has_pending", lambda: False)

    async def pending(pack):
        return "pending"

    monkeypatch.setattr(runtime, "pack_state", pending)
    await asyncio.wait_for(runtime.warmup(), 5)
    assert "image" not in runtime.warm
