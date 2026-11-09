"""Boşta kapanma: GPU yalnızca ComfyUI ve kuyruk boşken, süre dolunca kapanır; ComfyUI yanıt
vermese de süreci yaşıyorsa (büyük model yüklüyor) kapatılmaz."""
import asyncio
import time

import pytest
from aiohttp import web

import runtime

USAGE = "Current balance: 100.0 compute units\nUsage rate: 1.5 per hour"


@pytest.fixture
async def watch(data_dir, aiohttp_server, monkeypatch):
    queue = {"queue_running": [], "queue_pending": []}
    calls, stops = [], []
    comfy_alive = {"process": True, "http": True}

    async def queue_handler(request):
        if not comfy_alive["http"]:
            await asyncio.sleep(1)  # istemci zaman aşımına düşsün
        return web.json_response(queue)

    app = web.Application()
    app.add_routes([web.get("/queue", queue_handler)])
    server = await aiohttp_server(app)

    comfy_url = str(server.make_url("")).rstrip("/")

    async def fake_run(*cmd, **kw):
        calls.append(" ".join(cmd))
        if "forward" in cmd:  # yönlendirme kurulunca port yeniden açılır
            monkeypatch.setattr(runtime, "COMFY_URL", comfy_url)
        if cmd[0] == runtime.COLAB and cmd[1] == "usage":
            return USAGE
        if "pgrep" in " ".join(cmd):
            if not comfy_alive["process"]:
                raise RuntimeError("komut başarısız (1)")
            return "1234"
        return ""

    async def fake_stop_runtime():
        stops.append(time.time())

    token = data_dir / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(runtime, "COLAB_TOKEN", token)  # giriş yapılmış say: kredi okunur
    monkeypatch.setattr(runtime, "COMFY_URL", comfy_url)
    monkeypatch.setattr(runtime, "run", fake_run)
    monkeypatch.setattr(runtime, "stop_runtime", fake_stop_runtime)
    monkeypatch.setattr(runtime, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(runtime, "ClientTimeout", lambda total: __import__("aiohttp").ClientTimeout(total=0.2))
    runtime.ready = asyncio.Event()
    runtime.state.update(auto_shutdown=True, credits=None, last_busy=time.time())
    runtime.set_phase("ready", "GPU hazır")
    pending = {"jobs": False}
    credits = []
    task = asyncio.create_task(runtime.watchdog(lambda: pending["jobs"], credits.append))
    yield {"queue": queue, "stops": stops, "calls": calls, "pending": pending, "credits": credits, "comfy": comfy_alive}
    task.cancel()
    runtime.set_phase("off")


async def test_idle_gpu_is_stopped(watch):
    runtime.state["last_busy"] = time.time() - runtime.IDLE_MINUTES * 60 - 1
    await asyncio.sleep(0.3)
    assert watch["stops"] and runtime.state["phase"] == "off"
    assert "boşta" in runtime.state["detail"]
    assert watch["credits"][0] == {"balance": 100.0, "rate": 1.5}


async def test_credits_are_not_polled_without_sign_in(watch, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "COLAB_TOKEN", tmp_path / "missing-token.json")
    await asyncio.sleep(0.2)
    calls = len([c for c in watch["calls"] if c.endswith(" usage")])
    await asyncio.sleep(0.2)
    assert len([c for c in watch["calls"] if c.endswith(" usage")]) == calls  # no OAuth attempt every round
    assert "girişi yok" in runtime.state["credits"]["error"]


async def test_pending_studio_jobs_keep_gpu_alive(watch):
    watch["pending"]["jobs"] = True
    runtime.state["last_busy"] = time.time() - runtime.IDLE_MINUTES * 60 - 1
    await asyncio.sleep(0.3)
    assert not watch["stops"] and runtime.state["phase"] == "ready"
    assert time.time() - runtime.state["last_busy"] < 5


async def test_running_comfy_prompt_keeps_gpu_alive(watch):
    watch["queue"]["queue_running"] = [["x"]]
    runtime.state["last_busy"] = time.time() - runtime.IDLE_MINUTES * 60 - 1
    await asyncio.sleep(0.3)
    assert not watch["stops"]



async def test_warming_comfy_prompt_does_not_delay_auto_shutdown(watch):
    watch["queue"]["queue_running"] = [["warmup"]]
    runtime.warming = True
    runtime.state["last_busy"] = time.time() - runtime.IDLE_MINUTES * 60 - 1
    try:
        await asyncio.sleep(0.3)
        assert watch["stops"] and runtime.state["phase"] == "off"
    finally:
        runtime.warming = False

async def test_auto_shutdown_off_keeps_gpu(watch):
    runtime.state["auto_shutdown"] = False
    runtime.state["last_busy"] = time.time() - runtime.IDLE_MINUTES * 60 - 1
    await asyncio.sleep(0.3)
    assert not watch["stops"]
    runtime.state["auto_shutdown"] = True


async def test_unresponsive_but_alive_comfy_is_busy(watch):
    watch["comfy"]["http"] = False
    runtime.state["last_busy"] = time.time() - runtime.IDLE_MINUTES * 60 - 1
    await asyncio.sleep(0.8)
    assert not watch["stops"] and runtime.state["phase"] == "ready"


async def test_dead_comfy_stops_runtime_with_error(watch):
    watch["comfy"]["http"] = False
    watch["comfy"]["process"] = False
    await asyncio.sleep(0.8)
    assert watch["stops"]
    assert runtime.state["phase"] == "error" and "ComfyUI'ye ulaşılamıyor" in runtime.state["detail"]


async def test_failed_stop_is_shown_and_watchdog_keeps_retrying(watch, monkeypatch):
    """Colab CLI kapatırken hata verirse izleme döngüsü ölmez (ölürse boşta kapatma da durur):
    hata görünür ve stüdyonun runtime'ı sonraki turlarda yeniden bırakılmaya çalışılır."""
    reaps = []

    async def failing_stop_runtime():
        watch["stops"].append(time.time())
        raise RuntimeError("GPU kapatılamadı, yeniden denenecek: colab sessions zaman aşımı")

    async def failing_reap():
        reaps.append(time.time())
        raise RuntimeError("colab sessions zaman aşımı")

    monkeypatch.setattr(runtime, "stop_runtime", failing_stop_runtime)
    monkeypatch.setattr(runtime, "reap_owned", failing_reap)
    runtime.state["last_busy"] = time.time() - runtime.IDLE_MINUTES * 60 - 1
    await asyncio.sleep(0.4)
    assert runtime.state["phase"] == "error" and "kapatılamadı" in runtime.state["detail"]
    assert len(reaps) >= 2


async def test_lost_ssh_master_restores_port_forward(watch, unused_tcp_port, monkeypatch):
    """SSH ana bağlantısı koparsa sonraki ssh komutu yönlendirmesiz yeni bir ana bağlantı açar:
    ComfyUI yaşıyor ama port kapalı. Yönlendirme yeniden kurulur, GPU kapatılmaz."""
    monkeypatch.setattr(runtime, "COMFY_URL", f"http://127.0.0.1:{unused_tcp_port}")
    await asyncio.sleep(0.3)
    assert any("-O forward" in c for c in watch["calls"])
    assert f"http://127.0.0.1:{unused_tcp_port}" != runtime.COMFY_URL
    assert not watch["stops"] and runtime.state["phase"] == "ready"


async def test_manual_stop_during_crash_check_is_not_reported_as_crash(watch, monkeypatch):
    """Elle kapatma ssh bağlantısını keser; izleme turu o sırada ComfyUI'yi yokluyorsa ulaşamaz.
    Kapanış zaten sürdüğü için ikinci bir kapanış ve yanıltıcı "ulaşılamıyor" hatası olmamalı."""
    real_run = runtime.run

    async def run_while_user_stops(*cmd, **kw):
        if "pgrep" in " ".join(cmd):
            runtime.set_phase("stopping", "elle kapatıldı")  # pgrep beklenirken kullanıcı kapattı
            raise RuntimeError("komut başarısız (255)")
        return await real_run(*cmd, **kw)

    monkeypatch.setattr(runtime, "run", run_while_user_stops)
    watch["comfy"]["http"] = False
    await asyncio.sleep(0.8)
    assert not watch["stops"]
    assert runtime.state["phase"] == "stopping" and runtime.state["detail"] == "elle kapatıldı"


async def test_manual_stop_during_queue_read_does_not_start_second_shutdown(watch, aiohttp_server, monkeypatch):
    armed = {"on": False}

    async def queue_while_user_stops(request):
        if armed["on"]:
            runtime.set_phase("stopping", "elle kapatıldı")  # kuyruk yanıtı beklenirken kullanıcı kapattı
        return web.json_response({"queue_running": [], "queue_pending": []})

    app = web.Application()
    app.add_routes([web.get("/queue", queue_while_user_stops)])
    server = await aiohttp_server(app)
    monkeypatch.setattr(runtime, "COMFY_URL", str(server.make_url("")).rstrip("/"))
    await asyncio.sleep(0.2)  # eski sunucuya giden tur bitsin
    armed["on"] = True
    runtime.state["last_busy"] = time.time() - runtime.IDLE_MINUTES * 60 - 1
    await asyncio.sleep(0.3)
    assert not watch["stops"]
    assert runtime.state["phase"] == "stopping" and runtime.state["detail"] == "elle kapatıldı"
