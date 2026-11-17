"""Stüdyo web servisi: arayüzü sunar, GPU runtime'ını ve üretim işlerini API ile yönetir."""
import asyncio
import errno
import ipaddress
import json
import math
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

import config
import i18n
import router
import runtime
import sessions
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


def host_allowed(host):
    """`Host` başlığı bir IP, localhost ya da STUDIO_ALLOWED_HOSTS'taki bir ad mı? DNS rebinding'de
    tarayıcı saldırganın alan adını gönderir; IP ile ya da izin verilen adla gelen istek güvenlidir."""
    if "*" in config.ALLOWED_HOSTS:
        return True
    name = urlsplit(f"//{host}").hostname or ""
    name = name.rstrip(".")
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return name == "localhost" or name.endswith(".localhost") or name in config.ALLOWED_HOSTS


@web.middleware
async def host_guard(request, handler):
    """Bilinmeyen bir alan adıyla gelen her isteği (okuma dahil) reddeder: kötü niyetli bir sayfa kendi
    adını 127.0.0.1'e çevirse bile stüdyoya erişemez, sohbetleri okuyamaz, GPU'yu açamaz."""
    if not host_allowed(request.host):
        name = urlsplit(f"//{request.host}").hostname or request.host
        raise web.HTTPForbidden(text=(
            f"Colab Studio: the host name '{name}' is not allowed. If this is your own domain, add it to "
            f"STUDIO_ALLOWED_HOSTS in .env (e.g. STUDIO_ALLOWED_HOSTS={name}) and restart the studio."
        ))
    return await handler(request)


@web.middleware
async def csrf_guard(request, handler):
    """Başka bir sitenin tarayıcıda bu servise yazma isteği (POST/PUT/DELETE) göndermesini engeller:
    nginx'in basic auth bilgisini tarayıcı kendiliğinden eklediği için gerekli. Tarayıcı olmayan
    istemciler (curl, testler) bu başlıkları göndermez ve geçer."""
    if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/"):
        site = request.headers.get("Sec-Fetch-Site")
        origin = request.headers.get("Origin")
        # Kendi ters vekilin (reverse proxy) Host başlığını iç adresle değiştiriyorsa tarayıcının Origin'i ona
        # uymaz: vekilin eklediği X-Forwarded-Host da kabul edilir. Tarayıcı başka bir siteden bu başlığı
        # (CORS ön kontrolü olmadan) ekleyemez, bu yüzden CSRF korumasını zayıflatmaz.
        forwarded = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()
        origin_host = urlsplit(origin).netloc if origin is not None else None
        if (site is not None and site not in ("same-origin", "none")) or (
            origin_host is not None and origin_host != request.host and origin_host != forwarded
        ):
            raise web.HTTPForbidden(text=(
                "başka bir siteden gelen istek reddedildi"
                + (f" (Origin '{origin_host}', Host '{request.host}': kendi ters vekilin varsa Host başlığını "
                   "olduğu gibi ilet, örn. nginx'te 'proxy_set_header Host $host;')" if origin_host is not None
                   and origin_host != request.host else "")
            ))
    return await handler(request)


@web.middleware
async def english_for_other_clients(request, handler):
    """Arayüz dışındaki istemciler (MCP, curl) için yanıttaki Türkçe durum ve hata metinlerini İngilizceye
    çevirir (server/i18n.py). Arayüz kendi dil seçimine göre çevirdiği için ona dokunulmaz."""
    if not request.path.startswith("/api/") or not i18n.wants_english(request.headers):
        return await handler(request)
    try:
        response = await handler(request)
    except web.HTTPException as e:
        if e.text:
            e.text = i18n.to_english(e.text)
        raise
    if response.content_type == "application/json" and isinstance(response, web.Response) and response.body:
        response.text = json.dumps(i18n.translate_json(json.loads(response.text)), ensure_ascii=False)
    return response


# --- runtime ---

def _manager(request=None):
    """Açık oturumların yöneticisi (süreç genelinde tek: iş dağıtıcısı da aynısını kullanır)."""
    return sessions.manager()


def _plan(request=None):
    """Kullanıcının seçtiği eşzamanlı oturum planı (ayarlarda saklanır)."""
    return sessions.plan_from(studio.get_settings())


def state_response(request=None):
    """Eski tek-oturum alanları + tüm oturumların listesi.

    Eski istemciler (MCP, eski arayüz) yalnız üst düzey alanları okur; varsayılan oturum (slot 0)
    listede olduğu için davranışları değişmez."""
    body = {**runtime.snapshot(), "credit_stats": studio.credit_stats(), "queue": studio.queue_info()}
    if request is not None:
        manager = _manager(request)
        body["sessions"] = manager.snapshot()
        body["limits"] = manager.limits(_plan(request))
    return web.json_response(body)


async def runtime_state(request):
    return state_response(request)


async def runtime_start(request):
    # Eski uç (MCP colab_runtime_start, eski arayüz) da plana uyar: ek oturumlar planı doldurduysa
    # varsayılan oturumu açmak sınırı aşardı.
    if runtime.state["phase"] in ("off", "error"):
        manager, plan = _manager(request), _plan(request)
        extra = sum(1 for rt in manager.live.values() if rt.slot != 0 and rt.state["phase"] != "off")
        if extra >= plan:
            raise web.HTTPConflict(text=f"eşzamanlı oturum sınırı dolu (plan {plan}): planı yükselt ya da bir oturumu kapat")
    runtime.start()
    return state_response(request)


_stop_task = None


def _stop_done(task):
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        runtime.log(f"kapatma başarısız: {runtime.short_error(error, 300)}")


def _background_stop(reason):
    """Kapanışı arka planda yürütür (3 dakikaya kadar sürebilir): istek anında döner, arayüz yoklar."""
    global _stop_task
    _stop_task = asyncio.create_task(runtime.run_stop(reason))
    _stop_task.add_done_callback(_stop_done)
    return _stop_task


_background = set()  # arka plan kapanışları (referans tutulmazsa çöp toplayıcı yarıda kesebilir)


def _keep(task):
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def _stop_default(reason, session=None):
    """Varsayılan oturumu kapatır. Faz hemen "stopping" olur (yanıt güncel durumu göstersin) ve
    bekleyen/çalışan işler iptal edilir: kalsalardı ilki GPU'yu hemen yeniden açardı.

    `session` verilirse yalnız o oturumun işleri iptal edilir: `/api/runtime/stop` (session yok) her
    şeyi durdurur, `POST /api/sessions/{ad}/stop` başka oturumun sırasına dokunmaz."""
    runtime.begin_stop(reason)
    cancelled = studio.cancel_pending(session)
    if cancelled:
        runtime.log(f"GPU elle kapatıldı, {len(cancelled)} bekleyen iş iptal edildi")
    _background_stop(reason)


async def runtime_stop(request):
    """Kapanış 3 dakikaya kadar sürebilir (ssh çıkışı + `colab stop` + artık runtime'lar); isteği
    bekletmek nginx'in 120 sn zaman aşımına takılıp arayüzde hata gösteriyordu. Kapanış arka planda
    yürür, istek anında döner, arayüz durumu yoklayarak izler. Bekleyen ve çalışan işler iptal olur."""
    if runtime.state["phase"] not in ("stopping", "off"):
        _stop_default("elle kapatıldı")
    return state_response(request)


async def runtime_toggle_auto(request):
    runtime.toggle_auto()
    return state_response(request)


async def runtime_metrics(request):
    return web.json_response(list(runtime.metrics))


async def runtime_log(request):
    return web.json_response({"lines": list(runtime.logs)})


# --- oturumlar (çoklu Colab oturumu) ---

def sessions_body(manager, plan):
    return {"sessions": manager.snapshot(), "limits": manager.limits(plan)}


def session_state_response(request):
    manager = _manager(request)
    return web.json_response(sessions_body(manager, _plan(request)))


async def sessions_list(request):
    return session_state_response(request)


async def session_start(request):
    """Yeni oturum(lar) açar. Plan dolduysa 409; açılan oturum arka planda hazırlanır."""
    body = await read_json(request)
    if not isinstance(body, dict):
        bad("istek gövdesi bir nesne olmalı")
    count = body.get("count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= sessions.MAX_SESSIONS:
        bad(f"count 1-{sessions.MAX_SESSIONS} arası olmalı")
    profile = body.get("profile", "image")
    if profile is not None and (not isinstance(profile, str) or profile not in runtime.PACKS):
        bad(f"bilinmeyen model paketi: {profile}")
    packs = body.get("packs")
    if packs is not None:
        if not isinstance(packs, list) or not packs or not all(isinstance(p, str) for p in packs):
            bad("packs model paketi adlarından oluşan boş olmayan bir liste olmalı")
        unknown = sorted(set(packs) - runtime.PACKS)
        if unknown:
            bad(f"bilinmeyen model paketi: {unknown[0]}")
    manager = _manager(request)
    plan = _plan(request)
    limit = min(plan, sessions.MAX_SESSIONS)
    if manager.active_count() >= limit:
        raise web.HTTPConflict(text=f"eşzamanlı oturum sınırı dolu (plan {plan}): planı yükselt ya da bir oturumu kapat")
    keep_warm = bool(studio.get_settings().get(sessions.KEEP_WARM_KEY))
    started = []
    try:
        for _ in range(min(count, limit - manager.active_count())):
            rt = manager.start(pack=profile, keep_warm=keep_warm, packs=packs)
            # Her oturum kendi boşta kapatma/kredi/metrik izlemesini alır; yoksa oturum boşta
            # kaldığında kapanmaz ve kredi yakmaya devam eder.
            manager.watch(rt, lambda rt=rt: studio.has_pending_jobs(rt), studio.record_credits)
            started.append(rt.name)
    except sessions.PortBusy as e:
        raise web.HTTPConflict(text=str(e)) from None
    return web.json_response({**sessions_body(manager, plan), "started": started})


async def session_stop(request):
    name = request.match_info["name"]
    manager = _manager(request)
    if manager.get(name) is None:
        raise web.HTTPNotFound(text=f"oturum bulunamadı: {name}")
    # Kapanış 3 dakikaya kadar sürebilir: isteği bekletmek yerine arka planda yürütülür (arayüz yoklar).
    if name == runtime.SESSION:
        _stop_default("elle kapatıldı", runtime.DEFAULT)
    else:
        # Oturumun bekleyen işleri iptal edilir: kalsalardı kapanan oturumu yeniden açmaya çalışırlardı.
        cancelled = studio.cancel_pending(name)
        if cancelled:
            runtime.log(f"{name} kapatıldı, {len(cancelled)} bekleyen iş iptal edildi")
        _keep(asyncio.create_task(manager.stop(name)))
    return session_state_response(request)


async def sessions_stop_all(request):
    manager = _manager(request)
    # Önce bekleyen işler iptal edilir: kalsalardı biri GPU'yu hemen yeniden açardı.
    cancelled = studio.cancel_pending()
    if cancelled:
        runtime.log(f"tüm oturumlar kapatıldı, {len(cancelled)} bekleyen iş iptal edildi")
    runtime.begin_stop("hepsi kapatıldı")
    _keep(asyncio.create_task(manager.stop_all("hepsi kapatıldı")))
    return session_state_response(request)


async def session_plan_get(request):
    settings = studio.get_settings()
    plan = sessions.plan_from(settings)
    return web.json_response({**sessions.limits(_manager(request).active_count(), plan),
                              "asked": settings.get(sessions.ASKED_KEY) is not None,
                              "keep_warm": bool(settings.get(sessions.KEEP_WARM_KEY))})


async def session_plan_put(request):
    """Kullanıcının "kaç oturum?" cevabı. `asked_at` damgalanır: soru bir kez sorulur."""
    body = await read_json(request)
    if not isinstance(body, dict):
        bad("istek gövdesi bir nesne olmalı")
    plan = sessions.clamp_plan(body.get("plan"))
    studio.save_setting(sessions.PLAN_KEY, plan)
    studio.save_setting(sessions.ASKED_KEY, time.time())
    if "keep_warm" in body:
        studio.save_setting(sessions.KEEP_WARM_KEY, bool(body["keep_warm"]))
    return await session_plan_get(request)


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

def fan_out_sessions(count):
    """Paralel üretimde işlerin dağıtılacağı oturumlar: en az yüklü `count` açık oturum.

    Açık oturum yoksa tek iş varsayılan oturumda çalışır (None = yönlendirici karar verir)."""
    manager = _manager()
    live = sorted(router.usable(manager), key=lambda rt: router.score(rt, load=studio.pending_count))
    return [rt.name for rt in live[:count]] or [None]


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
    # Yanlış türde girdi ancak GPU 4-5 dakikada açıldıktan sonra hata verirdi: burada reddedilir.
    if kind in ("image", "video", "enhance") and any(Path(i).suffix not in IMAGE_TYPES.values() for i in inputs):
        bad("referans dosyaları görsel olmalı")
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
    session_param = body.get("session")
    if session_param in ("", "auto"):  # "auto": yönlendirici karar verir (iş kaydında boş kalır)
        session_param = None
    if session_param is not None and (not isinstance(session_param, str)
                                      or sessions.manager().get(session_param) is None):
        raise web.HTTPNotFound(text=f"oturum bulunamadı: {session_param}")
    parallel = body.get("parallel", 1)
    if not isinstance(parallel, int) or isinstance(parallel, bool) or not 1 <= parallel <= sessions.MAX_SESSIONS:
        bad(f"parallel 1-{sessions.MAX_SESSIONS} arası olmalı")
    if session_param and parallel > 1:
        bad("belirli bir oturum seçildiyse parallel 1 olmalı")
    thread_id = None if kind == "enhance" else body.get("thread_id")
    if thread_id is not None and studio.get_thread(thread_id) is None:
        raise web.HTTPNotFound(text="sohbet bulunamadı")
    if kind != "enhance" and thread_id is None:
        thread_id = studio.create_thread(mode, prompt)["id"]
    # Tek işte oturum boş bırakılır: yönlendirici kararı çalışma anında (daha güncel durumla) verir.
    if session_param:
        targets = [session_param]
    else:
        targets = fan_out_sessions(parallel) if parallel > 1 else [None]
    group_id = uuid.uuid4().hex if parallel > 1 else None
    try:
        created = [studio.create_job(thread_id, mode, kind, prompt, params, inputs,
                                     session=name, group_id=group_id) for name in targets]
    except ValueError as e:
        bad(str(e))
    if parallel == 1 and len(created) == 1:  # tek iş: eski yanıt biçimi (mevcut istemciler bozulmasın)
        return web.json_response(created[0])
    return web.json_response({"jobs": created, "group_id": group_id})


async def media_list(request):
    try:
        limit = max(1, min(int(request.query.get("limit", 30)), 500))
    except ValueError:
        bad("limit bir sayı olmalı")
    return web.json_response(studio.list_media(limit, request.query.get("ext")))


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

SETTING_KEYS = {"mode", "image.params", "video.params", "music.params", "speech.params", "transcribe.params",
                "model3d.params", "sessions.plan", "sessions.asked_at", "sessions.keep_warm"}


async def models_get(request):
    """Model kataloğundaki (models.toml) ad ve açıklamalar; arayüz ayarlarda gösterir."""
    return web.json_response({m: {"label": e["label"], "description": e["description"]} for m, e in workflows.CATALOG.items()})


async def settings_get(request):
    return web.json_response(studio.get_settings())


async def setting_put(request):
    key = request.match_info["key"]
    if key not in SETTING_KEYS:
        bad(f"bilinmeyen ayar: {key}")
    value = await read_json(request)
    # Ayarlar her cihazda açılışta okunur: geçersiz değer arayüzü her yerde bozardı.
    if key == "mode" and (not isinstance(value, str) or value not in MODES):
        bad(f"geçersiz mod: {value}")
    if key != "mode" and not key.startswith("sessions.") and not isinstance(value, dict):
        bad(f"{key} bir nesne olmalı")
    studio.save_setting(key, value)
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
        # açılır; watchdog temizliği yeniden dener ve başarınca hatayı kaldırır (ör. bilgisayar açılırken
        # ağ henüz yoktu).
        try:
            await runtime.stop_runtime()
        except Exception as e:  # servis ne olursa olsun açılsın
            runtime.set_phase("error", runtime.short_error(e))
            runtime.startup_cleanup_failed = True
        # Önceki çalışmadan kalan ek oturumlar (çökme, docker compose down, yarıda kalan kapanış):
        # Colab'da açık kalıp kredi yakmasınlar, slotları da boşalsın. Bırakılamayan kayıtta kalır.
        try:
            result = await sessions.manager().reap_leftovers()
            if result["released"]:
                runtime.log(f"önceki çalışmadan kalan oturumlar kapatıldı: {', '.join(result['released'])}")
            for error in result["errors"]:
                runtime.log(f"önceki çalışmadan kalan oturum kapatılamadı: {error}")
        except Exception as e:
            runtime.log(f"önceki çalışmadan kalan oturumlar temizlenemedi: {runtime.short_error(e, 300)}")
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
    # Kapatılamazsa sonraki açılışta on_startup yeniden dener. Ek oturumlar da kapatılır (Docker'da ve
    # run.sh'ta systemd'nin ExecStopPost adımı yok); aynı anda kapatılırlar, her biri dakikalar sürebilir.
    manager = sessions.manager()
    extra = [name for name, rt in manager.live.items() if rt.slot != 0]
    results = await asyncio.gather(*(manager.stop(name, "servis durdu") for name in extra), return_exceptions=True)
    for name, result in zip(extra, results):
        if isinstance(result, BaseException):
            runtime.log(f"{name} kapatılamadı: {runtime.short_error(result, 300)}")
    if runtime.state["phase"] != "off":
        await runtime.stop("servis durdu")


def create_app():
    studio.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    app = web.Application(client_max_size=50 * 1024 * 1024, middlewares=[host_guard, csrf_guard, english_for_other_clients])
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.add_routes([
        web.get("/api/runtime", runtime_state),
        web.post("/api/runtime/start", runtime_start),
        web.post("/api/runtime/stop", runtime_stop),
        web.post("/api/runtime/toggle-auto", runtime_toggle_auto),
        web.get("/api/runtime/metrics", runtime_metrics),
        web.get("/api/runtime/log", runtime_log),
        web.get("/api/sessions", sessions_list),
        web.post("/api/sessions", session_start),
        web.post("/api/sessions/stop-all", sessions_stop_all),
        web.post("/api/sessions/{name}/stop", session_stop),
        web.get("/api/sessions/plan", session_plan_get),
        web.put("/api/sessions/plan", session_plan_put),
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
        web.get("/api/media", media_list),
        web.static("/media", studio.MEDIA_DIR),
        web.static("/assets", WEB_DIST / "assets"),
        web.get("/", spa),
        web.get("/{tail:(?!api/|media/|assets/).*}", spa),
    ])
    return app


def _banner(_aiohttp_message=None):
    """Printed by run_app once the socket is bound, so a URL is only shown when it really works."""
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
    elif config.HOST in ("0.0.0.0", "::"):
        print(f"Colab Studio {version}: http://127.0.0.1:{config.PORT}/ on this machine, "
              f"http://<this machine's IP>:{config.PORT}/ from other devices", flush=True)
    else:
        print(f"Colab Studio {version}: http://{config.HOST}:{config.PORT}/", flush=True)


def _exit_if_cannot_listen(e):
    if e.errno == errno.EADDRINUSE:
        sys.exit(f"Colab Studio: port {config.PORT} is already in use (another Colab Studio, or another "
                 "program, is listening there). Stop it, or set another STUDIO_PORT in .env.")
    sys.exit(f"Colab Studio: cannot listen on {config.HOST}:{config.PORT} ({e.strerror or e}). STUDIO_HOST "
             "must be 127.0.0.1, 0.0.0.0 or an address of this machine.")


def _check_listen_address():
    """Binds once before the app starts: on_startup releases the studio's Colab session, which must not
    happen when this is a second copy that cannot listen anyway (it would stop the first copy's GPU)."""
    try:
        family, kind, proto, _, addr = socket.getaddrinfo(
            config.HOST, config.PORT, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)[0]
        with socket.socket(family, kind, proto) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # aiohttp does the same
            s.bind(addr)
    except OSError as e:
        _exit_if_cannot_listen(e)


if __name__ == "__main__":
    _check_listen_address()
    try:
        web.run_app(create_app(), host=config.HOST, port=config.PORT, print=_banner)
    except OSError as e:  # lost a race for the port after the check
        _exit_if_cannot_listen(e)
