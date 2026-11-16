"""Stüdyonun açtığı runtime'ların kaydı (owned_endpoints.json) ve yarım açılıştan kalan sahipsiz runtime.

Bozuk kayıt dosyası kapanışı yarıda kesip GPU'yu "stopping"de bırakıyor, servis açılışını düşürüyordu;
"colab new" runtime'ı alıp adlandırmadan ölünce de "[?]" runtime kredi yakmaya devam ediyordu.
"""
import json

import pytest

import app as app_module
import runtime


def sessions_text(*lines):
    return "\n".join(f"[{name}] {endpoint} | Hardware: L4 | Shape: Standard | Variant: GPU" for name, endpoint in lines)


@pytest.fixture
def colab(data_dir, monkeypatch):
    """colab CLI yerine: `colab sessions` çıktısı ayarlanır, unassign çağrıları kaydedilir."""
    box = {"sessions": "", "unassigned": [], "stopped": 0}

    async def fake_run(*cmd, **kw):
        if cmd[0] == runtime.COLAB and cmd[1] == "sessions":
            return box["sessions"]
        if cmd[0] == runtime.COLAB and cmd[1] == "stop":
            box["stopped"] += 1
            return ""
        if "-c" in cmd and "unassign" in cmd[-1]:
            box["unassigned"].append(cmd[-1].split("unassign(")[1].strip("')"))
            return ""
        return ""

    monkeypatch.setattr(runtime, "run", fake_run)
    monkeypatch.setattr(runtime, "SSH_CONF", data_dir / "yok.conf")
    runtime.state["phase"] = "off"
    yield box
    runtime.state["phase"] = "off"


@pytest.mark.parametrize("content", ['["e1", "e2"', "", "{}", '{"a": 1}', "null", "\x00\x01"])
async def test_corrupt_owned_file_does_not_break_stop(colab, content):
    runtime.OWNED_FILE.write_text(content)
    assert runtime._owned() == []
    await runtime.stop_runtime()  # JSONDecodeError fırlatmaz
    runtime._own("e3")
    assert json.loads(runtime.OWNED_FILE.read_text()) == ["e3"]


async def test_own_writes_atomically(colab):
    runtime._own("e1")
    runtime._own("e2")
    assert runtime._owned() == ["e1", "e2"]
    assert not list(runtime.DATA_DIR.glob("*.tmp"))


async def test_service_starts_with_corrupt_owned_file(colab, db, monkeypatch):
    """Önceden servis açılırken çöküyordu (GET / bağlantı yok)."""
    runtime.OWNED_FILE.write_text('["e1",')
    application = app_module.create_app()
    await app_module.on_startup(application)
    try:
        assert runtime.state["phase"] == "off"
    finally:
        for task in application["tasks"]:
            task.cancel()


async def test_failed_startup_cleanup_is_marked_for_retry(colab, db, monkeypatch):
    async def broken():
        raise RuntimeError("GPU kapatılamadı, yeniden denenecek: ağ yok")

    monkeypatch.setattr(runtime, "stop_runtime", broken)
    monkeypatch.setattr(runtime, "startup_cleanup_failed", False)
    application = app_module.create_app()
    await app_module.on_startup(application)
    try:
        assert runtime.state["phase"] == "error" and runtime.startup_cleanup_failed
        monkeypatch.setattr(runtime, "run", broken_run)
        runtime.start("none")  # kullanıcı yine de başlattı: eski temizlik hatası artık geçersiz
        assert not runtime.startup_cleanup_failed
        await runtime._start_task
    finally:
        for task in application["tasks"]:
            task.cancel()


async def broken_run(*cmd, **kw):
    raise RuntimeError("colab new başarısız")


async def test_unexpected_stop_error_never_leaves_phase_stopping(colab, monkeypatch):
    async def broken():
        raise ValueError("beklenmedik")

    monkeypatch.setattr(runtime, "stop_runtime", broken)
    runtime.state["phase"] = "ready"
    await runtime.stop("elle")
    assert runtime.state["phase"] == "error"


async def test_failed_start_with_unexpected_stop_error_ends_in_error(colab, monkeypatch):
    async def broken():
        raise ValueError("beklenmedik")

    async def failing(*cmd, **kw):
        raise RuntimeError("colab new başarısız")

    monkeypatch.setattr(runtime, "stop_runtime", broken)
    monkeypatch.setattr(runtime, "run", failing)
    monkeypatch.setattr(runtime, "preflight", lambda: None)
    runtime.start("none")
    await runtime._start_task
    assert runtime.state["phase"] == "error"


async def test_orphan_of_interrupted_start_is_released(colab):
    """colab new runtime'ı aldı ama adlandırmadan öldü: açılıştan önce olmayan "[?]" runtime bırakılır;
    önceden açık olan adsız runtime'a ve kullanıcının adlı oturumlarına dokunulmaz."""
    runtime._write_json(runtime.PENDING_FILE, ["e-eski"])
    colab["sessions"] = sessions_text(("?", "e-eski"), ("?", "e-yarim"), ("benim", "e-kullanici"))
    await runtime.reap_owned()
    assert colab["unassigned"] == ["e-yarim"]
    assert not runtime.PENDING_FILE.exists()


async def test_orphan_is_not_reaped_while_a_start_is_running(colab):
    runtime._write_json(runtime.PENDING_FILE, [])
    colab["sessions"] = sessions_text(("?", "e-yeni"))
    runtime.state["phase"] = "starting"
    await runtime.reap_owned()
    assert colab["unassigned"] == [] and runtime.PENDING_FILE.exists()


async def test_successful_start_records_endpoint_and_clears_pending(colab, monkeypatch, data_dir):
    sessions = data_dir / "sessions.json"
    sessions.write_text(json.dumps({runtime.SESSION: {"endpoint": "e1"}}))
    monkeypatch.setattr(runtime, "COLAB_SESSIONS", sessions)
    monkeypatch.setattr(runtime, "preflight", lambda: None)
    real_run = runtime.run

    async def run(*cmd, **kw):
        if cmd[0] == runtime.COLAB and cmd[1] == "new":
            assert runtime.PENDING_FILE.exists()  # colab new sürerken kayıt yerinde
        if cmd[0] == runtime.COLAB and cmd[1] == "usage":
            return "Current balance: 1.0\nUsage rate: 1.0"
        return await real_run(*cmd, **kw)

    async def noop(*a, **kw):
        return None

    monkeypatch.setattr(runtime, "run", run)
    for name in ("ensure_ssh_key", "connect", "forward_comfy", "wait_comfy_ready", "warmup", "poll_packs"):
        monkeypatch.setattr(runtime, name, noop)
    import asyncio
    runtime.ready = asyncio.Event()
    runtime.start("none")
    await runtime._start_task
    assert runtime.state["phase"] == "ready"
    assert runtime._owned() == ["e1"] and not runtime.PENDING_FILE.exists()
    await runtime.stop("test")
