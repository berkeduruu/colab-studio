"""Stüdyo web servisi: arayüzü sunar, GPU runtime'ını ve üretim işlerini API ile yönetir."""
import asyncio
import math
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

import config
import runtime
import studio
import workflows

WEB_DIST = Path(__file__).parent / "web" / "dist"
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
AUDIO_TYPES = {
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/flac": ".flac",
    "audio/x-flac": ".flac", "audio/ogg": ".ogg", "audio/webm": ".webm", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
}
UPLOAD_TYPES = {**IMAGE_TYPES, **AUDIO_TYPES}
SPEECH_LANGUAGES = {
    "chatterbox": ["Turkish", "English", "German", "French", "Spanish", "Italian", "Russian", "Arabic", "Portuguese",
                   "Dutch", "Polish", "Swedish", "Danish", "Norwegian", "Finnish", "Greek", "Hebrew", "Hindi",
                   "Japanese", "Korean", "Chinese", "Malay", "Swahili"],
    "qwen3": ["Auto", "English", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese",
              "Spanish", "Italian"],
}
# ComfyUI-Whisper'ın dil adları; arayüzdeki liste (Chips.tsx) bununla aynı.
TRANSCRIBE_LANGUAGES = ["auto", "Turkish", "English", "German", "French", "Spanish", "Italian", "Russian", "Arabic", "Japanese"]
QWEN3_PRESETS = ["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"]

# Düzenlemede referans görseller ve çıktı bu kısa kenar/piksel bütçesine küçültülür (oran korunur):
# değer, ComfyUI'deki "resolution" (yaklaşık piksel sayısının karekökü, 32'nin katı).
EDIT_RESOLUTIONS = {"720p": 960, "1080p": 1440, "1440p": 1920, "2160p": 2880}
# Toplam piksel bütçesi; en-boy oranı bunun içinde 32'nin katlarına yuvarlanır.
SIZES = {"1K": 1024 * 1024, "1.5K": 1024 * 1536, "2K": 2048 * 2048}
SAMPLERS = ["euler", "euler_ancestral", "res_multistep", "dpmpp_2m", "dpmpp_2m_sde", "er_sde", "uni_pc"]
SCHEDULERS = ["simple", "beta", "normal", "karras", "sgm_uniform"]
RATIOS = {"1:1": (1, 1), "2:3": (2, 3), "3:2": (3, 2), "3:4": (3, 4), "4:3": (4, 3), "9:16": (9, 16), "16:9": (16, 9)}


MODES = {"image", "video", "model3d", "music", "speech", "transcribe"}


def bad(msg):
    raise web.HTTPBadRequest(text=msg)


async def read_json(request):
    try:
        return await request.json()
    except ValueError:
        bad("geçersiz JSON")


def params_of(body):
    p = body.get("params") or {}
    if not isinstance(p, dict):
        bad("params bir nesne olmalı")
    return p


def _number(cast, p, key, default, lo, hi, label):
    try:
        v = cast(p.get(key, default))
    except (TypeError, ValueError):
        bad(f"{label} sayı olmalı")
    if not lo <= v <= hi:
        bad(f"{label} {lo}-{hi} arası olmalı")
    return v


def _seed(p):
    seed = p.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32):
        bad("seed 0 ile 2^32 arası bir tam sayı olmalı")
    return seed


def query_mode(request):
    """`?mode=` verilmezse None döner: sohbetler ve ön ayarlar tüm modlar için listelenir (tek
    çağrıda hepsini görmek isteyen istemciler için; arayüz her zaman modu veriyor)."""
    mode = request.query.get("mode")
    if mode is not None and mode not in MODES:
        bad(f"geçersiz mod: {mode}")
    return mode


def image_params(body):
    p = params_of(body)
    ratio, size = p.get("ratio", "2:3"), p.get("size", "1.5K")
    if ratio not in RATIOS:
        bad(f"geçersiz oran: {ratio}")
    if size not in SIZES:
        bad(f"geçersiz boyut: {size}")
    a, b = RATIOS[ratio]
    width = round(math.sqrt(SIZES[size] * a / b) / 32) * 32
    height = round(math.sqrt(SIZES[size] * b / a) / 32) * 32
    count = _number(int, p, "count", 1, 1, 4, "adet")
    steps = _number(int, p, "steps", 25, 1, 60, "adım")
    cfg = _number(float, p, "cfg", 3.0, 1.0, 10.0, "CFG")
    seed = _seed(p)
    edit_resolution = p.get("edit_resolution", "1080p")
    if edit_resolution not in EDIT_RESOLUTIONS:
        bad(f"geçersiz düzenleme çözünürlüğü: {edit_resolution}")
    sampler, scheduler = p.get("sampler", "euler"), p.get("scheduler", "simple")
    if sampler not in SAMPLERS:
        bad(f"geçersiz sampler: {sampler}")
    if scheduler not in SCHEDULERS:
        bad(f"geçersiz scheduler: {scheduler}")
    return {
        "sampler": sampler, "scheduler": scheduler, "edit_resolution": edit_resolution,
        "edit_pixels": EDIT_RESOLUTIONS[edit_resolution],
        "ratio": ratio, "size": size, "width": width, "height": height, "count": count,
        "steps": steps, "cfg": cfg, "turbo": bool(p.get("turbo", False)),
        "seed": seed, "negative": str(p.get("negative", "")),
    }


# H3'ün doğal tuvali 768 px kısa kenar, en çok 768x1344; 480p L4'te ~2.5 kat hızlı.
VIDEO_SIZES = {
    "480p": {"16:9": (864, 480), "9:16": (480, 864), "1:1": (640, 640)},
    "768p": {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (992, 992)},
}


def video_params(body):
    p = params_of(body)
    quality, ratio = p.get("quality", "480p"), p.get("ratio", "16:9")
    if quality not in VIDEO_SIZES:
        bad(f"geçersiz kalite: {quality}")
    if ratio not in VIDEO_SIZES[quality]:
        bad(f"geçersiz oran: {ratio}")
    seconds = _number(float, p, "seconds", 5, 2, 10, "süre (sn)")
    seed = _seed(p)
    width, height = VIDEO_SIZES[quality][ratio]
    turbo = bool(p.get("turbo", True))
    # L4'te ölçüldü (HANDOFF-performans.md): 768p · 20 adım örnekleme 8 sn ~23 dk, 9 sn ~29 dk, 10 sn ~33 dk;
    # diğer düğümlerle 9 sn de iş sınırını (studio.JOB_TIMEOUT, 30 dk) aşar.
    if quality == "768p" and seconds > 8 and not turbo:
        bad("768p Kaliteli video 8 sn'den uzunsa 30 dk sınırını aşar (9 sn ~31 dk, 10 sn ~35 dk); Turbo'yu seç ya da süreyi 8 sn'ye indir")
    return {
        "quality": quality, "ratio": ratio, "width": width, "height": height,
        "seconds": seconds, "turbo": turbo, "seed": seed,
    }


# TRELLIS.2 şekil ızgarası; L4'te ölçülen süreler arayüzde (Chips.tsx) yazıyor.
MODEL3D_RESOLUTIONS = [1024, 1536, 2048]
MODEL3D_TEXTURES = ["pbr", "vertex"]


def model3d_params(body):
    p = params_of(body)
    resolution = p.get("resolution", 1024)
    if resolution not in MODEL3D_RESOLUTIONS:
        bad(f"geçersiz 3D çözünürlüğü: {resolution}")
    texture = p.get("texture", "pbr")
    if texture not in MODEL3D_TEXTURES:
        bad(f"geçersiz doku türü: {texture}")
    faces = _number(int, p, "faces", 200000, 20000, 1000000, "üçgen sayısı")
    return {"resolution": resolution, "texture": texture, "faces": faces, "seed": _seed(p)}


def music_params(body):
    p = params_of(body)
    seconds = _number(float, p, "seconds", 120, 10, 360, "süre (sn)")
    seed = _seed(p)
    return {"lyrics": str(p.get("lyrics", "")).strip(), "seconds": seconds, "seed": seed}


def speech_params(body):
    p = params_of(body)
    engine = p.get("engine", "chatterbox")
    if engine not in SPEECH_LANGUAGES:
        bad(f"bilinmeyen motor: {engine}")
    language = p.get("language", SPEECH_LANGUAGES[engine][0])
    if language not in SPEECH_LANGUAGES[engine]:
        bad(f"{engine} bu dili desteklemiyor: {language}")
    preset = p.get("voice_preset", "Vivian")
    if preset not in QWEN3_PRESETS:
        bad(f"bilinmeyen hazır ses: {preset}")
    seed = _seed(p)
    return {
        "engine": engine, "language": language, "voice_preset": preset,
        "instruct": str(p.get("instruct", "")).strip(), "reference_text": str(p.get("reference_text", "")).strip(),
        "exaggeration": _number(float, p, "exaggeration", 0.5, 0.25, 2.0, "duygu yoğunluğu"),
        "cfg_weight": _number(float, p, "cfg_weight", 0.5, 0.0, 1.0, "tempo/CFG"),
        "temperature": _number(float, p, "temperature", 0.8, 0.05, 2.0, "sıcaklık"),
        "seed": seed,
    }


def transcribe_params(body):
    language = params_of(body).get("language", "auto")
    if language not in TRANSCRIBE_LANGUAGES:
        bad(f"geçersiz dil: {language}")
    return {"language": language}


@web.middleware
async def csrf_guard(request, handler):
    """Başka bir sitenin tarayıcıda bu servise yazma isteği (POST/PUT/DELETE) göndermesini engeller:
    nginx'in basic auth bilgisini tarayıcı kendiliğinden eklediği için gerekli. Tarayıcı olmayan
    istemciler (curl, testler) bu başlıkları göndermez ve geçer."""
    if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/"):
        site = request.headers.get("Sec-Fetch-Site")
        origin = request.headers.get("Origin")
        if (site is not None and site not in ("same-origin", "none")) or (
            origin is not None and urlsplit(origin).netloc != request.host
        ):
            raise web.HTTPForbidden(text="başka bir siteden gelen istek reddedildi")
    return await handler(request)


# --- runtime ---

def state_response():
    return web.json_response({**runtime.snapshot(), "credit_stats": studio.credit_stats(), "queue": studio.queue_info()})


async def runtime_state(request):
    return state_response()


async def runtime_start(request):
    runtime.start()
    return state_response()


_stop_task = None


def _stop_done(task):
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        runtime.log(f"kapatma başarısız: {runtime.short_error(error, 300)}")


async def runtime_stop(request):
    """Kapanış 3 dakikaya kadar sürebilir (ssh çıkışı + `colab stop` + artık runtime'lar); isteği
    bekletmek nginx'in 120 sn zaman aşımına takılıp arayüzde hata gösteriyordu. Kapanış arka planda
    yürür, istek anında döner, arayüz durumu yoklayarak izler."""
    global _stop_task
    if runtime.state["phase"] not in ("stopping", "off"):
        reason = "elle kapatıldı"
        runtime.begin_stop(reason)  # faz hemen "stopping": yanıt güncel durumu göstersin
        _stop_task = asyncio.create_task(runtime.run_stop(reason))
        _stop_task.add_done_callback(_stop_done)
    return state_response()


async def runtime_toggle_auto(request):
    runtime.toggle_auto()
    return state_response()


async def runtime_metrics(request):
    return web.json_response(list(runtime.metrics))


async def runtime_log(request):
    return web.json_response({"lines": list(runtime.logs)})


# --- sohbetler ---

async def threads_list(request):
    return web.json_response(studio.list_threads(query_mode(request)))


async def thread_get(request):
    thread = studio.get_thread(request.match_info["id"])
    if thread is None:
        raise web.HTTPNotFound()
    return web.json_response(thread)


async def thread_delete(request):
    studio.delete_thread(request.match_info["id"])
    return web.json_response({"ok": True})


# --- yükleme ---

async def upload(request):
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file":
        bad("'file' alanı gerekli")
    ext = UPLOAD_TYPES.get(part.headers.get("Content-Type", ""))
    if ext is None:
        bad("yalnızca görsel (PNG, JPEG, WebP) ya da ses (MP3, WAV, FLAC, OGG, WebM, M4A) yüklenebilir")
    name = studio.save_upload(await part.read(), ext)
    return web.json_response({"name": name, "url": f"/media/{name}"})


# --- işler ---

async def job_create(request):
    body = await read_json(request)
    if not isinstance(body, dict):
        bad("istek gövdesi bir nesne olmalı")
    kind, prompt = body.get("kind"), str(body.get("prompt", "")).strip()
    inputs = body.get("inputs") or []
    if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
        bad("inputs dosya adlarından oluşan bir liste olmalı")
    if not prompt and kind not in ("transcribe", "model3d"):
        bad("istem boş olamaz")
    if len(inputs) > 10:
        bad("en fazla 10 referans görsel")
    if kind == "image":
        mode, params = "image", image_params(body)
        # 2160p düzenlemede CFG > 1 VRAM'e sığmaz (batch 2), adım ~78 sn olur: ~33 dk, 30 dk sınırını aşar.
        if inputs and params["edit_resolution"] == "2160p" and params["cfg"] > 1:
            bad("2160p düzenleme CFG > 1 ile ~33 dk sürer, 30 dk sınırını aşar; Turbo'yu (CFG 1) seç ya da çözünürlüğü düşür")
    elif kind == "video":
        mode, params = "video", video_params(body)
        if len(inputs) > 2:
            bad("video için en fazla 2 görsel (ilk ve son kare)")
    elif kind == "model3d":
        mode, params = "model3d", model3d_params(body)
        if len(inputs) != 1 or Path(inputs[0]).suffix not in IMAGE_TYPES.values():
            bad("3D model için 1 görsel ekle")
        prompt = prompt or "3D model"
    elif kind == "music":
        mode, params = "music", music_params(body)
        if inputs:
            bad("müzik için görsel eklenemez")
    elif kind == "speech":
        mode, params = "speech", speech_params(body)
        if len(inputs) > 1 or any(Path(i).suffix not in AUDIO_TYPES.values() for i in inputs):
            bad("seslendirme için en fazla 1 ses dosyası (klonlanacak ses) eklenebilir")
    elif kind == "transcribe":
        mode, params = "transcribe", transcribe_params(body)
        if len(inputs) != 1 or Path(inputs[0]).suffix not in AUDIO_TYPES.values():
            bad("yazıya dökmek için 1 ses dosyası ekle")
        prompt = prompt or "Ses kaydı"
    elif kind == "enhance":
        mode, params = "image", {}
    else:
        bad(f"bilinmeyen iş türü: {kind}")
    thread_id = None if kind == "enhance" else body.get("thread_id")
    if thread_id is not None and studio.get_thread(thread_id) is None:
        raise web.HTTPNotFound(text="sohbet bulunamadı")
    if kind != "enhance" and thread_id is None:
        thread_id = studio.create_thread(mode, prompt)["id"]
    try:
        job = studio.create_job(thread_id, mode, kind, prompt, params, inputs)
    except ValueError as e:
        bad(str(e))
    return web.json_response(job)


async def job_get(request):
    job = studio.get_job(request.match_info["id"])
    if job is None:
        raise web.HTTPNotFound()
    return web.json_response(job)


async def job_cancel(request):
    if studio.get_job(request.match_info["id"]) is None:
        raise web.HTTPNotFound()
    return web.json_response(await studio.cancel_job(request.match_info["id"]))


# --- ön ayarlar ---

async def presets_list(request):
    return web.json_response(studio.list_presets(query_mode(request)))


async def preset_create(request):
    body = await read_json(request)
    if not isinstance(body, dict):
        bad("istek gövdesi bir nesne olmalı")
    name = str(body.get("name", "")).strip()
    if not name:
        bad("ön ayar adı boş olamaz")
    if body.get("mode") not in MODES:
        bad(f"geçersiz mod: {body.get('mode')}")
    return web.json_response({"id": studio.save_preset(body["mode"], name, params_of(body))})


async def preset_delete(request):
    studio.delete_preset(request.match_info["id"])
    return web.json_response({"ok": True})


# --- ayarlar (tarayıcıdan bağımsız, her cihazda aynı) ---

SETTING_KEYS = {"mode", "image.params", "video.params", "music.params", "speech.params", "transcribe.params", "model3d.params"}


async def models_get(request):
    """Model kataloğundaki (models.toml) ad ve açıklamalar; arayüz ayarlarda gösterir."""
    return web.json_response({m: {"label": e["label"], "description": e["description"]} for m, e in workflows.CATALOG.items()})


async def settings_get(request):
    return web.json_response(studio.get_settings())


async def setting_put(request):
    key = request.match_info["key"]
    if key not in SETTING_KEYS:
        bad(f"bilinmeyen ayar: {key}")
    studio.save_setting(key, await read_json(request))
    return web.json_response({"ok": True})


# --- arayüz ---

async def spa(request):
    return web.FileResponse(WEB_DIST / "index.html")


async def on_startup(app):
    studio.init()
    problem = runtime.preflight()
    if problem:
        # Kurulum eksik (colab CLI yok ya da giriş yapılmamış): arayüz ne yapılacağını söylesin.
        runtime.set_phase("error", problem)
    else:
        # Servis yeniden başladıysa sahipsiz kalan runtime kredi harcamasın. Bırakılamazsa servis yine
        # açılır; watchdog stüdyonun açtığı runtime'ı yeniden dener.
        try:
            await runtime.stop_runtime()
        except RuntimeError as e:
            runtime.set_phase("error", runtime.short_error(e))
    app["tasks"] = [
        asyncio.create_task(runtime.watchdog(studio.has_pending_jobs, studio.record_credits)),
        asyncio.create_task(runtime.metrics_loop()),
        asyncio.create_task(studio.worker()),
        asyncio.create_task(studio.cleanup_loop()),
    ]


async def on_shutdown(app):
    # İş işleyici kapanış sırasında yeni iş almasın: sıradakiler veritabanında "queued" kalır ve
    # servis açılınca yeniden kuyruğa girer.
    for task in app["tasks"]:
        task.cancel()
    await asyncio.gather(*app["tasks"], return_exceptions=True)
    # Servis dururken GPU açık kalırsa boşta kapatmayı izleyen kimse olmaz ve kredi harcanmaya devam eder.
    # Kapatılamazsa sonraki açılışta on_startup yeniden dener.
    if runtime.state["phase"] != "off":
        await runtime.stop("servis durdu")


def create_app():
    studio.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    app = web.Application(client_max_size=50 * 1024 * 1024, middlewares=[csrf_guard])
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.add_routes([
        web.get("/api/runtime", runtime_state),
        web.post("/api/runtime/start", runtime_start),
        web.post("/api/runtime/stop", runtime_stop),
        web.post("/api/runtime/toggle-auto", runtime_toggle_auto),
        web.get("/api/runtime/metrics", runtime_metrics),
        web.get("/api/runtime/log", runtime_log),
        web.get("/api/threads", threads_list),
        web.get("/api/threads/{id}", thread_get),
        web.delete("/api/threads/{id}", thread_delete),
        web.post("/api/uploads", upload),
        web.post("/api/jobs", job_create),
        web.get("/api/jobs/{id}", job_get),
        web.post("/api/jobs/{id}/cancel", job_cancel),
        web.get("/api/models", models_get),
        web.get("/api/settings", settings_get),
        web.put("/api/settings/{key}", setting_put),
        web.get("/api/presets", presets_list),
        web.post("/api/presets", preset_create),
        web.delete("/api/presets/{id}", preset_delete),
        web.static("/media", studio.MEDIA_DIR),
        web.static("/assets", WEB_DIST / "assets"),
        web.get("/", spa),
        web.get("/{tail:(?!api/|media/|assets/).*}", spa),
    ])
    return app


if __name__ == "__main__":
    if config.HOST not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("STUDIO_IN_DOCKER"):
        print(
            f"WARNING: listening on {config.HOST}:{config.PORT} without a password. Anyone who can reach this "
            "address can start the GPU and spend your Colab compute units. Use a firewall, an SSH tunnel, "
            "or nginx with basic auth (deploy/setup-nginx.sh).",
            file=sys.stderr, flush=True,
        )
    version = (config.REPO_DIR / "VERSION").read_text().strip()
    if os.environ.get("STUDIO_IN_DOCKER"):
        # İçerideki port sabit (8190); kullanıcı docker-compose ile başka bir host portu yayınlamış olabilir.
        print(f"Colab Studio {version}: listening on port {config.PORT} inside the container; "
              "open the address published by docker compose (STUDIO_PORT in .env, default 8190)", flush=True)
    else:
        print(f"Colab Studio {version}: "
              f"http://{'127.0.0.1' if config.HOST in ('0.0.0.0', '::') else config.HOST}:{config.PORT}/", flush=True)
    web.run_app(create_app(), host=config.HOST, port=config.PORT, print=None)
