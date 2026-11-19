"""Colab GPU runtime'ının yaşam döngüsü.

Runtime'ı açar ve kapatır, modelleri HuggingFace'ten indirtip ComfyUI'yi kurar ve bu sunucuya
yönlendirir (127.0.0.1:8188), boşta kalınca runtime'ı kapatır, kredi ve sistem kullanımını izler.

**Oturum başına durum.** Birden fazla Colab oturumu açılabildiği için runtime durumu artık bir
`SessionRuntime` nesnesinde tutulur (slot 0 = bugünkü varsayılan oturum). Davranış hâlâ bu dosyadaki
fonksiyonlarda; fonksiyonlar `session=None` ile varsayılan oturumda çalışır. Slot 0'ın alanları modül
seviyesindeki eski isimlere (`state`, `ready`, `warm`, `COMFY_URL`, …) delege eder: mevcut çağıranlar,
testler ve `monkeypatch` kullanan testler değişmeden çalışır.

Her oturum kendi portunu, SSH yapılandırmasını ve veri dosyalarını kullanır; uzak komutlar oturumun
kendi `Host` adıyla (`colab`, `colab-2`, …) çalışır. Ad karışırsa ssh komutu yanlış VM'de çalışır ya
da "Could not resolve hostname" ile düşer (bkz. docs/multi-session.md).
"""
import asyncio
import json
import os
import re
import shlex
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
# "colab new" sürerken yazılır: açılıştan önce açık olan runtime'lar. colab new, runtime'ı aldıktan sonra
# ama oturumu adlandırmadan ölürse (iptal, zaman aşımı, servis öldürüldü) runtime "[?]" adıyla sahipsiz
# kalır; bu listede olmayan "[?]" runtime'lar o yarım açılışındır ve reap_owned onları bırakır.
PENDING_FILE = DATA_DIR / "start_pending.json"
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


def _ssh_token_safe(text):
    """ssh_config genişletir "%x" dizilerini (%h, %p, ...) ProxyCommand ve IdentityFile değerlerinde: yolda
    kalan "%" çift yazılır."""
    return str(text).replace("%", "%%")


def ssh_conf_text(host_alias="colab", name=None, ssh_conf=None):
    """Bir oturumun SSH yapılandırması.

    host_alias: `Host` adı (slot 0: `colab`, yeni oturumlar: `colab-2`, …). Uzak komutlar bu adla
    çalışır; ad yapılandırmayla eşleşmezse ssh "Could not resolve hostname" ile düşer.
    name: ProxyCommand'in bağlanacağı Colab oturumu.
    ssh_conf: metnin yazıldığı dosya (çağıranlar yolu da versin diye duruyor). Ana bağlantı soketi
    dosya adına bağlı değil: `ControlPath …/%C` ana makine adına göre özetlendiği için iki oturum
    aynı soketi paylaşamaz — paylaşsalardı komutlar hata vermeden **yanlış VM'de** çalışırdı.

    Yollar boşluk içerebilir (ör. "~/My Projects/colab-studio"): ProxyCommand satırın geri kalanını
    kabuğa verir (kabuk tırnağı), IdentityFile ise ssh'ın kendi çift tırnağını ister."""
    host = "colab" if host_alias is None else host_alias
    session_name = SESSION if name is None else name
    proxy = " ".join(shlex.quote(_ssh_token_safe(x)) for x in (
        "sh", Path(__file__).parent / "colab_proxy.sh", session_name, COLAB_SESSIONS, COLAB, SSH_KEY))
    identity = _ssh_token_safe(SSH_KEY).replace("\\", "\\\\").replace('"', '\\"')
    return f"""Host {host}
  ProxyCommand {proxy}
  User root
  IdentityFile "{identity}"
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

# GPU durum makinesi: hangi fazdan hangi fazlara geçilebilir. set_phase izinsiz bir geçişi kayda
# düşer (ama uygular: GPU her durumda kapatılabilmeli); tests/test_phase_transitions.py aynı tabloyu
# kullanır, yeni bir geçiş bu tabloya bilinçli eklenir.
PHASE_TRANSITIONS = {
    "off": {"starting", "stopping", "error"},  # error: servis açılışında eksik kurulum ya da bırakılamayan runtime
    "starting": {"ready", "stopping", "error"},  # error: kurulum eksik; stopping: açılış hatası ya da kapatma
    "ready": {"stopping"},
    "stopping": {"off", "error", "stopping"},  # stopping→stopping: begin_stop, run_stop içinde bir kez daha çağrılır
    "error": {"starting", "stopping", "error"},  # yeni açılış, yeniden kapatma ya da kurulum hâlâ eksik
}

# --- varsayılan (slot 0) oturumun durumu: eski isimler korunuyor ---
state = {
    "phase": "off",  # PHASE_TRANSITIONS'taki fazlardan biri
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
# Servis açılışındaki temizlik (sahipsiz runtime'ı bırakma) başarısız olduysa True: watchdog yeniden dener.
startup_cleanup_failed = False
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


class _Shared:
    """Slot 0 alanını modül seviyesindeki eski isme bağlar.

    Mevcut kod ve testler `runtime.state`, `runtime.ready`, `runtime.warming` gibi isimleri doğrudan
    okuyup yazıyor (bazıları yeniden atıyor: `runtime.ready = asyncio.Event()`). Slot 0 için bu isimler
    aynen kalmalı; yeni oturumlar ise kendi alanlarını tutmalı. Bu tanımlayıcı ikisini tek yerden
    yönetir."""

    def __init__(self, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None or obj.slot == 0:
            return globals()[self.name]
        return obj.__dict__[self.name]

    def __set__(self, obj, value):
        if obj.slot == 0:
            globals()[self.name] = value
        else:
            obj.__dict__[self.name] = value


class SessionRuntime:
    """Bir Colab oturumunun durumu ve bağlantı bilgileri.

    Slot 0 varsayılan oturumdur: `state`, `ready`, `warm`, `COMFY_URL` gibi alanlar modül
    seviyesindeki eski isimlere delege edilir. Yeni oturumlar (slot ≥ 1) kendi alanlarını tutar; kendi
    portunu, SSH yapılandırmasını ve veri dosyalarını kullanır."""

    state = _Shared("state")
    logs = _Shared("logs")
    metrics = _Shared("metrics")
    ready = _Shared("ready")
    local_modes = _Shared("local_modes")
    warm = _Shared("warm")
    warming = _Shared("warming")
    packs = _Shared("packs")
    _pack_samples = _Shared("_pack_samples")
    setup_phase = _Shared("setup_phase")
    _start_task = _Shared("_start_task")
    _has_pending = _Shared("_has_pending")
    _durations_warned = _Shared("_durations_warned")
    comfy_port = _Shared("COMFY_PORT")
    comfy_url = _Shared("COMFY_URL")
    ssh_conf = _Shared("SSH_CONF")
    durations_file = _Shared("DURATIONS_FILE")
    owned_file = _Shared("OWNED_FILE")
    pending_file = _Shared("PENDING_FILE")

    def __init__(self, slot=0, name=None, host_alias="colab", data_dir=None, comfy_port=None, ssh_conf=None):
        self.slot = slot
        self.name = SESSION if name is None else name
        self.host_alias = host_alias
        self.data_dir = Path(DATA_DIR if data_dir is None else data_dir)
        if slot != 0:  # slot 0 alanları modül globalinde yaşıyor (yukarıdaki tanımlayıcılar)
            self.state = {"phase": "off", "detail": "", "auto_shutdown": True, "last_busy": None,
                          "start_balance": None, "ready_at": None, "credits": None, "startup": None}
            self.logs = deque(maxlen=300)
            self.metrics = deque(maxlen=METRICS_HISTORY)
            self.ready = asyncio.Event()
            self.local_modes = set()
            self.warm = set()
            self.warming = False
            self.packs = {}
            self._pack_samples = []
            self.setup_phase = {"text": None}
            self._start_task = None
            self._has_pending = lambda: False
            self._durations_warned = False
            self.comfy_port = COMFY_PORT if comfy_port is None else comfy_port
            # Yerel port VDS'te, VM içindeki port sabit (8188): tünel 8200 → 8188 gibi.
            self.comfy_url = f"http://127.0.0.1:{self.comfy_port}"
            self.ssh_conf = Path(ssh_conf) if ssh_conf is not None else SSH_DIR / f"config.{slot}"
            # Oturumun süre/endpoint dosyaları buraya yazılır; dizin yoksa ilk yazma FileNotFoundError
            # ile düşüyordu (kurulumda oluşturulmasına güvenilmiyor).
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.durations_file = self.data_dir / "startup_durations.json"
            self.owned_file = self.data_dir / "owned_endpoints.json"
            self.pending_file = self.data_dir / "start_pending.json"

    def __repr__(self):
        return f"SessionRuntime(slot={self.slot}, name={self.name!r})"

    # Boşta kapatmayı kim yürütecek? Varsayılan oturumda modül fonksiyonu, yeni oturumlarda yönetici
    # (kayıttan slotu da düşürmesi gerekiyor). None: `stop()` yolu kullanılır.
    stop_hook = None

    @property
    def ssh(self):
        """Bu oturumun SSH komut öneki (kendi yapılandırma dosyası ve ana bağlantısı)."""
        return ["ssh", "-F", str(self.ssh_conf)]

    @property
    def remote(self):
        """Uzak komut öneki: `ssh -F <conf> <alias>`. Yapılandırmadaki `Host` adıyla eşleşmeli;
        slot 0'da `colab`, yeni oturumlarda `colab-2`, `colab-3`…"""
        return [*self.ssh, self.host_alias]

    # --- kısayollar: davranış modül fonksiyonlarında, oturum parametresi kendisi ---
    def start(self, pack="image", packs=None):
        return start(pack, self, packs)

    async def stop(self, reason):
        return await _call_default(stop, self, reason)

    async def stop_runtime(self):
        return await _stop_runtime_of(self)

    def begin_stop(self, reason):
        return begin_stop(reason, self)

    async def run_stop(self, reason):
        return await run_stop(reason, self)

    def set_phase(self, phase, detail=""):
        return _call_default(set_phase, self, phase, detail)

    async def forward(self):
        return await forward_comfy(self)

    async def connect(self):
        return await connect(self)

    async def wait_comfy_ready(self):
        return await wait_comfy_ready(self)

    async def ensure_models(self, pack, on_wait=None):
        return await ensure_models(pack, on_wait, self)

    async def reap_owned(self):
        return await _call_default(reap_owned, self)

    async def warmup(self):
        return await warmup(self)

    async def watchdog(self, has_pending_jobs, on_credits):
        return await watchdog(has_pending_jobs, on_credits, self)

    async def metrics_loop(self):
        return await metrics_loop(self)

    def snapshot(self):
        return snapshot(self)

    def startup_progress(self):
        return startup_progress(self)

    def touch(self):
        return touch(self)

    def toggle_auto(self):
        return toggle_auto(self)


DEFAULT = SessionRuntime()


def _sel(session):
    """Oturum verilmezse varsayılan (slot 0) oturum."""
    return DEFAULT if session is None else session


def _call_default(fn, s, *args):
    """Varsayılan oturumda modül fonksiyonu **eski imzayla** çağrılır.

    Testler `runtime.set_phase`, `runtime.stop_runtime`, `runtime.reap_owned`, `runtime._start`,
    `runtime._own`, `runtime.pack_state` gibi isimleri oturum argümanı almayan sahtelerle
    değiştiriyor; varsayılan oturumda bu sözleşme korunmalı. Yeni oturumlarda ise fonksiyon kendi
    oturumunu parametre olarak alır."""
    return fn(*args) if s is DEFAULT else fn(*args, s)


def _set_phase(s, phase, detail=""):
    """set_phase, varsayılan oturumda eski imzayla çağrılır (testler onu yamalıyor)."""
    return _call_default(set_phase, s, phase, detail)


def log(msg, session=None):
    s = _sel(session)
    s.logs.append(f"{time.strftime('%H:%M:%S')} {msg}")


def short_error(e, limit=700):
    """Hata metnini kısaltır: komut çıktısında asıl hata en sondadır, ilk satır ise neyin
    başarısız olduğunu söyler; ikisi de kalır."""
    text = str(e) or type(e).__name__
    if len(text) <= limit:
        return text
    head = text.split("\n", 1)[0][:150]
    return f"{head}\n…\n{text[-(limit - len(head) - 3):]}"


def set_phase(phase, detail="", session=None):
    s = _sel(session)
    if phase not in PHASE_TRANSITIONS.get(s.state["phase"], ()):
        log(f"beklenmeyen faz geçişi: {s.state['phase']} → {phase}", s)
    s.state["phase"] = phase
    s.state["detail"] = detail
    if phase == "ready":
        s.ready.set()
    else:
        s.ready.clear()
    if phase != "starting":
        s.state["startup"] = None
    log(f"[{phase}] {detail}", s)


_durations_warned = False


def expected_durations(session=None):
    """Öğrenilmiş açılış adımı süreleri. Dosya bozuksa (yarım yazma, elle düzenleme, disk hatası)
    varsayılanlara düşülür: bozuk dosya arayüzün tamamını 500'e düşürmemeli (_learn atomik yazar)."""
    s = _sel(session)
    learned = {}
    try:
        data = json.loads(s.durations_file.read_text())
        if isinstance(data, dict):
            learned = {k: v for k, v in data.items() if isinstance(v, (int, float))}
    except FileNotFoundError:
        pass  # ilk açılış: henüz ölçülmüş süre yok, varsayılanlar normaldir
    except (OSError, ValueError) as e:
        # Her durum yoklamasında çağrılır: bozuk dosya için tek satır yazılır, yoksa günlük (300 satır) dolar
        # ve açılışın asıl satırları silinirdi.
        if not s._durations_warned:
            s._durations_warned = True
            log(f"açılış süreleri okunamadı, varsayılanlar kullanılıyor: {short_error(e, 200)}", s)
    return {key: learned.get(key, default) for key, _, default in STEPS}


def _learn(i, session=None):
    """Adımın ölçülen süresini öğrenir (yeni ölçüm %50 ağırlıklı). Dosya atomik yazılır: yazma
    sırasında bir çökme yarım JSON bırakmasın."""
    s = _sel(session)
    key = STEPS[i][0]
    learned = expected_durations(s)
    learned[key] = round(0.5 * learned[key] + 0.5 * (time.time() - s.state["startup"]["step_started_at"]), 1)
    tmp = s.durations_file.with_suffix(".json.tmp")
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)  # oturum dizini silinmişse yazma düşmesin
        tmp.write_text(json.dumps(learned))
        os.replace(tmp, s.durations_file)
    except OSError as e:
        log(f"açılış süresi kaydedilemedi: {short_error(e, 200)}", s)


def _begin_step(i, session=None):
    s = _sel(session)
    if i > 0:
        _learn(i - 1, s)
    s.state["startup"]["step"] = i
    s.state["startup"]["step_started_at"] = time.time()
    s.state["detail"] = STEPS[i][1]
    log(f"[starting] {STEPS[i][1]}", s)


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


async def _start(session=None):
    s = _sel(session)
    try:
        _begin_step(0, s)
        try:
            _write_json(s.pending_file, _endpoints(await run(COLAB, "sessions", timeout=60, quiet=True)))
        except (RuntimeError, OSError) as e:
            # Önceki runtime'lar bilinmeden sahipsiz runtime ayırt edilemez; açılış yine sürer.
            log(f"açık runtime'lar okunamadı, yarım açılış izlenmeyecek: {short_error(e, 200)}", s)
        await run(COLAB, "new", "--gpu", GPU, "-s", s.name, timeout=600)
        _call_default(_own, s, json.loads(COLAB_SESSIONS.read_text())[s.name]["endpoint"])
        s.pending_file.unlink(missing_ok=True)
        s.state["start_balance"] = parse_usage(await run(COLAB, "usage", timeout=60))["balance"]

        _begin_step(1, s)
        await ensure_ssh_key()
        # Yapılandırma oturumun kendi dosyasına yazılır: içindeki `Host` adı uzak komutların adıyla
        # aynı olmalı (colab, colab-2, …).
        s.ssh_conf.parent.mkdir(parents=True, exist_ok=True)
        s.ssh_conf.write_text(ssh_conf_text(s.host_alias, s.name, s.ssh_conf))
        await connect(s)

        _begin_step(2, s)
        if config.HF_TOKEN:
            await run(*s.remote, "install -D -m 600 /dev/stdin /root/.cache/huggingface/token",
                      timeout=30, stdin_data=config.HF_TOKEN.encode())
        else:
            log("HF_TOKEN yok: modeller girişsiz iner (Hugging Face hız sınırına takılabilir)", s)
        await run(f"tar -C {shlex.quote(str(REPO_DIR))} -cz --exclude=__pycache__ station | "
                  f"ssh -F {shlex.quote(str(s.ssh_conf))} {shlex.quote(s.host_alias)} 'tar -C /content -xz'", timeout=120, shell=True)
        # Model kataloğu (models.toml): station/models.py ve iş akışları aynı dosyayı kullanır.
        await run(*s.remote, "install -D -m 644 /dev/stdin /content/station/models.toml",
                  timeout=30, stdin_data=config.MODELS_FILE.read_bytes())
        poller = asyncio.create_task(poll_packs(s))
        try:
            # İkinci argüman: yalnız bu paketler insin (boşsa hepsi). Küçük/tek amaçlı oturumlar için.
            profile = " ".join(s.state["startup"].get("packs") or []) or "all"
            await run(*s.remote, f"bash /content/station/runtime_setup.sh "
                                 f"{s.state['startup']['pack'] or 'none'} {shlex.quote(profile)}", timeout=1800)
        finally:
            poller.cancel()

        _begin_step(3, s)
        await forward_comfy(s)
        await wait_comfy_ready(s)
        _learn(len(STEPS) - 1, s)
        if s.state["startup"]["pack"]:
            s.local_modes.add(s.state["startup"]["pack"])
        s.state["last_busy"] = time.time()
        s.state["ready_at"] = time.time()
        _set_phase(s, "ready", "GPU hazır")
        asyncio.create_task(warmup(s))
    except Exception as e:
        # Önce kapat, sonra hata göster: hata durumunda yeni bir açılış başlayabilir ve kapanış
        # onun oturumunu öldürmemeli.
        _set_phase(s, "stopping", "açılış başarısız, kapatılıyor")
        try:
            await _stop_runtime_of(s)
        except Exception as stop_error:  # ne olursa olsun "stopping"de takılı kalmasın
            log(short_error(stop_error), s)  # bırakılamayan runtime'ı watchdog yeniden dener
        _set_phase(s, "error", short_error(e))


async def forward_comfy(session=None):
    """ComfyUI portunu SSH ana bağlantısı üzerinden sunucuya yönlendirir (VM içinde port 8188)."""
    s = _sel(session)
    await run(*s.ssh, "-O", "forward", "-L", f"127.0.0.1:{s.comfy_port}:127.0.0.1:{COMFY_PORT}",
              s.host_alias, timeout=30)


async def connect(session=None, attempts=3, delay=5):
    """SSH ana bağlantısını kurar. Yeni runtime'da Colab'ın SSH köprüsü ara sıra mesaj vermeden
    kapanıyor (aynı runtime'a hemen ardından bağlanılabiliyor); birkaç kez denenir."""
    s = _sel(session)
    for attempt in range(1, attempts + 1):
        try:
            return await run(*s.ssh, "-fN", s.host_alias, timeout=90)
        except RuntimeError:
            if attempt == attempts:
                raise
            log(f"SSH bağlantısı kurulamadı, yeniden deneniyor ({attempt}/{attempts - 1})", s)
            await asyncio.sleep(delay)


def start(pack="image", session=None, packs=None):
    """Runtime'ı arka planda açar; zaten açılıyorsa ya da açıksa bir şey yapmaz. pack: açılışı
    tetikleyen işin model paketi; ilk o iner ve hazır sayılmak için yalnızca o beklenir. None: işin
    indirilecek paketi yok (yazıya dökme; Whisper'ı eklenti kendisi indirir), hiçbir paket beklenmez.
    packs: yalnız bu paketler insin (None: hepsi)."""
    global startup_cleanup_failed
    s = _sel(session)
    if s.state["phase"] in ("off", "error"):
        if s is DEFAULT:
            startup_cleanup_failed = False  # açılış kendi runtime'ını kurar; eski hata artık geçersiz
        # Kurulum eksikse (colab CLI ya da Google girişi yok) hata hemen döner: başlatan istek (arayüz,
        # MCP) "açılıyor" yerine ne yapılacağını görür.
        problem = preflight()
        if problem:
            _set_phase(s, "error", problem)
            return
        _set_phase(s, "starting", STEPS[0][1])
        now = time.time()
        s.state["startup"] = {"step": 0, "started_at": now, "step_started_at": now,
                              "pack": pack if pack in PACKS or pack is None else "image",
                              "packs": list(packs) if packs else None}
        s._start_task = asyncio.create_task(_call_default(_start, s))


# ComfyUI, kurulum betiğinin venv'indeki python ile başlar ("/content/venv/bin/python main.py ..."). Köşeli
# parantezli "main[.]py": pgrep'i çalıştıran kabuğun kendi komut satırı desene uymaz.
COMFY_PGREP = "(^|/)python[0-9.]* main[.]py"
COMFY_PROCESS = f"pgrep -f '{COMFY_PGREP}' >/dev/null && echo alive || echo dead"


async def wait_comfy_ready(session=None):
    """ComfyUI /queue'ya yanıt verene kadar bekler. Süreç ölmüşse (kurulum hatası, çökme) 10 dakika
    beklemeden comfy.log'un sonuyla hemen hata verir."""
    s = _sel(session)
    deadline = time.time() + 600
    async with ClientSession(timeout=ClientTimeout(total=5)) as http:
        while time.time() < deadline:
            try:
                async with http.get(f"{s.comfy_url}/queue") as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            if (await run(*s.remote, COMFY_PROCESS, timeout=20, quiet=True)).strip() != "alive":
                tail = await run(*s.remote, "tail -n 40 /content/comfy.log", timeout=30, quiet=True)
                raise RuntimeError(f"ComfyUI süreci öldü:\n{tail}")
            await asyncio.sleep(3)
    tail = await run(*s.remote, "tail -n 40 /content/comfy.log", timeout=30)
    raise RuntimeError(f"ComfyUI açılmadı:\n{tail}")


WARMUP_POLL_SECONDS = 10
WARMUP_DEADLINE_SECONDS = 1800  # görsel paketi bu süre içinde inmezse ön ısıtma bırakılır


async def pack_state(pack, session=None):
    """"done" | "failed" | "pending": paketin inme durumu tek ssh çağrısında okunur."""
    s = _sel(session)
    out = await run(
        *s.remote,
        f"if [ -f /content/packs/{pack}.done ]; then echo done; "
        f"elif [ -f /content/packs/{pack}.error ]; then echo failed; else echo pending; fi",
        timeout=30, quiet=True,
    )
    return out.strip() or "pending"


async def warmup(session=None):
    """Görsel modellerini (UNET, CLIP, VAE) arka planda GPU'ya yükler (~55 sn). Kuyrukta iş yokken ve
    görsel paketi inmişken çalışır (GPU'yu başka bir iş açtırdıysa paket sonradan iner; o zaman beklenir).
    Kullanıcı iş gönderirse hemen kesilir ve iş bitince yeniden denenir; boşta sayacını uzatmaz.
    Paket inemezse ya da WARMUP_DEADLINE_SECONDS içinde gelmezse vazgeçer: sonsuz yoklamada her turda
    ssh açıp durmaz."""
    s = _sel(session)
    deadline = time.time() + WARMUP_DEADLINE_SECONDS
    while s.state["phase"] == "ready" and "image" not in s.warm:
        try:
            if not s._has_pending():
                if "image" not in s.local_modes:
                    pack = await _call_default(pack_state, s, "image")
                    if pack == "failed":
                        log("görsel paketi inemedi, ön ısıtma yapılmıyor", s)
                        return
                    if pack != "done":
                        if time.time() > deadline:
                            log("görsel paketi beklendi, gelmedi; ön ısıtma bırakıldı", s)
                            return
                        await asyncio.sleep(WARMUP_POLL_SECONDS)
                        continue
                    s.local_modes.add("image")
                if await _warm_image(s) != "interrupted":
                    return
        except RuntimeError as e:
            log(f"ön ısıtma atlandı: {e}", s)
            return
        await asyncio.sleep(WARMUP_POLL_SECONDS)


async def _warm_image(session=None):
    """"done" | "interrupted" (kullanıcı işi geldi) | "failed"."""
    s = _sel(session)
    import workflows
    s.warming = True
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as http:
            async with http.post(f"{s.comfy_url}/prompt", json={"prompt": workflows.image_warmup()}) as r:
                if r.status != 200:
                    return "failed"
                pid = (await r.json())["prompt_id"]
            while s.state["phase"] == "ready":
                if s._has_pending():
                    await http.post(f"{s.comfy_url}/interrupt")
                    return "interrupted"
                async with http.get(f"{s.comfy_url}/history/{pid}") as r:
                    h = await r.json()
                if pid in h:
                    if h[pid].get("status", {}).get("completed"):
                        s.warm.add("image")
                        log("görsel modeli önceden yüklendi", s)
                        return "done"
                    return "failed"
                await asyncio.sleep(0.5)
            return "failed"
    except Exception as e:
        log(f"ön ısıtma atlandı: {e}", s)
        return "failed"
    finally:
        s.warming = False


def _write_json(path, value):
    """Atomik yazar: yazma sırasında çökme, güç kesintisi ya da dolu disk yarım JSON bırakmasın."""
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)  # oturumun veri dizini yoksa ilk yazma düşmesin
    tmp.write_text(json.dumps(value))
    os.replace(tmp, path)


def _read_list(path):
    """Okunamayan ya da bozuk dosya boş liste sayılır: kapanış ve servis açılışı bir dosya yüzünden
    durmamalı (durursa GPU "stopping"de kalır, servis hiç açılmaz)."""
    try:
        value = json.loads(path.read_text()) if path.exists() else []
    except (OSError, ValueError) as e:
        log(f"{path.name} okunamadı, boş sayılıyor: {short_error(e, 200)}")
        return []
    return [e for e in value if isinstance(e, str)] if isinstance(value, list) else []


def _owned(session=None):
    s = _sel(session)
    return _read_list(s.owned_file)


def _own(endpoint, session=None):
    s = _sel(session)
    s.owned_file.parent.mkdir(parents=True, exist_ok=True)
    _write_json(s.owned_file, _owned(s) + [endpoint])


_SESSION_LINE = re.compile(r"^\[(.*?)\] (\S+)", re.M)


def _endpoints(sessions_text, name=None):
    """`colab sessions` çıktısındaki runtime'lar ("[ad] endpoint | ..."); name verilirse yalnızca o adla."""
    return [e for n, e in _SESSION_LINE.findall(sessions_text) if name is None or n == name]


async def reap_owned(session=None):
    """Stüdyonun açtığı ama hâlâ açık kalan runtime'ları bırakır (ör. yarım kalmış bir açılıştan
    artakalan, artık "comfy" adını taşımayan oturum). Yalnızca kendi açtıklarına dokunur."""
    s = _sel(session)
    owned = _owned(s)
    pending = s.pending_file.exists()
    if not owned and not pending:
        return
    sessions = await run(COLAB, "sessions", timeout=60, quiet=True)
    alive = [e for e in owned if e in sessions]
    # Yarım kalmış açılışın adsız runtime'ı. Açılış hâlâ sürüyorsa (bu tur beklerken başladıysa) onun
    # dosyasıdır: dokunulmaz, açılış kendisi tamamlar ya da kapanışta bırakır.
    if pending and s.state["phase"] != "starting":
        before = set(_read_list(s.pending_file))
        alive += [e for e in _endpoints(sessions, "?") if e not in before and e not in alive]
    for e in alive:
        log(f"stüdyonun açık kalan runtime'ı kapatılıyor: {e}", s)
        await run(config.colab_python(), "-c", f"from colab_cli.common import state; state.client.unassign({e!r})", timeout=60)
    # Bu arada yeni bir açılış kendi runtime'ını eklemiş olabilir; yalnızca incelenenler silinir.
    _write_json(s.owned_file, [e for e in _owned(s) if e not in owned])
    if pending and s.state["phase"] != "starting":
        s.pending_file.unlink(missing_ok=True)


async def _stop_runtime_of(s):
    """Oturumun runtime'ını kapatır. Varsayılan oturumda modül fonksiyonu çağrılır: testler
    `runtime.stop_runtime` adını yamalıyor."""
    if s is DEFAULT:
        return await globals()["stop_runtime"]()
    return await stop_runtime(s)


async def stop_runtime(session=None):
    """Runtime'ı kapatır; kredi harcamasın diye bir adım başarısız olsa da diğerlerini dener, yerel
    durumu her durumda temizler. Bir adım başarısızsa sonunda RuntimeError verir; stüdyonun açtığı
    runtime bırakılamadıysa watchdog onu reap_owned ile yeniden dener."""
    s = _sel(session)
    errors = []
    if s.ssh_conf.exists():
        try:
            await run(*s.ssh, "-O", "exit", s.host_alias, timeout=20)
        except RuntimeError as e:
            log(str(e), s)  # ana bağlantı zaten yoksa olağan
    try:
        sessions = await run(COLAB, "sessions", timeout=60)
        if f"[{s.name}]" in sessions:
            await run(COLAB, "stop", "-s", s.name, timeout=120)
    except RuntimeError as e:
        errors.append(e)
    try:
        await _call_default(reap_owned, s)
    except Exception as e:  # yerel durum her durumda temizlensin
        errors.append(e)
    s.state["last_busy"] = None
    s.state["start_balance"] = None
    s.state["ready_at"] = None
    s.metrics.clear()
    s.local_modes.clear()
    s.warm.clear()
    s.packs.clear()
    s._pack_samples.clear()
    s.setup_phase["text"] = None
    s.warming = False
    if errors:
        raise RuntimeError(f"GPU kapatılamadı, yeniden denenecek: {errors[-1]}")


def update_packs(text, session=None):
    """Runtime'dan okunan progress.json'u işler; dosya henüz yoksa (indirme başlamadı) boş gelir."""
    s = _sel(session)
    if not text.strip():
        return
    s.packs.clear()
    s.packs.update(json.loads(text))
    sample = (time.time(), sum(p["done"] for p in s.packs.values()))
    if s._pack_samples:
        s._pack_samples[1:] = [sample]
    else:
        s._pack_samples.append(sample)


def download_rate(session=None):
    s = _sel(session)
    if len(s._pack_samples) < 2:
        return None
    (t0, b0), (t1, b1) = s._pack_samples
    return (b1 - b0) / (t1 - t0) if t1 > t0 and b1 > b0 else None


def pack_status(pack, session=None):
    """Paketin inme durumu: oran, bayt, hız ve kalan süre (paketler sırayla iner; öndekiler de beklenir)."""
    s = _sel(session)
    p = s.packs.get(pack)
    if p is None:
        return None
    rate = download_rate(s)
    ahead = 0
    for name, q in s.packs.items():  # sırayla iner: bu paketten öncekilerin kalanı da beklenir
        ahead += q["total"] - q["done"]
        if name == pack:
            break
    return {
        "total": p["total"], "done": p["done"], "fraction": p["done"] / p["total"] if p["total"] else 1.0,
        "rate": rate, "eta": round(ahead / rate) if rate and ahead else (0 if not ahead else None),
    }


async def poll_packs(session=None):
    """Açılışta kurulum betiği sürerken indirme ilerlemesini ve kurulum aşamasını okur (gösterim için)."""
    s = _sel(session)
    while True:
        try:
            out = await run(*s.remote, "cat /content/packs/progress.json 2>/dev/null; echo; cat /content/setup_phase 2>/dev/null; true",
                            timeout=15, quiet=True)
            lines = out.split("\n")
            update_packs(lines[0], s)
            s.setup_phase["text"] = (lines[1].strip() if len(lines) > 1 else "") or None
        except RuntimeError:
            pass  # bağlantı anlık koptuysa bir sonraki okumada güncellenir
        await asyncio.sleep(3)


async def ensure_models(pack, on_wait=None, session=None):
    """İş türünün model paketi runtime'da inene kadar bekler; beklerken on_wait(pack_status) çağrılır
    (on_wait hata fırlatırsa, ör. iş iptal edildiyse, bekleme biter). Paket inmiyorsa indirme sırası
    bu pakete verilir (models.py, dosya sınırında ona geçer). İndirici öldüyse ya da MODEL_STALL_SECONDS
    boyunca inen bayt artmadıysa hata verilir: yoksa iş sonsuza kadar "çalışıyor" kalır ve GPU kapanmaz."""
    s = _sel(session)
    if pack not in s.local_modes:
        await run(*s.remote, f"mkdir -p /content/packs && echo {pack} > /content/packs/priority", timeout=30, quiet=True)
    last_bytes, last_moved = None, time.time()
    while pack not in s.local_modes:
        if on_wait:
            on_wait(pack_status(pack, s))
        total_bytes = sum(p["done"] for p in s.packs.values())
        if total_bytes != last_bytes:
            last_bytes, last_moved = total_bytes, time.time()
        elif time.time() - last_moved > MODEL_STALL_SECONDS:
            raise RuntimeError(f"{PACK_LABELS.get(pack, pack)} modelleri {MODEL_STALL_SECONDS // 60} dakikadır inmiyor, indirme takıldı")
        out = await run(
            *s.remote,
            f"if [ -f /content/packs/{pack}.done ]; then echo done; "
            f"elif [ -f /content/packs/{pack}.error ]; then tail -3 /content/packs/{pack}.error; exit 1; "
            # [.] kalıbın bu kabuğun kendi komut satırıyla eşleşmesini önler
            f"elif ! pgrep -f 'station/models[.]py' >/dev/null && [ ! -f /content/packs/{pack}.done ]; then "
            f"echo 'model indirici durdu:'; tail -5 /content/models.log; exit 1; fi",
            timeout=30, quiet=True,
        )
        if out.strip() == "done":
            s.local_modes.add(pack)
        else:
            await asyncio.sleep(MODEL_POLL_SECONDS)


def begin_stop(reason, session=None):
    """Kapanışı senkron başlatır: açılış sürüyorsa iptal eder ve fazı hemen "stopping" yapar. İsteği
    bekletmemek için (nginx 120 sn) kapanışın kendisi run_stop ile arka planda yürütülür."""
    s = _sel(session)
    if s._start_task and not s._start_task.done():
        s._start_task.cancel()
    _set_phase(s, "stopping", reason)


async def run_stop(reason, session=None):
    """Kapanışı yürütür; begin_stop ile aynı işi bir kez daha (zararsız) yapar ki tek başına da
    kullanılabilsin."""
    s = _sel(session)
    begin_stop(reason, s)
    try:
        await _stop_runtime_of(s)
    except Exception as e:  # beklenmedik hata da "stopping"de bırakmasın
        _set_phase(s, "error", short_error(e))
        return
    _set_phase(s, "off", reason)


async def stop(reason, session=None):
    await run_stop(reason, session)


async def retry_startup_cleanup():
    """Servis ağ gelmeden açıldıysa (bilgisayar yeni açıldı, DNS henüz yok) açılıştaki temizlik
    başarısız olur ve arayüz kalıcı bir hata gösterir. Colab'a yeniden ulaşılınca temizlik tamamlanır
    ve hata kalkar. Ulaşılamıyorsa faz ve hata metni olduğu gibi kalır (her turda titremesin).

    Servis açılışının temizliğidir: yalnızca varsayılan oturuma aittir."""
    global startup_cleanup_failed
    await run(COLAB, "sessions", timeout=60, quiet=True)  # ulaşılamıyorsa RuntimeError: sonraki tur
    if not startup_cleanup_failed or state["phase"] != "error":
        return  # beklerken bir açılış başladı: onun oturumuna dokunulmaz
    # "stopping" fazı temizlik sürerken yeni bir açılışı bekletir (start yalnızca off/error'da başlar).
    set_phase("stopping", "")
    try:
        await globals()["stop_runtime"]()
    except Exception as e:
        set_phase("error", short_error(e))
        return
    startup_cleanup_failed = False
    set_phase("off", "")


def touch(session=None):
    """Stüdyo bir iş gönderdiğinde boşta sayacını sıfırlar."""
    s = _sel(session)
    if s.state["phase"] == "ready":
        s.state["last_busy"] = time.time()


def toggle_auto(session=None):
    s = _sel(session)
    s.state["auto_shutdown"] = not s.state["auto_shutdown"]
    touch(s)


def parse_usage(text):
    balance = re.search(r"Current balance:\s*([\d.]+)", text)
    rate = re.search(r"Usage rate:\s*([\d.]+)", text)
    if not balance or not rate:
        raise RuntimeError(f"colab usage çıktısı okunamadı: {text[-200:]}")
    return {"balance": float(balance.group(1)), "rate": float(rate.group(1))}


async def log_crash_context(session=None):
    """Runtime kapanmadan önce ComfyUI'nin son kaydını ve bellek durumunu kayda düşer."""
    s = _sel(session)
    try:
        await run(*s.remote, "pgrep -af main.py; free -g; tail -40 /content/comfy.log", timeout=20)
    except RuntimeError as e:
        log(f"çökme kaydı alınamadı: {e}", s)


async def watchdog(has_pending_jobs, on_credits, session=None):
    """Kredi, boşta kapatma ve ComfyUI çökmesini izler. Bir turdaki beklenmedik hata döngüyü
    durdurmaz: durursa boşta kapatma da durur ve GPU kredi harcamaya devam eder."""
    s = _sel(session)
    s._has_pending = has_pending_jobs
    async with ClientSession(timeout=ClientTimeout(total=10)) as http:
        while True:
            try:
                await _watch_once(http, has_pending_jobs, on_credits, s)
            except Exception as e:
                log(f"izleme turu başarısız, sonraki turda yeniden denenecek: {short_error(e, 300)}", s)
            await asyncio.sleep(POLL_SECONDS)


async def _watch_once(http, has_pending_jobs, on_credits, session=None):
    s = _sel(session)
    if not COLAB_TOKEN.exists():
        # Girişsizken `colab usage` her turda bir OAuth akışı başlatıp log'u şişirir; giriş yapılınca
        # sonraki tur krediyi yeniden okur.
        s.state["credits"] = {"error": "colab CLI'da Google girişi yok"}
    else:
        try:
            s.state["credits"] = parse_usage(await run(COLAB, "usage", timeout=60, quiet=True))
            on_credits(s.state["credits"])
        except RuntimeError as e:
            s.state["credits"] = {"error": str(e)[:200]}
    if s is DEFAULT and startup_cleanup_failed and s.state["phase"] == "error":
        await retry_startup_cleanup()
    if s.state["phase"] in ("off", "error"):
        await _call_default(reap_owned, s)
    if s.state["phase"] != "ready":
        return
    try:
        async with http.get(f"{s.comfy_url}/queue") as r:
            q = await r.json()
        # Yanıt beklenirken GPU elle kapatılmış olabilir: eski gözleme göre ikinci bir kapanış başlamasın.
        if s.state["phase"] != "ready":
            return
        if (q["queue_running"] and not s.warming) or q["queue_pending"] or has_pending_jobs():
            s.state["last_busy"] = time.time()
        elif s.state["auto_shutdown"] and time.time() - s.state["last_busy"] >= IDLE_MINUTES * 60:
            reason = f"{IDLE_MINUTES} dk boşta kaldı, GPU kapatıldı"
            if s.stop_hook:  # yeni oturumlarda kapanışı yönetici yürütür (kayıttan slotu da düşürür)
                await s.stop_hook(reason)
            else:
                await _call_default(stop, s, reason)
    except OSError as e:
        # Büyük model yüklerken ComfyUI saniyelerce yanıt vermeyebilir; süreç
        # yaşıyorsa meşgul sayılır, yalnızca süreç ya da bağlantı yoksa kapatılır.
        try:
            await run(*s.remote, f"pgrep -f '{COMFY_PGREP}'", timeout=20, quiet=True)
            s.state["last_busy"] = time.time()
            # Port kapalıysa (bağlantı reddedildi) SSH ana bağlantısı kopmuş, sonraki ssh
            # komutu yeni ana bağlantıyı yönlendirmesiz açmıştır: yönlendirme yeniden kurulur.
            if isinstance(e, ClientConnectorError) and isinstance(e.os_error, ConnectionRefusedError):
                log("ComfyUI portu kapalı (SSH bağlantısı yenilenmiş), yönlendirme yeniden kuruluyor", s)
                await forward_comfy(s)
        except RuntimeError:
            # Kapanış zaten sürüyorsa (elle kapatma ssh bağlantısını kesti) ComfyUI'ye ulaşılamaması
            # beklenir: ikinci bir kapanış ya da yanıltıcı "ulaşılamıyor" hatası olmasın.
            if s.state["phase"] != "ready":
                return
            _set_phase(s, "stopping", "ComfyUI'ye ulaşılamıyor, kapatılıyor")
            await log_crash_context(s)
            try:
                await _stop_runtime_of(s)
            except Exception as stop_error:  # "stopping"de takılı kalmasın
                log(short_error(stop_error), s)  # bırakılamayan runtime sonraki turda reap_owned ile denenir
            _set_phase(s, "error", f"ComfyUI'ye ulaşılamıyor, runtime kapatıldı: {e!r}")


METRICS_CMD = (
    "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits; "
    "free -b | awk '/^Mem:/{print $2, $7}'; "
    "df -B1 --output=size,used /content | tail -1; "
    "cat /content/packs/progress.json 2>/dev/null || true"
)


async def metrics_loop(session=None):
    """GPU açıkken GPU, VRAM, RAM ve disk kullanımını tek SSH bağlantısı üzerinden okur."""
    s = _sel(session)
    while True:
        if s.state["phase"] == "ready":
            try:
                out = (await run(*s.remote, METRICS_CMD, timeout=15, quiet=True)).split("\n")
                util, vram_used, vram_total, temp = (float(x) for x in out[0].split(","))
                ram_total, ram_avail = (int(x) for x in out[1].split())
                disk_total, disk_used = (int(x) for x in out[2].split())
                update_packs("\n".join(out[3:]), s)
                s.metrics.append({
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
                log(f"sistem bilgisi okunamadı: {e}", s)
        await asyncio.sleep(METRICS_SECONDS)


def startup_progress(session=None):
    s = _sel(session)
    st = s.state["startup"]
    if st is None:
        return None
    exp = expected_durations(s)
    now = time.time()
    done = sum(exp[key] for key, _, _ in STEPS[: st["step"]])
    current = exp[STEPS[st["step"]][0]]
    # Adım beklenenden uzun sürerse çubuk %95'te bekler; kalan süre en az birkaç saniye görünür.
    in_step = min(now - st["step_started_at"], current * 0.95)
    remaining_step = current - in_step
    # Kurulum adımında modeller inerken ComfyUI kurulur; hangisi geç biterse adım odur. Kalan süre
    # öğrenilmiş adım süresinden ve indirmenin kalanından büyük olanı.
    install = STEPS[st["step"]][0] == "install"
    download = pack_status(st.get("pack", "image"), s) if install else None
    if download and download["eta"]:
        remaining_step = max(remaining_step, download["eta"])
    later = sum(exp[key] for key, _, _ in STEPS[st["step"] + 1:])
    remaining = remaining_step + later
    # Çubuk geri gitmez (tahmin büyüse de).
    st["last"] = max(st.get("last", 0.0), min((done + in_step) / (done + in_step + remaining), 0.99))
    return {
        "progress": st["last"],
        "remaining_seconds": max(5, round(remaining)),
        "elapsed_seconds": round(now - st["started_at"]),
        "step": st["step"],
        "steps": [label for _, label, _ in STEPS],
        "step_elapsed_seconds": round(now - st["step_started_at"]),
        "download": download,
        "pack": st.get("pack", "image"),
        "setup": s.setup_phase["text"] if install else None,
    }


def snapshot(session=None):
    s = _sel(session)
    idle = None
    if s.state["phase"] == "ready" and s.state["last_busy"]:
        idle = int(time.time() - s.state["last_busy"])
    c = s.state["credits"]
    spent = None
    if s.state["start_balance"] is not None and c and "balance" in c:
        spent = s.state["start_balance"] - c["balance"]
    return {
        "phase": s.state["phase"],
        "detail": s.state["detail"],
        "auto_shutdown": s.state["auto_shutdown"],
        "idle_seconds": idle,
        "idle_minutes": IDLE_MINUTES,
        "credits": c,
        "session_spent": spent,
        "session_seconds": round(time.time() - s.state["ready_at"]) if s.state["ready_at"] else None,
        "startup": startup_progress(s),
        "gpu": GPU,
        # Model paketleri ve anlık GPU kullanımı: işin gerçekten ilerlediği arayüzde görünsün.
        "packs": {name: {**pack_status(name, s), "label": PACK_LABELS.get(name, name)} for name in s.packs},
        "gpu_now": {k: s.metrics[-1][k] for k in ("t", "gpu", "vram_used", "vram_total")} if s.metrics and s.state["phase"] == "ready" else None,
    }
