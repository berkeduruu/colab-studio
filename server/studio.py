"""Stüdyo: sohbetler, işler ve medya.

İşler oturum başına sırayla çalışır (birden çok Colab oturumu açıksa farklı oturumların işleri
birbirini beklemez). GPU kapalıysa ilk iş runtime'ı açar ve hazır olmasını bekler. Sonuçlar
sunucuya indirilir (GPU kapanınca runtime'daki kopyalar silinir). Kullanılmayan sohbetler (varsayılan 30 gün, STUDIO_RETENTION_DAYS) silinir.
"""
import asyncio
import itertools
import json
import random
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, FormData, WSMsgType

import config
import router
import runtime
import sessions
import tracker
import workflows

DATA_DIR = config.DATA_DIR
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "studio.db"
RETENTION_DAYS = config.RETENTION_DAYS  # 0: hiçbir şey kendiliğinden silinmez
CLEANUP_SECONDS = 6 * 3600
JOB_TIMEOUT = 1800
CLIENT_ID = "colab-studio"


def _session(session=None):
    """İşin çalıştığı oturum: verilmezse varsayılan (slot 0)."""
    return runtime.DEFAULT if session is None else session


def model_packs(job):
    """İşin gerektirdiği model paketleri; ilki GPU açılışını tetikleyen paket olur.

    Paketsiz tür (yazıya dökme) hiçbirini beklemez: modelini eklentisi ilk kullanımda indirir."""
    return [job["kind"]] if job["kind"] in runtime.PACKS else []


def session_for(job):
    """İşin oturumu: kayıtta yazılıysa o (kullanıcı seçimi ya da önceki yönlendirme), yoksa
    yönlendirici seçer (paket hazır → en az yüklü → ısınmış → küçük slot). İşin oturumu kapatıldıysa
    None döner."""
    manager = sessions.manager()
    name = job.get("session") if job else None
    if not name:
        name = router.choose(manager, model_packs(job) if job else (), job["kind"] if job else None,
                             load=pending_count)
    rt = manager.get(name)
    if rt is None and name != runtime.SESSION:
        # İşin oturumu kapatıldı: iş varsayılan oturuma kaydırılmaz (kullanıcının açmadığı GPU'yu açardı).
        return None
    return rt or runtime.DEFAULT


SESSION_CLOSED = "Oturum kapatıldı"


def _session_gone(rt):
    """Ek oturum kapatılıp yöneticiden düştüyse True: o oturumun nesnesiyle yeniden açılış yapılmamalı
    (kayıtta, listede ve boşta kapatmada görünmeyen bir runtime kredi yakmaya devam ederdi)."""
    return rt is not runtime.DEFAULT and sessions.manager().get(rt.name) is not rt


# Varsayılan oturumda modül fonksiyonları çağrılır: testler bunları yamalıyor ve tek oturumlu
# davranış birebir korunmalı. Yeni oturumlarda aynı işi oturum nesnesi yapar.
def _start_for(rt, pack):
    return runtime.start(pack) if rt is runtime.DEFAULT else rt.start(pack)


async def _stop_for(rt, reason):
    if rt is runtime.DEFAULT:
        return await runtime.stop(reason)
    return await rt.stop(reason)


def _touch_for(rt):
    if rt is runtime.DEFAULT:
        return runtime.touch()
    return rt.touch()


async def _ensure_models_for(rt, pack, on_wait):
    if rt is runtime.DEFAULT:
        return await runtime.ensure_models(pack, on_wait)
    return await rt.ensure_models(pack, on_wait)


async def _in(rt, fn, *args):
    """Oturum parametreli yardımcıyı çağırır: varsayılan oturumda **eski imza** kullanılır
    (testler bu fonksiyonları oturum argümanı almadan yamalıyor)."""
    return await (fn(*args) if rt is runtime.DEFAULT else fn(*args, rt))


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

# İş durum makinesi: bir iş yalnızca bu geçişlerle ilerler; done ve error son durumdur, hiçbir yazım
# onları geri açamaz (iptal edilen bir iş "running"e dönmez). İptal ayrı bir durum değildir:
# status="error", error=CANCELLED; istemciler bunu iş kaydındaki "cancelled" alanından okur.
JOB_TRANSITIONS = {
    "queued": {"waiting_gpu", "running", "error"},
    "waiting_gpu": {"running", "error"},  # servis yeniden başlarken init() bunları topluca "queued" yapar
    "running": {"done", "error"},
    "done": set(),
    "error": set(),
}
PENDING = ("queued", "waiting_gpu", "running")
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
    job_cols = {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}
    if "stage" not in job_cols:
        db.execute("ALTER TABLE jobs ADD COLUMN stage TEXT")
    # Çoklu oturum: işi hangi Colab oturumunun çalıştırdığı ve paralel üretimde grup kimliği.
    if "session" not in job_cols:
        db.execute("ALTER TABLE jobs ADD COLUMN session TEXT")
    if "group_id" not in job_cols:
        db.execute("ALTER TABLE jobs ADD COLUMN group_id TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS jobs_session ON jobs(session, status)")
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
    d["cancelled"] = d["status"] == "error" and d["error"] == CANCELLED
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


def list_media(limit=30, ext=None):
    """Medya kitaplığı, en yeni önce: kökteki üretim çıktıları ve uploads/ altındaki yüklemeler.
    MCP gibi istemciler stüdyonun diskine değil buraya bakar (stüdyo Docker'da ya da başka makinede
    olabilir). Veri dizininin disk kullanımı da döner."""
    ext = (ext or "").lower().lstrip(".")
    files = []
    for folder, prefix, is_output in ((MEDIA_DIR, "", True), (MEDIA_DIR / "uploads", "uploads/", False)):
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if not f.is_file() or (ext and f.suffix.lower().lstrip(".") != ext):
                continue
            try:
                st = f.stat()
            except FileNotFoundError:  # bu arada silindi
                continue
            files.append({"name": prefix + f.name, "size": st.st_size, "mtime": st.st_mtime, "is_output": is_output})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    disk = shutil.disk_usage(DATA_DIR)
    return {"files": files[:limit], "disk": {"total": disk.total, "used": disk.used, "free": disk.free}}


# --- işler ---

def create_job(thread_id, mode, kind, prompt, params, inputs, session=None, group_id=None):
    for name in inputs:
        path = (MEDIA_DIR / name).resolve()
        if not path.is_relative_to(MEDIA_DIR.resolve()) or not path.is_file():
            raise ValueError(f"yüklenen dosya bulunamadı: {name}")
    jid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO jobs (id, thread_id, mode, kind, prompt, params, inputs, status, created_at, session, group_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (jid, thread_id, mode, kind, prompt, json.dumps(params), json.dumps(inputs), "queued", time.time(),
         session, group_id),
    )
    db.commit()
    job = get_job(jid)
    _enqueue(jid, kind, job["created_at"])
    return job


def get_job(jid):
    row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    return _job_dict(row) if row else None


def _update(jid, **fields):
    """Durum dışındaki alanları yazar (aşama, ilerleme, çıktılar); durum yalnızca _set_status ile değişir."""
    assert "status" not in fields, "iş durumu _set_status ile değişir"
    keys = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE jobs SET {keys} WHERE id=?", (*fields.values(), jid))
    db.commit()


def _set_status(jid, status, **fields):
    """İşi JOB_TRANSITIONS'a göre `status`'a geçirir. Geçiş izinsizse (iş bu arada iptal edildi, bitti ya
    da silindi) hiçbir şey yazılmaz ve False döner: son durumdaki bir iş yarışla yeniden açılamaz."""
    sources = [s for s, targets in JOB_TRANSITIONS.items() if status in targets]
    keys = ", ".join(f"{k}=?" for k in ("status", *fields))
    marks = ",".join("?" * len(sources))
    cur = db.execute(f"UPDATE jobs SET {keys} WHERE id=? AND status IN ({marks})", (status, *fields.values(), jid, *sources))
    db.commit()
    return cur.rowcount == 1


def pending_count(session):
    """Oturuma atanmış bekleyen/çalışan iş sayısı: yönlendirmede yük göstergesi.

    Oturumu henüz yazılmamış işler (yönlendirilmekte olan iş dahil) hiçbir oturumun yükü sayılmaz:
    varsayılan oturuma sayılsalardı varsayılan oturum dolu görünür, işler ek oturumlara yığılırdı.
    Dağıtıcı oturumu iş kapıya girmeden yazar."""
    name = session if isinstance(session, str) else session.name
    row = db.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE status IN ('queued','waiting_gpu','running') AND session=?",
        (name,),
    ).fetchone()
    return row["c"]


def has_pending_jobs(session=None):
    """Bekleyen/çalışan iş var mı? Oturum verilirse yalnız o oturumun işleri sayılır (boşta kapatma
    ve "açılışı iptal et" kararı oturum başına verilir). Oturum verilmeyen işler varsayılan oturuma
    aittir: tek oturumlu kurulumlarda davranış değişmez."""
    if session is None:
        row = db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','waiting_gpu','running') LIMIT 1").fetchone()
        return row is not None
    name = session if isinstance(session, str) else session.name
    row = db.execute(
        "SELECT 1 FROM jobs WHERE status IN ('queued','waiting_gpu','running') "
        "AND (session=? OR (session IS NULL AND ?=?)) LIMIT 1",
        (name, name, runtime.SESSION),
    ).fetchone()
    return row is not None


def cancel_pending(session=None):
    """GPU elle kapatılınca bekleyen tüm işler iptal olur: yoksa sıradaki iş GPU'yu birkaç saniye
    sonra yeniden açar ve kapatma boşa gider. Çalışan iş ComfyUI'yle birlikte kesilir; bağlantı
    hatası yerine "İptal edildi" yazılsın diye işaretlenir. İptal edilen işlerin kimliklerini döndürür.

    `session` verilirse yalnız o oturumun işleri iptal edilir: bir oturumu kapatmak başka oturumun
    sırasını boşaltmaz."""
    if session is None:
        rows = db.execute("SELECT id, status FROM jobs WHERE status IN ('queued','waiting_gpu','running')").fetchall()
    else:
        name = session if isinstance(session, str) else session.name
        rows = db.execute(
            "SELECT id, status FROM jobs WHERE status IN ('queued','waiting_gpu','running') "
            "AND (session=? OR (session IS NULL AND ?=?))",
            (name, name, runtime.SESSION),
        ).fetchall()
    ids = []
    for r in rows:
        cancelled.add(r["id"])
        if r["status"] != "running":
            _set_status(r["id"], "error", error=CANCELLED, finished_at=time.time())
        ids.append(r["id"])
    return ids


MODEL_LABELS = {"image": "Görsel", "enhance": "İstem geliştirme", "video": "Video", "music": "Müzik", "speech": "Ses", "transcribe": "Yazıya dökme", "model3d": "3D"}


async def interrupt_comfy(session=None):
    rt = _session(session)
    async with ClientSession(timeout=ClientTimeout(total=10)) as http:
        async with http.post(f"{rt.comfy_url}/interrupt") as r:
            r.raise_for_status()


async def cancel_job(jid):
    """Sıradaki iş hemen düşer; çalışan işte ComfyUI kesilir, worker işi iptal olarak kapatır."""
    job = get_job(jid)
    if job["status"] in ("queued", "waiting_gpu"):
        cancelled.add(jid)
        _set_status(jid, "error", error=CANCELLED, finished_at=time.time())
    elif job["status"] == "running":
        cancelled.add(jid)
        # İş hangi oturumda çalışıyorsa kesme ona gider: başka oturumun grafiği durdurulmaz.
        rt = session_for(job)
        if rt is not None:  # oturumu kapatıldıysa kesilecek ComfyUI kalmadı
            await interrupt_comfy(rt)
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


_tasks = set()  # süren iş görevleri (referans tutulmazsa çöp toplayıcı iptal edebilir)
_session_gates = {}  # (oturum adı, olay döngüsü) -> Semaphore(1)


def _gate(rt):
    """Oturum başına tek iş: bir ComfyUI tek grafiği verimli çalıştırır.

    Kilit döngü başına kurulur: testler her testte yeni bir olay döngüsü açıyor ve asyncio
    nesneleri ilk kullanıldıkları döngüye bağlanıyor."""
    loop = asyncio.get_running_loop()
    key = (rt.name, id(loop))
    gate = _session_gates.get(key)
    if gate is None:
        gate = _session_gates[key] = asyncio.Semaphore(1)
    return gate


async def worker():
    """Kuyruk dağıtıcısı: işi alır, oturumunu belirler ve **kendi oturumunda** çalıştırır.

    Tek bir işin ya da veritabanının beklenmedik hatası döngüyü durdurmaz: döngü durursa yeni işler
    hiç alınmaz, GPU açılışı da kuyruktaki işi bulamaz (servis yeniden başlayana kadar stüdyo ölü
    kalır). İşler görev olarak açıldığı için farklı oturumlar birbirini beklemez; aynı oturumun
    işleri kapısında (semaphore) sıraya girer."""
    while True:
        _, _, jid = await queue.get()
        task = asyncio.create_task(_dispatch(jid))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _dispatch(jid):
    """İşi oturumunun kapısından geçirip çalıştırır; hata durumunda işi kapatır."""
    rt = None
    try:
        job = get_job(jid)
        if job is None or job["status"] != "queued":  # iptal edilmiş ya da silinmiş
            return
        rt = session_for(job)
        if rt is None:
            _set_status(jid, "error", error=SESSION_CLOSED, finished_at=time.time())
            return
        if job.get("session") != rt.name:
            # Yönlendirme sonucu kapıya girmeden yazılır: kapıda bekleyen iş de oturumunun yükü
            # sayılsın (yazılmazsa varsayılan oturumun yükü sayılıyor, sonraki işler hep aynı ek
            # oturuma yığılıyordu).
            _update(jid, session=rt.name)
        async with _gate(rt):
            # Varsayılan oturumda eski imza (tek argüman) korunur: testler `_work`'ü yamalıyor.
            await (_work(jid) if rt is runtime.DEFAULT else _work(jid, rt))
    except Exception as e:  # işin kendi hataları _work içinde yakalanır; buraya kalan kök neden
        runtime.log(f"iş {jid[:8]}: beklenmedik hata: {runtime.short_error(e, 300)}")
        try:
            _set_status(jid, "error", error=runtime.short_error(e), finished_at=time.time())
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
        if rt is not None:
            _touch_for(rt)  # iş bitti: boşta sayacı sıfırlanır (GPU hemen kapanmasın)


async def _work(jid, session=None):
    """İşi verilen oturumda çalıştırır. Oturum verilmezse işin kayıtlı oturumu (yönlendirme
    sonucu), o da yoksa varsayılan oturum kullanılır."""
    job = get_job(jid)
    if job is None or job["status"] != "queued":  # iptal edilmiş ya da silinmiş
        cancelled.discard(jid)
        return
    rt = _session(session) if session is not None else session_for(job)
    if rt is None or _session_gone(rt):
        _set_status(jid, "error", error=SESSION_CLOSED, finished_at=time.time())
        return
    if job.get("session") != rt.name:  # iş hangi oturumda çalıştığını taşır
        _update(jid, session=rt.name)
    packs = model_packs(job)
    try:
        if not rt.ready.is_set():
            _set_status(jid, "waiting_gpu")
            # Önceki bir kapanış sürüyorsa bitmesi beklenir, sonra GPU bir kez açılır.
            started = False
            while not rt.ready.is_set():
                if jid in cancelled:
                    # Açılışı bu iş tetiklediyse ve o oturumda başka iş yoksa GPU boşuna açılıp 15 dk
                    # boşta beklemesin (başka oturumun işi bu oturumu açık tutmaz).
                    if rt.state["phase"] == "starting" and not has_pending_jobs(rt):
                        await _stop_for(rt, "iş iptal edildi, GPU kapatılıyor")
                    raise RuntimeError(CANCELLED)
                if _session_gone(rt):  # beklerken oturum kapatıldı: yeniden açılmaz
                    raise RuntimeError(SESSION_CLOSED)
                if rt.state["phase"] in ("off", "error"):
                    if started:
                        raise RuntimeError(f"GPU açılamadı: {rt.state['detail']}")
                    _start_for(rt, packs[0] if packs else None)  # işin modelleri ilk iner; paketsiz tür (yazıya dökme) hiçbirini beklemez
                    started = True
                progress = rt.startup_progress()
                if progress:  # GPU açılışı sürüyor: iş satırı ~2,5 dk boş ve %0 kalmasın
                    _update(jid, stage=f"GPU hazırlanıyor · {progress['steps'][progress['step']]}",
                            progress=progress["progress"])
                    live[jid] = lambda p=progress: {
                        "progress": p["progress"], "eta": p["remaining_seconds"],
                        "stage_seconds": p["step_elapsed_seconds"], "stage_expected": None, "loading": False,
                    }
                await asyncio.sleep(2)
        # GPU hazır olduğu anda gelen iptal bekleme döngüsünde görülmeyebilir: geçiş reddedilirse iş
        # zaten son durumdadır (iptal edildi ya da silindi), çalıştırılmaz.
        if not _set_status(jid, "running"):
            return
        _touch_for(rt)
        for pack in packs:
            if pack in rt.local_modes:
                continue

            def on_wait(st, pack=pack):
                if jid in cancelled:  # paket arka planda inmeye devam eder; yalnızca iş bırakılır
                    raise RuntimeError(CANCELLED)
                _waiting_models(jid, pack, st)

            await _ensure_models_for(rt, pack, on_wait)
        if rt.warming:
            await (interrupt_comfy() if rt is runtime.DEFAULT else interrupt_comfy(rt))
            while rt.warming:
                await asyncio.sleep(0.1)
        try:
            await asyncio.wait_for(_run(job) if rt is runtime.DEFAULT else _run(job, rt), JOB_TIMEOUT)
        except TimeoutError:
            # ComfyUI işi kendisi sürdürür; kesilmezse sonraki işler GPU'yu boş bulamaz.
            await (interrupt_comfy() if rt is runtime.DEFAULT else interrupt_comfy(rt))
            raise RuntimeError(f"Süre aşımı: iş {JOB_TIMEOUT // 60} dakikada bitmedi, durduruldu") from None
        if jid in cancelled:  # iptal, ComfyUI'ye iş gitmeden (model hazırlığı, yükleme) geldiyse
            raise RuntimeError(CANCELLED)
        _set_status(jid, "done", progress=1.0, finished_at=time.time())
    except Exception as e:
        error = CANCELLED if jid in cancelled else runtime.short_error(e)
        _set_status(jid, "error", error=error, finished_at=time.time())


async def _run(job, session=None):
    rt = _session(session)
    async with ClientSession(timeout=ClientTimeout(total=600)) as http:
        images = [await _in(rt, _upload, http, name) for name in job["inputs"]]
        p = job["params"]
        if job["kind"] == "transcribe":
            outputs = await _in(rt, _execute, http, job, [workflows.transcribe(images[0], p["language"])])
            _update(job["id"], text=outputs[0][workflows.TEXT_NODE]["text"][0].strip())
            return
        if job["kind"] == "enhance":
            wf = workflows.enhance_prompt(job["prompt"], images, random.randrange(2**32))
            outputs = await _in(rt, _execute, http, job, [wf])
            _update(job["id"], text=outputs[0][workflows.TEXT_NODE]["text"][0].strip())
            return
        seed = p["seed"] if p["seed"] is not None else random.randrange(2**32)
        if job["kind"] == "video":
            outputs = await _in(rt, _execute, http, job,
                                [workflows.video_generate({**p, "prompt": job["prompt"], "seed": seed}, images)])
            saved = [await _in(rt, _download, http, job["id"], f, i)
                     for i, f in enumerate(outputs[0][workflows.SAVE_NODE]["images"])]
            _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))
            return
        if job["kind"] == "speech":
            wf = workflows.speech_generate({**p, "text": job["prompt"], "seed": seed}, images[0] if images else None)
            outputs = await _in(rt, _execute, http, job, [wf])
            saved = [await _in(rt, _download, http, job["id"], f, i)
                     for i, f in enumerate(outputs[0][workflows.SAVE_NODE]["audio"])]
            _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))
            return
        if job["kind"] == "model3d":
            outputs = await _in(rt, _execute, http, job, [workflows.model3d_generate({**p, "seed": seed}, images[0])])
            saved = [await _in(rt, _download, http, job["id"], f, i)
                     for i, f in enumerate(outputs[0][workflows.SAVE_NODE]["3d"])]
            _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))
            return
        if job["kind"] == "music":
            wf = workflows.music_generate({**p, "style": job["prompt"], "seed": seed})
            outputs = await _in(rt, _execute, http, job, [wf])
            saved = [await _in(rt, _download, http, job["id"], f, i)
                     for i, f in enumerate(outputs[0][workflows.SAVE_NODE]["audio"])]
            _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))
            return
        if job["kind"] != "image":
            raise ValueError(f"bilinmeyen iş türü: {job['kind']}")
        graphs = []
        for i in range(p["count"]):
            gp = {**p, "prompt": job["prompt"], "seed": seed + i}
            graphs.append(workflows.image_edit(gp, images) if images else workflows.image_generate(gp))
        outputs = await _in(rt, _execute, http, job, graphs)
        saved = []
        for out in outputs:
            for img in out[workflows.SAVE_NODE]["images"]:
                saved.append(await _in(rt, _download, http, job["id"], img, len(saved)))
        _update(job["id"], outputs=json.dumps(saved), params=json.dumps({**p, "seed": seed}))


async def _upload(http, name, session=None):
    form = FormData()
    form.add_field("image", (MEDIA_DIR / name).read_bytes(), filename=Path(name).name)
    form.add_field("overwrite", "true")
    async with http.post(f"{_session(session).comfy_url}/upload/image", data=form) as r:
        r.raise_for_status()
        return (await r.json())["name"]


async def _download(http, jid, img, index, session=None):
    params = {"filename": img["filename"], "subfolder": img["subfolder"], "type": img["type"]}
    async with http.get(f"{_session(session).comfy_url}/view", params=params) as r:
        r.raise_for_status()
        data = await r.read()
    name = f"{jid}_{index}{Path(img['filename']).suffix}"
    (MEDIA_DIR / name).write_bytes(data)
    return name


async def _execute(http, job, graphs, session=None):
    """Grafikleri sırayla çalıştırır; ilerlemeyi ComfyUI websocket'inden okur. Kalan süre önceki
    işlerden öğrenilen düğüm sürelerinden hesaplanır (tracker.py); iş başarıyla biterse bu işin
    ölçümleri de öğrenilir."""
    rt = _session(session)
    jid, results = job["id"], []
    cold = job["kind"] not in rt.warm
    t = tracker.Tracker(timings(), job, graphs, cold)
    live[jid] = t.snapshot
    loaded = False  # ilk adım bitince model GPU'dadır; sonraki örnekleyiciler yükleme beklemez
    async with http.ws_connect(f"{rt.comfy_url}/ws?clientId={CLIENT_ID}") as ws:
        for gi, graph in enumerate(graphs):
            if jid in cancelled:  # iptal iki grafik arasında geldiyse kesilecek bir iş yoktu
                raise RuntimeError(CANCELLED)
            async with http.post(f"{rt.comfy_url}/prompt", json={"prompt": graph, "client_id": CLIENT_ID}) as r:
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
            async with http.get(f"{rt.comfy_url}/history/{pid}") as r:
                results.append((await r.json())[pid]["outputs"])
    t.finish()
    learn_timings(t.learned())
    rt.warm.add(job["kind"])
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
    # RETENTION_DAYS=0: sohbetler, işler ve yüklemeler hiç silinmez; kredi örnekleri ve sahipsiz medya yine temizlenir.
    cutoff = time.time() - RETENTION_DAYS * 86400 if RETENTION_DAYS else None
    # Süre sohbetin son kullanımından sayılır (son işinin açılışı ya da bitişi), açılışından değil:
    # 31 gün önce açılıp dün kullanılan sohbet silinmemeli. Bekleyen işi olan sohbete dokunulmaz.
    if cutoff is not None:
        stale = db.execute(
            "SELECT t.id FROM threads t WHERE MAX(t.created_at, COALESCE((SELECT MAX(MAX(j.created_at, "
            "COALESCE(j.finished_at, 0))) FROM jobs j WHERE j.thread_id = t.id), 0)) < ? AND NOT EXISTS "
            f"(SELECT 1 FROM jobs j WHERE j.thread_id = t.id AND j.status IN ({','.join('?' * len(PENDING))}))",
            (cutoff, *PENDING),
        ).fetchall()
        for t in stale:
            delete_thread(t["id"])
        db.execute("DELETE FROM jobs WHERE thread_id IS NULL AND created_at < ?", (cutoff,))
    db.execute("DELETE FROM credit_samples WHERE ts < ?", (time.time() - 90 * 86400,))
    db.commit()
    prune_timings()
    sweep_orphans()
    # Bir işin girdisi olan yükleme (kalan sohbette görünen referans görseli gibi) süresi dolsa da kalır.
    used = set()
    for row in db.execute("SELECT inputs FROM jobs"):
        used.update(json.loads(row["inputs"]))
    for f in (MEDIA_DIR / "uploads").iterdir():
        if cutoff is not None and f.stat().st_mtime < cutoff and f"uploads/{f.name}" not in used:
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
