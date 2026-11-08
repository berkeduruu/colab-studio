"""Colab GPU runtime'ının yaşam döngüsü.

Runtime'ı açar ve kapatır, modelleri HuggingFace'ten indirtip ComfyUI'yi kurar ve bu sunucuya
yönlendirir (127.0.0.1:8188), boşta kalınca runtime'ı kapatır, kredi ve sistem kullanımını izler.
"""
import asyncio
import json
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path

from aiohttp import ClientConnectorError, ClientSession, ClientTimeout

import config

COLAB = config.COLAB
COLAB_SESSIONS = config.COLAB_SESSIONS
SESSION = config.SESSION
GPU = config.GPU
IDLE_MINUTES = config.IDLE_MINUTES
COMFY_PORT = 8188
COMFY_URL = f"http://127.0.0.1:{COMFY_PORT}"
REPO_DIR = config.REPO_DIR
DATA_DIR = config.DATA_DIR
DURATIONS_FILE = DATA_DIR / "startup_durations.json"
OWNED_FILE = DATA_DIR / "owned_endpoints.json"  # stüdyonun açtığı GPU runtime'ları
SSH_DIR = DATA_DIR / "ssh"
SSH_CONF = SSH_DIR / "config"
SSH_KEY = SSH_DIR / "id_ed25519"
# Unix soket yolu en çok ~104 bayt olabilir (ssh kendi soneki için de yer ister). STUDIO_DATA uzunsa
# SSH_DIR altındaki yol aşılıyor ve ssh açılmıyordu: soket her zaman kısa bir dizine konur, adı %C (özet).
SSH_SOCKET_DIR = Path(f"/tmp/colab-studio-{os.getuid()}")
SSH = ["ssh", "-F", str(SSH_CONF)]
POLL_SECONDS = 30
METRICS_SECONDS = 5
METRICS_HISTORY = 120  # 10 dakika
MODEL_POLL_SECONDS = 3
# Model beklerken bu süre boyunca hiçbir paketin inen baytı artmazsa indirici takılmış sayılır.
MODEL_STALL_SECONDS = 600


def ssh_conf_text():
    return f"""Host colab
  ProxyCommand sh {Path(__file__).parent / "colab_proxy.sh"} {SESSION} {COLAB_SESSIONS} {COLAB} {SSH_KEY}
  User root
  IdentityFile {SSH_KEY}
  IdentitiesOnly yes
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  LogLevel ERROR
  ControlMaster auto
  ControlPath {SSH_SOCKET_DIR}/%C
  ControlPersist yes
  ServerAliveInterval 15
  ServerAliveCountMax 4
  ConnectTimeout 30
"""


COLAB_TOKEN = Path("~/.config/colab-cli/token.json").expanduser()


def preflight():
    """Kurulum eksikse kullanıcıya gösterilecek hata; tamamsa None. Eksik kurulumda colab/ssh
    çağrıları anlaşılmaz "komut bulunamadı" hatalarıyla düşmesin."""
    if not os.access(COLAB, os.X_OK):
        return f"colab CLI bulunamadı ({COLAB}): ./install.sh çalıştır ya da .env'de COLAB_BIN ayarla"
    if not COLAB_TOKEN.exists():
        if os.environ.get("STUDIO_IN_DOCKER"):
            return "colab CLI'da Google girişi yok: 'docker compose run --rm studio colab usage' ile giriş yap, sonra GPU'yu tekrar başlat"
        return "colab CLI'da Google girişi yok: terminalde 'colab usage' çalıştırıp giriş yap, sonra GPU'yu tekrar başlat"
    for tool in ("ssh", "ssh-keygen", "tar"):
        if shutil.which(tool) is None:
            return f"{tool} bulunamadı: openssh-client ve tar kurulu olmalı"
    return None


async def ensure_ssh_key():
    """Stüdyoya ait SSH anahtarı (kullanıcının ~/.ssh'ına dokunulmaz); yoksa üretilir."""
    SSH_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    SSH_SOCKET_DIR.mkdir(exist_ok=True, mode=0o700)
    if not SSH_KEY.exists():
        await run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "colab-studio", "-f", str(SSH_KEY), timeout=30)


# Açılış adımları: (kimlik, ekranda görünen ad, ilk tahmini süre sn). Tahminler ölçülen
# sürelerle güncellenir.
STEPS = [
    ("allocate", "GPU ayrılıyor", 60),
    ("connect", "Bağlanılıyor", 15),
    ("install", "Modeller indiriliyor, ComfyUI kuruluyor", 90),
    ("comfy", "ComfyUI başlatılıyor", 40),
]

state = {
    "phase": "off",  # off | starting | ready | stopping | error
    "detail": "",
    "auto_shutdown": True,
    "last_busy": None,
    "start_balance": None,
    "ready_at": None,
    "credits": None,
    "startup": None,  # {"step": i, "started_at": t, "step_started_at": t}
}
logs = deque(maxlen=300)
metrics = deque(maxlen=METRICS_HISTORY)
ready = asyncio.Event()
_start_task = None
# Model paketi inmiş iş türleri (station/models.py). Görsel paketi açılışta beklenir,
# diğerleri arka planda iner; iş gelince paketi inene kadar beklenir.
local_modes = set()
# Bu oturumda en az bir işi bitmiş türler: modelleri bir kez diskten okunup yüklenmiştir.
warm = set()
warming = False
_has_pending = lambda: False
# İnen model paketlerinin ilerlemesi (station/models.py'nin progress.json'u): paket -> {total, done}
packs = {}
# (zaman, toplam inen bayt): ilk ve son ölçüm. Hız indirmenin başından beri ortalama: xet dosyayı
# hızla çekip sonunda diske yazarken dakikalarca duraksıyor; anlık hız sıfıra düşüp kalan süreyi
# saatlere çıkarmasın.
_pack_samples = []
setup_phase = {"text": None}  # kurulum betiğinin aşaması ("3/5 ..."), açılışta okunur
PACK_LABELS = {"image": "Görsel", "speech": "Ses", "enhance": "İstem geliştirme", "model3d": "3D", "video": "Video", "music": "Müzik"}
# station/models.py'deki paketler; yazıya dökme (Whisper) ve Chatterbox modellerini eklentileri ilk
# kullanımda kendisi indirir (küçükler).
PACKS = {"image", "speech", "enhance", "model3d", "video", "music"}


def log(msg):
    logs.append(f"{time.strftime('%H:%M:%S')} {msg}")


def short_error(e, limit=700):
    """Hata metnini kısaltır: komut çıktısında asıl hata en sondadır, ilk satır ise neyin
    başarısız olduğunu söyler; ikisi de kalır."""
    text = str(e) or type(e).__name__
    if len(text) <= limit:
        return text
    head = text.split("\n", 1)[0][:150]
    return f"{head}\n…\n{text[-(limit - len(head) - 3):]}"


def set_phase(phase, detail=""):
    state["phase"] = phase
    state["detail"] = detail
    if phase == "ready":
        ready.set()
    else:
        ready.clear()
    if phase != "starting":
        state["startup"] = None
    log(f"[{phase}] {detail}")


def expected_durations():
    """Öğrenilmiş açılış adımı süreleri. Dosya bozuksa (yarım yazma, elle düzenleme, disk hatası)
    varsayılanlara düşülür: bozuk dosya arayüzün tamamını 500'e düşürmemeli (_learn atomik yazar)."""
    learned = {}
    try:
        data = json.loads(DURATIONS_FILE.read_text())
        if isinstance(data, dict):
            learned = {k: v for k, v in data.items() if isinstance(v, (int, float))}
    except (OSError, ValueError) as e:
        log(f"açılış süreleri okunamadı, varsayılanlar kullanılıyor: {short_error(e, 200)}")
    return {key: learned.get(key, default) for key, _, default in STEPS}


def _learn(i):
    """Adımın ölçülen süresini öğrenir (yeni ölçüm %50 ağırlıklı). Dosya atomik yazılır: yazma
    sırasında bir çökme yarım JSON bırakmasın."""
    key = STEPS[i][0]
    learned = expected_durations()
    learned[key] = round(0.5 * learned[key] + 0.5 * (time.time() - state["startup"]["step_started_at"]), 1)
    tmp = DURATIONS_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(learned))
        os.replace(tmp, DURATIONS_FILE)
    except OSError as e:
        log(f"açılış süresi kaydedilemedi: {short_error(e, 200)}")


def _begin_step(i):
    if i > 0:
        _learn(i - 1)
    state["startup"]["step"] = i
    state["startup"]["step_started_at"] = time.time()
    state["detail"] = STEPS[i][1]
    log(f"[starting] {STEPS[i][1]}")


async def run(*cmd, timeout, shell=False, quiet=False, stdin_data=None):
    if shell:
        proc = await asyncio.create_subprocess_shell(
            cmd[0], stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin_data), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"zaman aşımı ({timeout}s): {' '.join(cmd)[:120]}") from None
    except asyncio.CancelledError:
        # Açılış iptal edildiyse "colab new" gibi süreçler arkada sürüp runtime açmasın.
        proc.kill()
        raise
    text = out.decode(errors="replace")
    if not quiet:
        for line in text.splitlines()[-20:]:
            log(line)
    if proc.returncode != 0:
        raise RuntimeError(f"komut başarısız ({proc.returncode}): {' '.join(cmd)[:120]}\n{text[-800:]}") from None
    return text


async def _start():
    problem = preflight()
    if problem:
        set_phase("error", problem)
        return
    try:
        _begin_step(0)
        await run(COLAB, "new", "--gpu", GPU, "-s", SESSION, timeout=600)
        _own(json.loads(COLAB_SESSIONS.read_text())[SESSION]["endpoint"])
        state["start_balance"] = parse_usage(await run(COLAB, "usage", timeout=60))["balance"]

        _begin_step(1)
        await ensure_ssh_key()
        SSH_CONF.write_text(ssh_conf_text())
        await connect()

        _begin_step(2)
        if config.HF_TOKEN:
            await run(*SSH, "colab", "install -D -m 600 /dev/stdin /root/.cache/huggingface/token",
                      timeout=30, stdin_data=config.HF_TOKEN.encode())
        else:
            log("HF_TOKEN yok: modeller girişsiz iner (Hugging Face hız sınırına takılabilir)")
        await run(f"tar -C '{REPO_DIR}' -cz --exclude=__pycache__ station | ssh -F '{SSH_CONF}' colab 'tar -C /content -xz'", timeout=120, shell=True)
        # Model kataloğu (models.toml): station/models.py ve iş akışları aynı dosyayı kullanır.
        await run(*SSH, "colab", "install -D -m 644 /dev/stdin /content/station/models.toml",
                  timeout=30, stdin_data=config.MODELS_FILE.read_bytes())
        poller = asyncio.create_task(poll_packs())
        try:
            await run(*SSH, "colab", f"bash /content/station/runtime_setup.sh {state['startup']['pack'] or 'none'}", timeout=1800)
        finally:
            poller.cancel()

        _begin_step(3)
        await forward_comfy()
        await wait_comfy_ready()
        _learn(len(STEPS) - 1)
        if state["startup"]["pack"]:
            local_modes.add(state["startup"]["pack"])
        state["last_busy"] = time.time()
        state["ready_at"] = time.time()
        set_phase("ready", "GPU hazır")
        asyncio.create_task(warmup())
    except Exception as e:
        # Önce kapat, sonra hata göster: hata durumunda yeni bir açılış başlayabilir ve kapanış
        # onun oturumunu öldürmemeli.
        set_phase("stopping", "açılış başarısız, kapatılıyor")
        try:
            await stop_runtime()
        except RuntimeError as stop_error:
            log(short_error(stop_error))  # bırakılamayan runtime'ı watchdog yeniden dener
        set_phase("error", short_error(e))


async def forward_comfy():
    """ComfyUI portunu SSH ana bağlantısı üzerinden sunucuya yönlendirir."""
    await run(*SSH, "-O", "forward", "-L", f"127.0.0.1:{COMFY_PORT}:127.0.0.1:{COMFY_PORT}", "colab", timeout=30)


async def connect(attempts=3, delay=5):
    """SSH ana bağlantısını kurar. Yeni runtime'da Colab'ın SSH köprüsü ara sıra mesaj vermeden
    kapanıyor (aynı runtime'a hemen ardından bağlanılabiliyor); birkaç kez denenir."""
    for attempt in range(1, attempts + 1):
        try:
            return await run(*SSH, "-fN", "colab", timeout=90)
        except RuntimeError:
            if attempt == attempts:
                raise
            log(f"SSH bağlantısı kurulamadı, yeniden deneniyor ({attempt}/{attempts - 1})")
            await asyncio.sleep(delay)


def start(pack="image"):
    """Runtime'ı arka planda açar; zaten açılıyorsa ya da açıksa bir şey yapmaz. pack: açılışı
    tetikleyen işin model paketi; ilk o iner ve hazır sayılmak için yalnızca o beklenir. None: işin
    indirilecek paketi yok (yazıya dökme; Whisper'ı eklenti kendisi indirir), hiçbir paket beklenmez."""
    global _start_task
    if state["phase"] in ("off", "error"):
        set_phase("starting", STEPS[0][1])
        now = time.time()
        state["startup"] = {"step": 0, "started_at": now, "step_started_at": now, "pack": pack if pack in PACKS or pack is None else "image"}
        _start_task = asyncio.create_task(_start())


# ComfyUI, kurulum betiğinin venv'indeki python ile başlar ("/content/venv/bin/python main.py ..."). Köşeli
# parantezli "main[.]py": pgrep'i çalıştıran kabuğun kendi komut satırı desene uymaz.
COMFY_PGREP = "(^|/)python[0-9.]* main[.]py"
COMFY_PROCESS = f"pgrep -f '{COMFY_PGREP}' >/dev/null && echo alive || echo dead"


async def wait_comfy_ready():
    """ComfyUI /queue'ya yanıt verene kadar bekler. Süreç ölmüşse (kurulum hatası, çökme) 10 dakika
    beklemeden comfy.log'un sonuyla hemen hata verir."""
    deadline = time.time() + 600
    async with ClientSession(timeout=ClientTimeout(total=5)) as http:
        while time.time() < deadline:
            try:
                async with http.get(f"{COMFY_URL}/queue") as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            if (await run(*SSH, "colab", COMFY_PROCESS, timeout=20, quiet=True)).strip() != "alive":
                tail = await run(*SSH, "colab", "tail -n 40 /content/comfy.log", timeout=30, quiet=True)
                raise RuntimeError(f"ComfyUI süreci öldü:\n{tail}")
            await asyncio.sleep(3)
    tail = await run(*SSH, "colab", "tail -n 40 /content/comfy.log", timeout=30)
    raise RuntimeError(f"ComfyUI açılmadı:\n{tail}")


WARMUP_POLL_SECONDS = 10
WARMUP_DEADLINE_SECONDS = 1800  # görsel paketi bu süre içinde inmezse ön ısıtma bırakılır


async def pack_state(pack):
    """"done" | "failed" | "pending": paketin inme durumu tek ssh çağrısında okunur."""
    out = await run(
        *SSH, "colab",
        f"if [ -f /content/packs/{pack}.done ]; then echo done; "
        f"elif [ -f /content/packs/{pack}.error ]; then echo failed; else echo pending; fi",
        timeout=30, quiet=True,
    )
    return out.strip() or "pending"


async def warmup():
    """Görsel modellerini (UNET, CLIP, VAE) arka planda GPU'ya yükler (~55 sn). Kuyrukta iş yokken ve
    görsel paketi inmişken çalışır (GPU'yu başka bir iş açtırdıysa paket sonradan iner; o zaman beklenir).
    Kullanıcı iş gönderirse hemen kesilir ve iş bitince yeniden denenir; boşta sayacını uzatmaz.
    Paket inemezse ya da WARMUP_DEADLINE_SECONDS içinde gelmezse vazgeçer: sonsuz yoklamada her turda
    ssh açıp durmaz."""
    deadline = time.time() + WARMUP_DEADLINE_SECONDS
    while state["phase"] == "ready" and "image" not in warm:
        try:
            if not _has_pending():
                if "image" not in local_modes:
                    pack = await pack_state("image")
                    if pack == "failed":
                        log("görsel paketi inemedi, ön ısıtma yapılmıyor")
                        return
                    if pack != "done":
                        if time.time() > deadline:
                            log("görsel paketi beklendi, gelmedi; ön ısıtma bırakıldı")
                            return
                        await asyncio.sleep(WARMUP_POLL_SECONDS)
                        continue
                    local_modes.add("image")
                if await _warm_image() != "interrupted":
                    return
        except RuntimeError as e:
            log(f"ön ısıtma atlandı: {e}")
            return
        await asyncio.sleep(WARMUP_POLL_SECONDS)


async def _warm_image():
    """"done" | "interrupted" (kullanıcı işi geldi) | "failed"."""
    global warming
    import workflows
    warming = True
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as http:
            async with http.post(f"{COMFY_URL}/prompt", json={"prompt": workflows.image_warmup()}) as r:
                if r.status != 200:
                    return "failed"
                pid = (await r.json())["prompt_id"]
            while state["phase"] == "ready":
                if _has_pending():
                    await http.post(f"{COMFY_URL}/interrupt")
                    return "interrupted"
                async with http.get(f"{COMFY_URL}/history/{pid}") as r:
                    h = await r.json()
                if pid in h:
                    if h[pid].get("status", {}).get("completed"):
                        warm.add("image")
                        log("görsel modeli önceden yüklendi")
                        return "done"
                    return "failed"
                await asyncio.sleep(0.5)
            return "failed"
    except Exception as e:
        log(f"ön ısıtma atlandı: {e}")
        return "failed"
    finally:
        warming = False


def _owned():
    return json.loads(OWNED_FILE.read_text()) if OWNED_FILE.exists() else []


def _own(endpoint):
    OWNED_FILE.write_text(json.dumps(_owned() + [endpoint]))


async def reap_owned():
    """Stüdyonun açtığı ama hâlâ açık kalan runtime'ları bırakır (ör. yarım kalmış bir açılıştan
    artakalan, artık "comfy" adını taşımayan oturum). Yalnızca kendi açtıklarına dokunur."""
    owned = _owned()
    if not owned:
        return
    sessions = await run(COLAB, "sessions", timeout=60, quiet=True)
    alive = [e for e in owned if e in sessions]
    for e in alive:
        log(f"stüdyonun açık kalan runtime'ı kapatılıyor: {e}")
        await run(config.colab_python(), "-c", f"from colab_cli.common import state; state.client.unassign({e!r})", timeout=60)
    # Bu arada yeni bir açılış kendi runtime'ını eklemiş olabilir; yalnızca incelenenler silinir.
    OWNED_FILE.write_text(json.dumps([e for e in _owned() if e not in owned]))


async def stop_runtime():
    """Runtime'ı kapatır; kredi harcamasın diye bir adım başarısız olsa da diğerlerini dener, yerel
    durumu her durumda temizler. Bir adım başarısızsa sonunda RuntimeError verir; stüdyonun açtığı
    runtime bırakılamadıysa watchdog onu reap_owned ile yeniden dener."""
    errors = []
    if SSH_CONF.exists():
        try:
            await run(*SSH, "-O", "exit", "colab", timeout=20)
        except RuntimeError as e:
            log(str(e))  # ana bağlantı zaten yoksa olağan
    try:
        sessions = await run(COLAB, "sessions", timeout=60)
        if f"[{SESSION}]" in sessions:
            await run(COLAB, "stop", "-s", SESSION, timeout=120)
    except RuntimeError as e:
        errors.append(e)
    try:
        await reap_owned()
    except RuntimeError as e:
        errors.append(e)
    state["last_busy"] = None
    state["start_balance"] = None
    state["ready_at"] = None
    metrics.clear()
    local_modes.clear()
    warm.clear()
    packs.clear()
    _pack_samples.clear()
    setup_phase["text"] = None
    global warming
    warming = False
    if errors:
        raise RuntimeError(f"GPU kapatılamadı, yeniden denenecek: {errors[-1]}")


def update_packs(text):
    """Runtime'dan okunan progress.json'u işler; dosya henüz yoksa (indirme başlamadı) boş gelir."""
    if not text.strip():
        return
    packs.clear()
    packs.update(json.loads(text))
    sample = (time.time(), sum(p["done"] for p in packs.values()))
    if _pack_samples:
        _pack_samples[1:] = [sample]
    else:
        _pack_samples.append(sample)


def download_rate():
    if len(_pack_samples) < 2:
        return None
    (t0, b0), (t1, b1) = _pack_samples
    return (b1 - b0) / (t1 - t0) if t1 > t0 and b1 > b0 else None


def pack_status(pack):
    """Paketin inme durumu: oran, bayt, hız ve kalan süre (paketler sırayla iner; öndekiler de beklenir)."""
    p = packs.get(pack)
    if p is None:
        return None
    rate = download_rate()
    ahead = 0
    for name, q in packs.items():  # sırayla iner: bu paketten öncekilerin kalanı da beklenir
        ahead += q["total"] - q["done"]
        if name == pack:
            break
    return {
        "total": p["total"], "done": p["done"], "fraction": p["done"] / p["total"] if p["total"] else 1.0,
        "rate": rate, "eta": round(ahead / rate) if rate and ahead else (0 if not ahead else None),
    }


async def poll_packs():
    """Açılışta kurulum betiği sürerken indirme ilerlemesini ve kurulum aşamasını okur (gösterim için)."""
    while True:
        try:
            out = await run(*SSH, "colab", "cat /content/packs/progress.json 2>/dev/null; echo; cat /content/setup_phase 2>/dev/null; true",
                            timeout=15, quiet=True)
            lines = out.split("\n")
            update_packs(lines[0])
            setup_phase["text"] = (lines[1].strip() if len(lines) > 1 else "") or None
        except RuntimeError:
            pass  # bağlantı anlık koptuysa bir sonraki okumada güncellenir
        await asyncio.sleep(3)


async def ensure_models(pack, on_wait=None):
    """İş türünün model paketi runtime'da inene kadar bekler; beklerken on_wait(pack_status) çağrılır
    (on_wait hata fırlatırsa, ör. iş iptal edildiyse, bekleme biter). Paket inmiyorsa indirme sırası
    bu pakete verilir (models.py, dosya sınırında ona geçer). İndirici öldüyse ya da MODEL_STALL_SECONDS
    boyunca inen bayt artmadıysa hata verilir: yoksa iş sonsuza kadar "çalışıyor" kalır ve GPU kapanmaz."""
    if pack not in local_modes:
        await run(*SSH, "colab", f"mkdir -p /content/packs && echo {pack} > /content/packs/priority", timeout=30, quiet=True)
    last_bytes, last_moved = None, time.time()
    while pack not in local_modes:
        if on_wait:
            on_wait(pack_status(pack))
        total_bytes = sum(p["done"] for p in packs.values())
        if total_bytes != last_bytes:
            last_bytes, last_moved = total_bytes, time.time()
        elif time.time() - last_moved > MODEL_STALL_SECONDS:
            raise RuntimeError(f"{PACK_LABELS.get(pack, pack)} modelleri {MODEL_STALL_SECONDS // 60} dakikadır inmiyor, indirme takıldı")
        out = await run(
            *SSH, "colab",
            f"if [ -f /content/packs/{pack}.done ]; then echo done; "
            f"elif [ -f /content/packs/{pack}.error ]; then tail -3 /content/packs/{pack}.error; exit 1; "
            # [.] kalıbın bu kabuğun kendi komut satırıyla eşleşmesini önler
            f"elif ! pgrep -f 'station/models[.]py' >/dev/null && [ ! -f /content/packs/{pack}.done ]; then "
            f"echo 'model indirici durdu:'; tail -5 /content/models.log; exit 1; fi",
            timeout=30, quiet=True,
        )
        if out.strip() == "done":
            local_modes.add(pack)
        else:
            await asyncio.sleep(MODEL_POLL_SECONDS)


def begin_stop(reason):
    """Kapanışı senkron başlatır: açılış sürüyorsa iptal eder ve fazı hemen "stopping" yapar. İsteği
    bekletmemek için (nginx 120 sn) kapanışın kendisi run_stop ile arka planda yürütülür."""
    global _start_task
    if _start_task and not _start_task.done():
        _start_task.cancel()
    set_phase("stopping", reason)


async def run_stop(reason):
    """Kapanışı yürütür; begin_stop ile aynı işi bir kez daha (zararsız) yapar ki tek başına da
    kullanılabilsin."""
    begin_stop(reason)
    try:
        await stop_runtime()
    except RuntimeError as e:
        set_phase("error", short_error(e))
        return
    set_phase("off", reason)


async def stop(reason):
    await run_stop(reason)


def touch():
    """Stüdyo bir iş gönderdiğinde boşta sayacını sıfırlar."""
    if state["phase"] == "ready":
        state["last_busy"] = time.time()


def toggle_auto():
    state["auto_shutdown"] = not state["auto_shutdown"]
    touch()


def parse_usage(text):
    balance = re.search(r"Current balance:\s*([\d.]+)", text)
    rate = re.search(r"Usage rate:\s*([\d.]+)", text)
    if not balance or not rate:
        raise RuntimeError(f"colab usage çıktısı okunamadı: {text[-200:]}")
    return {"balance": float(balance.group(1)), "rate": float(rate.group(1))}


async def log_crash_context():
    """Runtime kapanmadan önce ComfyUI'nin son kaydını ve bellek durumunu kayda düşer."""
    try:
        await run(*SSH, "colab", "pgrep -af main.py; free -g; tail -40 /content/comfy.log", timeout=20)
    except RuntimeError as e:
        log(f"çökme kaydı alınamadı: {e}")


async def watchdog(has_pending_jobs, on_credits):
    """Kredi, boşta kapatma ve ComfyUI çökmesini izler. Bir turdaki beklenmedik hata döngüyü
    durdurmaz: durursa boşta kapatma da durur ve GPU kredi harcamaya devam eder."""
    global _has_pending
    _has_pending = has_pending_jobs
    async with ClientSession(timeout=ClientTimeout(total=10)) as http:
        while True:
            try:
                await _watch_once(http, has_pending_jobs, on_credits)
            except Exception as e:
                log(f"izleme turu başarısız, sonraki turda yeniden denenecek: {short_error(e, 300)}")
            await asyncio.sleep(POLL_SECONDS)


async def _watch_once(http, has_pending_jobs, on_credits):
    if not COLAB_TOKEN.exists():
        # Girişsizken `colab usage` her turda bir OAuth akışı başlatıp log'u şişirir; giriş yapılınca
        # sonraki tur krediyi yeniden okur.
        state["credits"] = {"error": "colab CLI'da Google girişi yok"}
    else:
        try:
            state["credits"] = parse_usage(await run(COLAB, "usage", timeout=60, quiet=True))
            on_credits(state["credits"])
        except RuntimeError as e:
            state["credits"] = {"error": str(e)[:200]}
    if state["phase"] in ("off", "error"):
        await reap_owned()
    if state["phase"] != "ready":
        return
    try:
        async with http.get(f"{COMFY_URL}/queue") as r:
            q = await r.json()
        if (q["queue_running"] and not warming) or q["queue_pending"] or has_pending_jobs():
            state["last_busy"] = time.time()
        elif state["auto_shutdown"] and time.time() - state["last_busy"] >= IDLE_MINUTES * 60:
            await stop(f"{IDLE_MINUTES} dk boşta kaldı, GPU kapatıldı")
    except OSError as e:
        # Büyük model yüklerken ComfyUI saniyelerce yanıt vermeyebilir; süreç
        # yaşıyorsa meşgul sayılır, yalnızca süreç ya da bağlantı yoksa kapatılır.
        try:
            await run(*SSH, "colab", f"pgrep -f '{COMFY_PGREP}'", timeout=20, quiet=True)
            state["last_busy"] = time.time()
            # Port kapalıysa (bağlantı reddedildi) SSH ana bağlantısı kopmuş, sonraki ssh
            # komutu yeni ana bağlantıyı yönlendirmesiz açmıştır: yönlendirme yeniden kurulur.
            if isinstance(e, ClientConnectorError) and isinstance(e.os_error, ConnectionRefusedError):
                log("ComfyUI portu kapalı (SSH bağlantısı yenilenmiş), yönlendirme yeniden kuruluyor")
                await forward_comfy()
        except RuntimeError:
            set_phase("stopping", "ComfyUI'ye ulaşılamıyor, kapatılıyor")
            await log_crash_context()
            try:
                await stop_runtime()
            except RuntimeError as stop_error:
                log(short_error(stop_error))  # bırakılamayan runtime sonraki turda reap_owned ile denenir
            set_phase("error", f"ComfyUI'ye ulaşılamıyor, runtime kapatıldı: {e!r}")


METRICS_CMD = (
    "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits; "
    "free -b | awk '/^Mem:/{print $2, $7}'; "
    "df -B1 --output=size,used /content | tail -1; "
    "cat /content/packs/progress.json 2>/dev/null || true"
)


async def metrics_loop():
    """GPU açıkken GPU, VRAM, RAM ve disk kullanımını tek SSH bağlantısı üzerinden okur."""
    while True:
        if state["phase"] == "ready":
            try:
                out = (await run(*SSH, "colab", METRICS_CMD, timeout=15, quiet=True)).split("\n")
                util, vram_used, vram_total, temp = (float(x) for x in out[0].split(","))
                ram_total, ram_avail = (int(x) for x in out[1].split())
                disk_total, disk_used = (int(x) for x in out[2].split())
                update_packs("\n".join(out[3:]))
                metrics.append({
                    "t": time.time(),
                    "gpu": util,
                    "temp": temp,
                    "vram_used": vram_used * 2**20,
                    "vram_total": vram_total * 2**20,
                    "ram_used": ram_total - ram_avail,
                    "ram_total": ram_total,
                    "disk_used": disk_used,
                    "disk_total": disk_total,
                })
            except (RuntimeError, ValueError, IndexError) as e:
                log(f"sistem bilgisi okunamadı: {e}")
        await asyncio.sleep(METRICS_SECONDS)


def startup_progress():
    s = state["startup"]
    if s is None:
        return None
    exp = expected_durations()
    now = time.time()
    done = sum(exp[key] for key, _, _ in STEPS[: s["step"]])
    current = exp[STEPS[s["step"]][0]]
    # Adım beklenenden uzun sürerse çubuk %95'te bekler; kalan süre en az birkaç saniye görünür.
    in_step = min(now - s["step_started_at"], current * 0.95)
    remaining_step = current - in_step
    # Kurulum adımında modeller inerken ComfyUI kurulur; hangisi geç biterse adım odur. Kalan süre
    # öğrenilmiş adım süresinden ve indirmenin kalanından büyük olanı.
    install = STEPS[s["step"]][0] == "install"
    download = pack_status(s.get("pack", "image")) if install else None
    if download and download["eta"]:
        remaining_step = max(remaining_step, download["eta"])
    later = sum(exp[key] for key, _, _ in STEPS[s["step"] + 1:])
    remaining = remaining_step + later
    # Çubuk geri gitmez (tahmin büyüse de).
    s["last"] = max(s.get("last", 0.0), min((done + in_step) / (done + in_step + remaining), 0.99))
    return {
        "progress": s["last"],
        "remaining_seconds": max(5, round(remaining)),
        "elapsed_seconds": round(now - s["started_at"]),
        "step": s["step"],
        "steps": [label for _, label, _ in STEPS],
        "step_elapsed_seconds": round(now - s["step_started_at"]),
        "download": download,
        "pack": s.get("pack", "image"),
        "setup": setup_phase["text"] if install else None,
    }


def snapshot():
    idle = None
    if state["phase"] == "ready" and state["last_busy"]:
        idle = int(time.time() - state["last_busy"])
    c = state["credits"]
    spent = None
    if state["start_balance"] is not None and c and "balance" in c:
        spent = state["start_balance"] - c["balance"]
    return {
        "phase": state["phase"],
        "detail": state["detail"],
        "auto_shutdown": state["auto_shutdown"],
        "idle_seconds": idle,
        "idle_minutes": IDLE_MINUTES,
        "credits": c,
        "session_spent": spent,
        "session_seconds": round(time.time() - state["ready_at"]) if state["ready_at"] else None,
        "startup": startup_progress(),
        "gpu": GPU,
        # Model paketleri ve anlık GPU kullanımı: işin gerçekten ilerlediği arayüzde görünsün.
        "packs": {name: {**pack_status(name), "label": PACK_LABELS.get(name, name)} for name in packs},
        "gpu_now": {k: metrics[-1][k] for k in ("t", "gpu", "vram_used", "vram_total")} if metrics and state["phase"] == "ready" else None,
    }
