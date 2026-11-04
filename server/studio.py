"""Stüdyo: sohbetler, işler ve medya.

İşler sırayla çalışır. GPU kapalıysa ilk iş runtime'ı açar ve hazır olmasını bekler. Sonuçlar
sunucuya indirilir (GPU kapanınca runtime'daki kopyalar silinir). 30 günden eski sohbetler silinir.
"""
import asyncio
import itertools
import json
import random
import sqlite3
import time
import uuid
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, FormData, WSMsgType

import config
import runtime
import tracker
import workflows

DATA_DIR = config.DATA_DIR
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "studio.db"
RETENTION_DAYS = 30
CLEANUP_SECONDS = 6 * 3600
JOB_TIMEOUT = 1800
CLIENT_ID = "colab-studio"

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY, mode TEXT NOT NULL, title TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, thread_id TEXT, mode TEXT NOT NULL, kind TEXT NOT NULL,
  prompt TEXT NOT NULL, params TEXT NOT NULL, inputs TEXT NOT NULL,
  status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, error TEXT,
  outputs TEXT NOT NULL DEFAULT '[]', text TEXT,
  created_at REAL NOT NULL, finished_at REAL
);
CREATE TABLE IF NOT EXISTS presets (
  id TEXT PRIMARY KEY, mode TEXT NOT NULL, name TEXT NOT NULL, params TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credit_samples (ts REAL PRIMARY KEY, balance REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS timings (key TEXT PRIMARY KEY, seconds REAL NOT NULL);
"""
CREDIT_SAMPLE_SECONDS = 300

# İşin o an çalışan düğümüne göre kullanıcıya gösterilen aşama
STAGES = {
    "UNETLoader": "Modeller yükleniyor",
    "CLIPLoader": "Modeller yükleniyor",
    "VAELoader": "Modeller yükleniyor",
    "QwenImage21Cache": "Modeller yükleniyor",
    "LoadImage": "Referanslar okunuyor",
    "TextEncodeQwenImage21": "İstem işleniyor",
    "TextGenerate": "İstem geliştiriliyor",
    "KSampler": "Üretiliyor",
    "VAEDecode": "Kaydediliyor",
    "SaveImage": "Kaydediliyor",
    "LoraLoaderModelOnly": "Modeller yükleniyor",
    "MiniMaxH3ImageToVideo": "İstem işleniyor",
    "SamplerCustomAdvanced": "Üretiliyor",
    "VAEDecodeAudio": "Ses çözülüyor",
    "CreateVideo": "Kaydediliyor",
    "SaveVideo": "Kaydediliyor",
    "CheckpointLoaderSimple": "Modeller yükleniyor",
    "YuE2GenerateMusic": "Melodi ve vokal planlanıyor",
    "SaveAudioMP3": "Kaydediliyor",
    "ChatterBoxOfficial23LangEngineNode": "Ses modeli hazırlanıyor",
    "Qwen3TTSEngineNode": "Ses modeli hazırlanıyor",
    "LoadAudio": "Ses dosyası okunuyor",
    "CharacterVoicesNode": "Referans ses işleniyor",
    "UnifiedTTSTextNode": "Seslendiriliyor",
    "Apply Whisper": "Yazıya dökülüyor",
    "LoadBackgroundRemovalModel": "Modeller yükleniyor",
    "CLIPVisionLoader": "Modeller yükleniyor",
    "RemoveBackground": "Arka plan ayrılıyor",
    "Trellis2Conditioning": "Görsel işleniyor",
    "VaeDecodeStructureTrellis2": "Kaba yapı çözülüyor",
    "VaeDecodeShapeTrellis": "Şekil çözülüyor",
    "VaeDecodeTextureTrellis": "Doku çözülüyor",
    "RemeshMesh": "Ağ temizleniyor",
    "DecimateMesh": "Ağ sadeleştiriliyor",
    "PaintMesh": "Renkler boyanıyor",
    "UnwrapMesh": "UV açılıyor",
    "BakeTextureFromVoxel": "Doku pişiriliyor",
    "BakeNormalMapFromMesh": "Normal haritası pişiriliyor",
    "BakeAmbientOcclusion": "Gölgelendirme pişiriliyor",
    "SaveGLB": "Kaydediliyor",
}

db = None
# İstem geliştirme kısa ve kullanıcı sonucunu bekliyor: sıradaki işlerin önüne geçer.
queue = asyncio.PriorityQueue()
CANCELLED = "İptal edildi"
cancelled = set()  # iptal istenen çalışan işler; ComfyUI kesilince hata yerine bu yazılır
# Çalışan işin canlı durumu (kalan süre, aşamanın süresi, son güncelleme); yalnızca bellekte.
live = {}


def init():
    global db
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (MEDIA_DIR / "uploads").mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    # Silinen satırların metni (istem, parametreler, yazıya döküm) sayfa içinde kalmasın:
    # DELETE sırasında içerik sıfırlanır. Bu ayardan önce silinmiş kayıtların artıkları açılışta
    # bir kez VACUUM ile atılır (aşağıda); silme yolunda VACUUM gerekmez, tüm dosyayı yeniden yazardı.
    db.execute("PRAGMA secure_delete=ON")
    db.executescript(SCHEMA)
    if "stage" not in {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}:
        db.execute("ALTER TABLE jobs ADD COLUMN stage TEXT")
    # Servis yeniden başladıysa çalışan iş yarıda kalmıştır; sıradakiler yeniden kuyruğa girer.
    db.execute("UPDATE jobs SET status='error', error='Servis yeniden başladı' WHERE status='running'")
    db.execute("UPDATE jobs SET status='queued' WHERE status='waiting_gpu'")
    db.commit()
    # secure_delete açılmadan önce silinmiş kayıtların artıkları yalnız VACUUM ile gider; bir kez
    # yeter, silme yolunda tekrarlanmaz. Kilitliyse bir sonraki açılışta yeniden denenir.
    try:
        db.execute("VACUUM")
    except sqlite3.OperationalError:
        pass
    prune_timings()
    for r in db.execute("SELECT id, kind, created_at FROM jobs WHERE status='queued'"):
        _enqueue(r["id"], r["kind"], r["created_at"])


def _enqueue(jid, kind, created_at):
    queue.put_nowait((0 if kind == "enhance" else 1, created_at, jid))


def _job_dict(row):
    d = dict(row)
    for k in ("params", "inputs", "outputs"):
        d[k] = json.loads(d[k])
    state = live.get(d["id"]) if d["status"] in ("running", "waiting_gpu") else None
    d["live"] = state() if state else None
    if d["live"]:
        d["progress"] = d["live"]["progress"]
    return d


# --- öğrenilmiş süreler (ilerleme ve kalan süre tahmini için) ---

# Okuma yolu (her durum yoklamasında queue_info + her işte tracker) tabloyu yeniden taramasın.
_timings = None


def timings():
    """Öğrenilmiş süreler; değişince (learn/prune) önbellek atılır."""
    global _timings
    if _timings is None:
        _timings = {r["key"]: r["seconds"] for r in db.execute("SELECT key, seconds FROM timings")}
    return _timings


def learn_timings(measured):
    """Yeni ölçüm eskisiyle yarı yarıya karışır: tek bir sapma tahmini bozmaz, değişim çabuk yansır."""
    global _timings
    known = timings()
    for key, sec in measured.items():
        value = sec if key not in known else 0.5 * known[key] + 0.5 * sec
        db.execute("INSERT OR REPLACE INTO timings VALUES (?,?)", (key, round(value, 2)))
    db.commit()
    _timings = None


def prune_timings():
    """Bilinen variant kalıplarına uymayan (eskimiş) süre anahtarlarını siler; yoksa tabloda birikir."""
    global _timings
    stale = [k for k in timings() if not tracker.is_known_timing_key(k)]
    db.executemany("DELETE FROM timings WHERE key=?", [(k,) for k in stale])
    db.commit()
    _timings = None
    return stale


# --- sohbetler ---

def list_threads(mode=None):
    """mode verilmezse tüm modların sohbetleri (MCP gibi istemciler tek çağrıda hepsini görür)."""
    if mode is None:
        rows = db.execute("SELECT * FROM threads ORDER BY created_at DESC").fetchall()
    else:
        rows = db.execute("SELECT * FROM threads WHERE mode=? ORDER BY created_at DESC", (mode,)).fetchall()
    return [dict(r) for r in rows]


def create_thread(mode, title):
    tid = uuid.uuid4().hex
    db.execute("INSERT INTO threads VALUES (?,?,?,?)", (tid, mode, title[:80], time.time()))
    db.commit()
    return dict(db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone())


def get_thread(tid):
    thread = db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
    if thread is None:
        return None
    jobs = db.execute("SELECT * FROM jobs WHERE thread_id=? ORDER BY created_at", (tid,)).fetchall()
    return {**dict(thread), "jobs": [_job_dict(j) for j in jobs]}


def delete_thread(tid):
    outputs, inputs = set(), set()
    for job in db.execute("SELECT outputs, inputs FROM jobs WHERE thread_id=?", (tid,)).fetchall():
        outputs.update(json.loads(job["outputs"]))
        inputs.update(json.loads(job["inputs"]))
    db.execute("DELETE FROM jobs WHERE thread_id=?", (tid,))
    db.execute("DELETE FROM threads WHERE id=?", (tid,))
    db.commit()
    # Çıktılar ve referans dosyaları sohbetle birlikte gider; başka sohbetteki bir işin girdisi olan
    # dosya ("bu görselden 3D yap") kalır, o iş de silinince sweep_orphans temizler.
    still_used = set()
    for row in db.execute("SELECT inputs FROM jobs").fetchall():
        still_used.update(json.loads(row["inputs"]))
    for name in (outputs | inputs) - still_used:
        (MEDIA_DIR / name).unlink(missing_ok=True)


# --- ön ayarlar ---

def get_settings():
    return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM settings")}


def save_setting(key, value):
    db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
    db.commit()


def queue_info():
    """Bekleyen işlerin sırası, o an çalışan iş ve sıradakilerin tahmini başlama süresi (çalışan
    işin kalanı + öndeki işlerin öğrenilmiş süreleri); arayüz 'Sırada' satırında gösterir."""
    rows = [_job_dict(r) for r in db.execute(
        "SELECT * FROM jobs WHERE status IN ('queued','waiting_gpu','running') "
        "ORDER BY status = 'queued', kind != 'enhance', created_at"
    )]
    running = next((r for r in rows if r["status"] != "queued"), None)
    known = timings()
    wait = 0.0
    if running and running["status"] == "waiting_gpu":
        startup = runtime.startup_progress()
        total = known.get(f"{running['kind']}|{tracker.variant(running)}|total")
        wait = startup["remaining_seconds"] + total if startup and total is not None else None
    elif running:
        wait = running["live"]["eta"] if running["live"] else None
    starts_in = {}
    for r in rows:
        if r["status"] != "queued":
            continue
        starts_in[r["id"]] = round(wait) if wait is not None else None
        total = known.get(f"{r['kind']}|{tracker.variant(r)}|total")
        wait = wait + total if wait is not None and total is not None else None
    current = {k: running[k] for k in ("id", "kind", "status", "stage", "progress", "prompt")} if running else None
    return {"order": [r["id"] for r in rows], "current": current, "starts_in": starts_in}


def list_presets(mode=None):
    if mode is None:
        rows = db.execute("SELECT * FROM presets ORDER BY mode, name").fetchall()
    else:
        rows = db.execute("SELECT * FROM presets WHERE mode=? ORDER BY name", (mode,)).fetchall()
    return [{**dict(r), "params": json.loads(r["params"])} for r in rows]


def save_preset(mode, name, params):
    pid = uuid.uuid4().hex
    db.execute("INSERT INTO presets VALUES (?,?,?,?)", (pid, mode, name[:60], json.dumps(params)))
    db.commit()
    return pid


def delete_preset(pid):
    db.execute("DELETE FROM presets WHERE id=?", (pid,))
    db.commit()


# --- yüklemeler ---

def save_upload(data, ext):
    name = f"uploads/{uuid.uuid4().hex}{ext}"
    (MEDIA_DIR / name).write_bytes(data)
    return name


# --- işler ---

def create_job(thread_id, mode, kind, prompt, params, inputs):
    for name in inputs:
        path = (MEDIA_DIR / name).resolve()
        if not path.is_relative_to(MEDIA_DIR.resolve()) or not path.is_file():
            raise ValueError(f"yüklenen dosya bulunamadı: {name}")
    jid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO jobs (id, thread_id, mode, kind, prompt, params, inputs, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (jid, thread_id, mode, kind, prompt, json.dumps(params), json.dumps(inputs), "queued", time.time()),
    )
    db.commit()
    job = get_job(jid)
    _enqueue(jid, kind, job["created_at"])
    return job


def get_job(jid):
    row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    return _job_dict(row) if row else None


def _update(jid, **fields):
    keys = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE jobs SET {keys} WHERE id=?", (*fields.values(), jid))
    db.commit()


def has_pending_jobs():
    return db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','waiting_gpu','running') LIMIT 1").fetchone() is not None


MODEL_LABELS = {"image": "Görsel", "enhance": "İstem geliştirme", "video": "Video", "music": "Müzik", "speech": "Ses", "transcribe": "Yazıya dökme", "model3d": "3D"}


async def interrupt_comfy():
    async with ClientSession(timeout=ClientTimeout(total=10)) as http:
        async with http.post(f"{runtime.COMFY_URL}/interrupt") as r:
            r.raise_for_status()


async def cancel_job(jid):
    """Sıradaki iş hemen düşer; çalışan işte ComfyUI kesilir, worker işi iptal olarak kapatır."""
    job = get_job(jid)
    if job["status"] in ("queued", "waiting_gpu"):
        cancelled.add(jid)
        _update(jid, status="error", error=CANCELLED, finished_at=time.time())
    elif job["status"] == "running":
        cancelled.add(jid)
        await interrupt_comfy()
    return get_job(jid)


def _waiting_models(jid, kind, st):
    """İşin modelleri henüz inmemişse inme durumu işin satırında görünür."""
    label = MODEL_LABELS[kind]
    if st is None:  # indirme durumu henüz okunmadı
        _update(jid, stage=f"{label} modelleri hazırlanıyor")
        return
    gb = 1024 ** 3
    if st["fraction"] >= 1:  # xet son parçaları diske yazıp dosyayı yerine koyuyor
        text = f"{label} modelleri diske yazılıyor · {st['total'] / gb:.1f} GB"
    else:
        text = f"{label} modelleri iniyor · {st['done'] / gb:.1f}/{st['total'] / gb:.1f} GB"
        if st["rate"]:
            text += f" · {st['rate'] / 2**20:.0f} MB/s"
    _update(jid, stage=text, progress=st["fraction"])
    live[jid] = lambda: {"progress": st["fraction"], "eta": st["eta"], "stage_seconds": None, "stage_expected": None, "loading": False}


async def worker():
    """Kuyruk işleyicisi. Tek bir işin ya da veritabanının beklenmedik hatası döngüyü durdurmaz:
    döngü durursa yeni işler hiç alınmaz, GPU açılışı da kuyruktaki işi bulamaz (servis yeniden
    başlayana kadar stüdyo ölü kalır)."""
    while True:
        _, _, jid = await queue.get()
        try:
            await _work(jid)
        except Exception as e:  # işin kendi hataları _work içinde yakalanır; buraya kalan kök neden
            runtime.log(f"iş {jid[:8]}: beklenmedik hata: {runtime.short_error(e, 300)}")
            try:
                _update(jid, status="error", error=runtime.short_error(e), finished_at=time.time())
            except Exception as write_error:
                runtime.log(f"iş {jid[:8]}: hata durumu yazılamadı: {runtime.short_error(write_error, 200)}")
        finally:
            try:
                if get_job(jid) is None:  # sohbet iş sürerken silindi: indirilen çıktı sahipsiz kalmasın
                    for f in MEDIA_DIR.glob(f"{jid}_*"):
                        f.unlink()
            except Exception as e:
                runtime.log(f"iş {jid[:8]}: sahipsiz çıktı temizlenemedi: {runtime.short_error(e, 200)}")
            cancelled.discard(jid)
            live.pop(jid, None)
            runtime.touch()


async def _work(jid):
    job = get_job(jid)
    if job is None or job["status"] != "queued":  # iptal edilmiş ya da silinmiş
        cancelled.discard(jid)
        return
    try:
        if not runtime.ready.is_set():
            _update(jid, status="waiting_gpu")
            # Önceki bir kapanış sürüyorsa bitmesi beklenir, sonra GPU bir kez açılır.
            started = False
            while not runtime.ready.is_set():
                if jid in cancelled:
                    # Açılışı bu iş tetiklediyse ve başka iş yoksa GPU boşuna açılıp 15 dk boşta beklemesin.
                    if runtime.state["phase"] == "starting" and not has_pending_jobs():
                        await runtime.stop("iş iptal edildi, GPU kapatılıyor")
                    raise RuntimeError(CANCELLED)
                if runtime.state["phase"] in ("off", "error"):
                    if started:
                        raise RuntimeError(f"GPU açılamadı: {runtime.state['detail']}")
                    runtime.start(job["kind"] if job["kind"] in runtime.PACKS else None)  # işin modelleri ilk iner; paketsiz tür (yazıya dökme) hiçbirini beklemez
                    started = True
                await asyncio.sleep(2)
        _update(jid, status="running")
        runtime.touch()
        if job["kind"] in runtime.PACKS and job["kind"] not in runtime.local_modes:
            def on_wait(st):
                if jid in cancelled:  # paket arka planda inmeye devam eder; yalnızca iş bırakılır
                    raise RuntimeError(CANCELLED)
                _waiting_models(jid, job["kind"], st)
            await runtime.ensure_models(job["kind"], on_wait)
        if runtime.warming:
            await interrupt_comfy()
            while runtime.warming:
                await asyncio.sleep(0.1)
        try:
            await asyncio.wait_for(_run(job), JOB_TIMEOUT)
        except TimeoutError:
            # ComfyUI işi kendisi sürdürür; kesilmezse sonraki işler GPU'yu boş bulamaz.
            await interrupt_comfy()
            raise RuntimeError(f"Süre aşımı: iş {JOB_TIMEOUT // 60} dakikada bitmedi, durduruldu") from None
        if jid in cancelled:  # iptal, ComfyUI'ye iş gitmeden (model hazırlığı, yükleme) geldiyse
            raise RuntimeError(CANCELLED)
        _update(jid, status="done", progress=1.0, finished_at=time.time())
    except Exception as e:
        error = CANCELLED if jid in cancelled else runtime.short_error(e)
        _update(jid, status="error", error=error, finished_at=time.time())


async def _run(job):
    async with ClientSession(timeout=ClientTimeout(total=600)) as http:
        images = [await _upload(http, name) for name in job["inputs"]]
        p = job["params"]
        if job["kind"] == "transcribe":
            outputs = await _execute(http, job, [workflows.transcribe(images[0], p["language"])])
            _update(job["id"], text=outputs[0][workflows.TEXT_NODE]["text"][0].strip())
            return
        if job["kind"] == "enhance":
            wf = workflows.enhance_prompt(job["prompt"], images, random.randrange(2**32))
            outputs = await _execute(http, job, [wf])
            _update(job["id"], text=outputs[0][workflows.TEXT_NODE]["text"][0].strip())
            return
        seed = p["seed"] if p["seed"] is not None else random.randrange(2**32)
        if job["kind"] == "video":
            outputs = await _execute(http, job, [workflows.video_generate({**p, "prompt": job["prompt"], "seed": seed}, images)])
            saved = [await _download(http, job["id"], f, i) for i, f in enumerate(outputs[0][workflows.SAVE_NODE]["images"])]
            _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))
            return
        if job["kind"] == "speech":
            wf = workflows.speech_generate({**p, "text": job["prompt"], "seed": seed}, images[0] if images else None)
            outputs = await _execute(http, job, [wf])
            saved = [await _download(http, job["id"], f, i) for i, f in enumerate(outputs[0][workflows.SAVE_NODE]["audio"])]
            _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))
            return
        if job["kind"] == "model3d":
            outputs = await _execute(http, job, [workflows.model3d_generate({**p, "seed": seed}, images[0])])
            saved = [await _download(http, job["id"], f, i) for i, f in enumerate(outputs[0][workflows.SAVE_NODE]["3d"])]
            _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))
            return
        if job["kind"] == "music":
            wf = workflows.music_generate({**p, "style": job["prompt"], "seed": seed})
            outputs = await _execute(http, job, [wf])
            saved = [await _download(http, job["id"], f, i) for i, f in enumerate(outputs[0][workflows.SAVE_NODE]["audio"])]
            _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))
            return
        if job["kind"] != "image":
            raise ValueError(f"bilinmeyen iş türü: {job['kind']}")
        graphs = []
        for i in range(p["count"]):
            gp = {**p, "prompt": job["prompt"], "seed": seed + i}
            graphs.append(workflows.image_edit(gp, images) if images else workflows.image_generate(gp))
        outputs = await _execute(http, job, graphs)
        saved = []
        for out in outputs:
            for img in out[workflows.SAVE_NODE]["images"]:
                saved.append(await _download(http, job["id"], img, len(saved)))
        _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))


async def _upload(http, name):
    form = FormData()
    form.add_field("image", (MEDIA_DIR / name).read_bytes(), filename=Path(name).name)
    form.add_field("overwrite", "true")
    async with http.post(f"{runtime.COMFY_URL}/upload/image", data=form) as r:
        r.raise_for_status()
        return (await r.json())["name"]


async def _download(http, jid, img, index):
    params = {"filename": img["filename"], "subfolder": img["subfolder"], "type": img["type"]}
    async with http.get(f"{runtime.COMFY_URL}/view", params=params) as r:
        r.raise_for_status()
        data = await r.read()
    name = f"{jid}_{index}{Path(img['filename']).suffix}"
    (MEDIA_DIR / name).write_bytes(data)
    return name


async def _execute(http, job, graphs):
    """Grafikleri sırayla çalıştırır; ilerlemeyi ComfyUI websocket'inden okur. Kalan süre önceki
    işlerden öğrenilen düğüm sürelerinden hesaplanır (tracker.py); iş başarıyla biterse bu işin
    ölçümleri de öğrenilir."""
    jid, results = job["id"], []
    cold = job["kind"] not in runtime.warm
    t = tracker.Tracker(timings(), job, graphs, cold)
    live[jid] = t.snapshot
    loaded = False  # ilk adım bitince model GPU'dadır; sonraki örnekleyiciler yükleme beklemez
    async with http.ws_connect(f"{runtime.COMFY_URL}/ws?clientId={CLIENT_ID}") as ws:
        for gi, graph in enumerate(graphs):
            if jid in cancelled:  # iptal iki grafik arasında geldiyse kesilecek bir iş yoktu
                raise RuntimeError(CANCELLED)
            async with http.post(f"{runtime.COMFY_URL}/prompt", json={"prompt": graph, "client_id": CLIENT_ID}) as r:
                body = await r.json()
                if r.status != 200:
                    raise RuntimeError(f"ComfyUI işi reddetti: {json.dumps(body)[:400]}")
            pid = body["prompt_id"]
            t.start_graph(gi)
            stage = "Çalışıyor"
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                ev = json.loads(msg.data)
                data = ev.get("data", {})
                if data.get("prompt_id") != pid:
                    continue
                if ev["type"] == "execution_cached":
                    t.cached(data.get("nodes", []))
                elif ev["type"] == "executing" and data.get("node") in graph:
                    if jid in cancelled:
                        # Kesme, grafik gönderilip yürütme başlamadan geldiyse ComfyUI yürütme
                        # başında kesme bayrağını sıfırlar; düğüm başladığında yeniden kesilir.
                        await interrupt_comfy()
                    t.executing(data["node"])
                    node = graph[data["node"]]
                    stage = node.get("_meta", {}).get("title") or STAGES.get(node["class_type"], "Çalışıyor")
                    detail = stage
                    if node["class_type"] in tracker.SAMPLERS:  # ilk adıma kadar model yüklenir
                        detail += " (model GPU'ya yükleniyor)" if cold and not loaded else " (başlıyor)"
                    _update(jid, stage=detail, progress=t.snapshot()["progress"])
                elif ev["type"] == "progress":
                    v, mx = data["value"], data["max"]
                    t.progress(v, mx)
                    loaded = True
                    sampler = graph.get(t.node, {}).get("class_type") in tracker.SAMPLERS
                    detail = f"{stage} ({v}/{mx} adım)" if sampler else f"{stage} (%{round(100 * v / mx)})"
                    _update(jid, progress=t.snapshot()["progress"], stage=detail)
                elif ev["type"] == "execution_interrupted":
                    raise RuntimeError(CANCELLED)
                elif ev["type"] == "execution_error":
                    raise RuntimeError(f"{data.get('exception_type')}: {data.get('exception_message', '')[:300]}")
                elif ev["type"] == "executing" and data.get("node") is None:
                    break
                elif ev["type"] == "execution_success":
                    break
            else:
                # Bağlantı bitiş olayı gelmeden kapandı (ComfyUI çöktü ya da SSH yönlendirmesi koptu);
                # history'de bu işin kaydı olmaz.
                raise RuntimeError("ComfyUI bağlantısı koptu")
            async with http.get(f"{runtime.COMFY_URL}/history/{pid}") as r:
                results.append((await r.json())[pid]["outputs"])
    t.finish()
    learn_timings(t.learned())
    runtime.warm.add(job["kind"])
    return results


# --- kredi geçmişi ---

def record_credits(credits):
    last = db.execute("SELECT ts FROM credit_samples ORDER BY ts DESC LIMIT 1").fetchone()
    if last is None or time.time() - last["ts"] >= CREDIT_SAMPLE_SECONDS:
        db.execute("INSERT INTO credit_samples VALUES (?,?)", (time.time(), credits["balance"]))
        db.commit()


def _spent_since(since):
    """Bakiyedeki düşüşlerin toplamı; artışlar (kredi satın alma) sayılmaz."""
    rows = db.execute("SELECT balance FROM credit_samples WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    return sum(max(0.0, a["balance"] - b["balance"]) for a, b in itertools.pairwise(rows))


def credit_stats():
    now = time.time()
    first = db.execute("SELECT MIN(ts) AS ts FROM credit_samples").fetchone()["ts"]
    if first is None:
        return None
    midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    week_start = max(first, now - 7 * 86400)
    days = max((now - week_start) / 86400, 1 / 24)
    week = _spent_since(week_start)
    return {
        "today": _spent_since(midnight),
        "week": week,
        "per_day": week / days,
        "tracked_days": round(days, 1),
    }


ORPHAN_GRACE_SECONDS = 3600  # yeni inen dosya iş satırına yazılmadan önce sahipsiz görünebilir


def sweep_orphans():
    """Hiçbir işin çıktısı ya da girdisi olmayan medya dosyalarını siler (silinen sohbetin sürerken
    biten işinin çıktısı gibi); yüklemeler (uploads/) süre dolunca ayrıca temizlenir."""
    used = set()
    for row in db.execute("SELECT outputs, inputs FROM jobs"):
        used.update(json.loads(row["outputs"]))
        used.update(json.loads(row["inputs"]))
    removed = []
    for f in MEDIA_DIR.iterdir():
        if f.is_file() and f.name not in used and time.time() - f.stat().st_mtime > ORPHAN_GRACE_SECONDS:
            f.unlink()
            removed.append(f.name)
    return removed


def cleanup_once():
    """Saklama süresi dolan sohbetleri, eski iş satırlarını, kredi örneklerini, eskimiş süre
    anahtarlarını ve sahipsiz medyayı siler."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    for t in db.execute("SELECT id FROM threads WHERE created_at < ?", (cutoff,)).fetchall():
        delete_thread(t["id"])
    db.execute("DELETE FROM jobs WHERE thread_id IS NULL AND created_at < ?", (cutoff,))
    db.execute("DELETE FROM credit_samples WHERE ts < ?", (time.time() - 90 * 86400,))
    db.commit()
    prune_timings()
    sweep_orphans()
    for f in (MEDIA_DIR / "uploads").iterdir():
        if f.stat().st_mtime < cutoff:
            f.unlink()


async def cleanup_loop():
    """Temizliği 6 saatte bir çalıştırır. Bir turdaki hata döngüyü durdurmaz: durursa saklama
    süresi işlemez, medya ve veritabanı büyümeye devam eder."""
    while True:
        try:
            cleanup_once()
        except Exception as e:
            runtime.log(f"temizlik turu başarısız, sonraki turda yeniden denenecek: {runtime.short_error(e, 300)}")
        await asyncio.sleep(CLEANUP_SECONDS)
