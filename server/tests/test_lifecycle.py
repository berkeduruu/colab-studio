"""Oturum yaşam döngüsü: izleme görevleri, boşta kapatma ve kredi.

Her oturumun kendi watchdog'u ve metrik döngüsü olmalı; yoksa açılan oturum boşta kapanmaz ve kredi
yakmaya devam eder. Boşta kapanış yöneticiden geçer ki kayıttaki slot da düşsün.
"""
import asyncio

import pytest

import runtime
import sessions
import studio

DEFAULT = runtime.SESSION
SECOND = f"{runtime.SESSION}-2"


@pytest.fixture
async def manager(db, monkeypatch, tmp_path):
    """Varsayılan oturum + boş kayıt; oturum açılışı sahtelenir (gerçek Colab/SSH yok)."""
    mgr = sessions.Manager(sessions.Registry(tmp_path / "sessions.json", check_ports=False))
    monkeypatch.setattr(sessions, "_manager", mgr)
    monkeypatch.setattr(runtime.SessionRuntime, "start", lambda self, pack="image", packs=None: None)
    runtime.DEFAULT.state["phase"] = "off"
    yield mgr
    for tasks in list(mgr.tasks.values()):
        for task in tasks:
            task.cancel()
    mgr.tasks.clear()
    for rt in mgr.live.values():
        rt.state["phase"] = "off"


@pytest.fixture
def watched(monkeypatch):
    """İzleme döngülerini kaydeden sahteler: gerçek ssh/ağ yok."""
    calls = []

    async def fake_watchdog(has_pending, on_credits, session=None):
        calls.append(("watch", (session or runtime.DEFAULT).name))
        await asyncio.sleep(3600)

    async def fake_metrics(session=None):
        calls.append(("metrics", (session or runtime.DEFAULT).name))
        await asyncio.sleep(3600)

    monkeypatch.setattr(runtime, "watchdog", fake_watchdog)
    monkeypatch.setattr(runtime, "metrics_loop", fake_metrics)
    return calls


async def test_starting_a_session_starts_its_watchdogs(manager, watched):
    """Oturum açılınca boşta kapatma ve metrik döngüsü de başlar."""
    rt = manager.start(pack=None)
    manager.watch(rt, lambda: False, lambda credits: None)
    await asyncio.sleep(0)
    assert rt.name in manager.tasks and len(manager.tasks[rt.name]) == 2
    assert ("watch", SECOND) in watched and ("metrics", SECOND) in watched


async def test_stopping_a_session_cancels_its_watchdogs(manager, watched):
    """Kapanan oturumun döngüleri durur: yoksa kapalı oturum için ssh açıp dururlar."""
    rt = manager.start(pack=None)
    manager.watch(rt, lambda: False, lambda credits: None)

    async def fake_stop(self, reason="elle kapatıldı"):
        return None

    tasks = list(manager.tasks[rt.name])
    rt.stop = fake_stop.__get__(rt, type(rt))
    await manager.stop(rt.name)
    await asyncio.sleep(0)
    assert all(task.cancelled() or task.done() for task in tasks)
    assert rt.name not in manager.tasks


async def test_idle_shutdown_of_a_new_session_goes_through_the_manager(manager, monkeypatch):
    """Oturum boşta kapanınca kayıttaki slot düşer: portlar bir sonraki açılışta yeniden kullanılır."""
    rt = manager.start(pack=None)
    assert rt.stop_hook is not None
    stopped = []

    async def fake_stop(name, reason="elle kapatıldı"):
        stopped.append((name, reason))
        manager.registry.release(manager.get(name).slot)
        manager.live.pop(name, None)

    monkeypatch.setattr(manager, "stop", fake_stop)
    await rt.stop_hook("15 dk boşta kaldı, GPU kapatıldı")
    assert stopped == [(SECOND, "15 dk boşta kaldı, GPU kapatıldı")]
    assert SECOND not in manager.live


async def test_default_session_keeps_the_module_stop_path(manager):
    """Varsayılan oturumun kancası yok: kapanış eski yoldan (testlerin yamaladığı `stop`) gider."""
    assert runtime.DEFAULT.stop_hook is None
    manager.start(slot=0, pack=None)
    assert runtime.DEFAULT.stop_hook is None


async def test_keep_warm_turns_off_the_idle_shutdown(manager):
    """`keep_warm` açıksa oturum boşta kapanmaz (uzun işler arası model yüklemesi yaşanmasın)."""
    rt = manager.start(pack=None, keep_warm=True)
    assert rt.state["auto_shutdown"] is False
    other = manager.start(pack=None, keep_warm=False)
    assert other.state["auto_shutdown"] is True


async def test_keep_warm_is_saved_with_the_plan(client, manager):
    """Arayüz plan kaydederken keep_warm'ı da gönderebiliyor."""
    body = await (await client.put("/api/sessions/plan", json={"plan": 2, "keep_warm": True})).json()
    assert body["keep_warm"] is True
    assert studio.get_settings()[sessions.KEEP_WARM_KEY] is True


async def test_new_sessions_are_watched_through_the_api(client, manager, watched):
    """API'den açılan oturum izlemeli doğar; izlemesiz oturum kredi sızdırır."""
    out = await (await client.post("/api/sessions", json={"count": 1})).json()
    assert out["started"] == [SECOND]
    await asyncio.sleep(0)
    assert SECOND in manager.tasks


async def test_manager_start_works_with_the_real_runtime_method(db, monkeypatch, tmp_path):
    """The API path (`Manager.start` → `SessionRuntime.start`) must work with the real object: patched
    fakes hid a signature mismatch (a method taking only `pack` made `POST /api/sessions` return 500)."""
    manager = sessions.Manager(sessions.Registry(tmp_path / "sessions.json", check_ports=False))
    monkeypatch.setattr(sessions, "_manager", manager)

    async def no_start(session=None):
        return None

    monkeypatch.setattr(runtime, "_start", no_start)
    rt = manager.start(pack=None, packs=["image"])
    assert rt.state["phase"] == "starting"
    assert rt.state["startup"]["packs"] == ["image"]
