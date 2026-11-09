"""GPU durum makinesi (runtime.state["phase"]) yalnızca izinli geçişlerle ilerler.

set_phase çağrıları kaydedilir ve runtime.PHASE_TRANSITIONS ile karşılaştırılır. Yeni bir geçiş eklenirse
(ya da yanlış yerden set_phase çağrılırsa) bu test tabloyu bilinçli güncellemeye zorlar.
"""
import asyncio
import json

import pytest

import runtime

# Geçiş tablosu üretim kodunda: tek kaynak.
ALLOWED = runtime.PHASE_TRANSITIONS


@pytest.fixture
def transitions(monkeypatch):
    """Her set_phase çağrısını (önceki, yeni) olarak kaydeder."""
    seen = []
    real = runtime.set_phase

    def recording(phase, detail=""):
        seen.append((runtime.state["phase"], phase))
        real(phase, detail)

    monkeypatch.setattr(runtime, "set_phase", recording)
    runtime.state["phase"] = "off"
    yield seen
    runtime.state["phase"] = "off"
    runtime.ready.clear()


def assert_allowed(seen):
    illegal = [(a, b) for a, b in seen if b not in ALLOWED[a]]
    assert not illegal, f"izinsiz faz geçişi: {illegal}"


def test_table_covers_every_phase_exactly():
    assert set(ALLOWED) == {"off", "starting", "ready", "stopping", "error"}
    assert all(targets <= set(ALLOWED) for targets in ALLOWED.values())


@pytest.fixture
def fake_runtime(data_dir, monkeypatch):
    """Colab, SSH ve ComfyUI olmadan açılış adımlarını yürütür."""
    sessions = data_dir / "sessions.json"
    sessions.write_text(json.dumps({runtime.SESSION: {"endpoint": "e1"}}))
    box = {"fail": None, "stop_fail": False, "stops": 0}

    async def fake_run(*cmd, **kw):
        if box["fail"]:
            raise RuntimeError(box["fail"])
        if len(cmd) > 1 and cmd[1] == "usage":
            return "Current balance: 100.0 compute units\nUsage rate: 1.5 per hour"
        return ""

    async def fake_stop_runtime():
        box["stops"] += 1
        if box["stop_fail"]:
            raise RuntimeError("kapatılamadı")

    async def noop(*a, **kw):
        return None

    monkeypatch.setattr(runtime, "COLAB_SESSIONS", sessions)
    monkeypatch.setattr(runtime, "preflight", lambda: None)  # testlerde colab CLI yok
    monkeypatch.setattr(runtime, "SSH_CONF", data_dir / "ssh.conf")
    monkeypatch.setattr(runtime, "run", fake_run)
    monkeypatch.setattr(runtime, "stop_runtime", fake_stop_runtime)
    for name in ("ensure_ssh_key", "connect", "forward_comfy", "wait_comfy_ready", "warmup", "poll_packs"):
        monkeypatch.setattr(runtime, name, noop)
    monkeypatch.setattr(runtime, "_own", lambda endpoint: None)
    runtime.ready = asyncio.Event()
    return box


async def finish_start():
    await runtime._start_task


async def test_successful_start_then_stop(transitions, fake_runtime):
    runtime.start("none")
    await finish_start()
    assert runtime.state["phase"] == "ready" and runtime.ready.is_set()
    await runtime.stop("elle kapatıldı")
    assert runtime.state["phase"] == "off" and not runtime.ready.is_set()
    assert [b for _, b in transitions] == ["starting", "ready", "stopping", "off"]
    assert_allowed(transitions)


async def test_failed_start_ends_in_error_and_can_restart(transitions, fake_runtime):
    fake_runtime["fail"] = "colab new başarısız"
    runtime.start("none")
    await finish_start()
    assert runtime.state["phase"] == "error" and "başarısız" in runtime.state["detail"]
    assert [b for _, b in transitions] == ["starting", "stopping", "error"]

    fake_runtime["fail"] = None
    runtime.start("none")  # error'dan yeni açılış
    await finish_start()
    assert runtime.state["phase"] == "ready"
    assert [b for _, b in transitions] == ["starting", "stopping", "error", "starting", "ready"]
    assert_allowed(transitions)


async def test_failed_start_with_failed_cleanup_still_ends_in_error(transitions, fake_runtime):
    fake_runtime["fail"] = "ssh bağlanamadı"
    fake_runtime["stop_fail"] = True
    runtime.start("none")
    await finish_start()
    assert runtime.state["phase"] == "error"
    assert_allowed(transitions)


async def test_stop_failure_ends_in_error_and_retry_reaches_off(transitions, fake_runtime):
    runtime.start("none")
    await finish_start()
    fake_runtime["stop_fail"] = True
    await runtime.stop("kapat")
    assert runtime.state["phase"] == "error"
    fake_runtime["stop_fail"] = False
    await runtime.stop("yeniden dene")
    assert runtime.state["phase"] == "off"
    assert_allowed(transitions)


async def test_start_is_ignored_unless_off_or_error(transitions, fake_runtime):
    for phase in ("starting", "ready", "stopping"):
        runtime.state["phase"] = phase
        runtime.start("none")
        assert runtime.state["phase"] == phase
    assert transitions == []


async def test_cancelling_a_start_goes_through_stopping(transitions, fake_runtime, monkeypatch):
    gate = asyncio.Event()

    async def slow_connect(*a, **kw):
        await gate.wait()

    monkeypatch.setattr(runtime, "connect", slow_connect)
    runtime.start("none")
    await asyncio.sleep(0.05)
    assert runtime.state["phase"] == "starting"
    await runtime.stop("açılış iptal")
    assert runtime.state["phase"] == "off"
    assert [b for _, b in transitions][:2] == ["starting", "stopping"]
    assert_allowed(transitions)


async def test_missing_setup_ends_in_error_without_touching_colab(transitions, fake_runtime, monkeypatch):
    monkeypatch.setattr(runtime, "preflight", lambda: "colab CLI bulunamadı")
    runtime.start("none")  # açılış görevi hiç başlamaz: hata isteğin yanıtında görünür
    assert runtime.state["phase"] == "error" and "colab CLI" in runtime.state["detail"]
    runtime.start("none")  # kurulum hâlâ eksikken yeniden deneme
    assert [b for _, b in transitions] == ["error", "error"]
    assert fake_runtime["stops"] == 0
    assert_allowed(transitions)
