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
    rt.state["phase"] = "ready"
    await rt.stop_hook("15 dk boşta kaldı, GPU kapatıldı")
    for _ in range(5):  # kapanış kendi görevinde yürür
        await asyncio.sleep(0)
    assert stopped == [(SECOND, "15 dk boşta kaldı, GPU kapatıldı")]
    assert SECOND not in manager.live


async def test_idle_shutdown_from_inside_the_watchdog_completes(manager, monkeypatch):
    """Boşta kapanış, oturumun kendi watchdog görevinin içinden tetiklenir. Yönetici kapanışta o
    görevi iptal ediyor: kapanış aynı görevde yürüseydi yarıda kesilir, faz "stopping"de takılır,
    runtime Colab'da açık kalır ve slot hiç boşalmazdı."""
    rt = manager.start(pack=None)
    rt.state["phase"] = "ready"
    released = []

    async def fake_stop_runtime(s):
        await asyncio.sleep(0.01)
        released.append(s.name)

    monkeypatch.setattr(runtime, "_stop_runtime_of", fake_stop_runtime)

    async def watchdog_like():
        await rt.stop_hook("15 dk boşta kaldı, GPU kapatıldı")
        await asyncio.sleep(3600)  # watchdog döngüsü sürer; yönetici onu iptal eder

    watchdog = asyncio.create_task(watchdog_like())
    manager.tasks[rt.name] = [watchdog]
    for _ in range(50):
        if SECOND not in manager.live:
            break
        await asyncio.sleep(0.01)
    assert released == [SECOND]
    assert rt.state["phase"] == "off"
    assert SECOND not in manager.live and manager.registry.entries() == {}
    await asyncio.sleep(0)
    assert watchdog.cancelled() or watchdog.done()


async def test_a_session_that_cannot_be_released_stays_listed(manager, monkeypatch):
    """Colab'da bırakılamayan oturum listeden ve kayıttan düşmez: yeniden kapatılabilsin,
    colab-stop-all.sh ve sonraki açılış da onu bilsin (düşseydi kimsenin izlemediği runtime kalırdı)."""
    rt = manager.start(pack=None)
    rt.state["phase"] = "ready"

    async def failing_stop_runtime(s):
        raise RuntimeError("ağ yok")

    monkeypatch.setattr(runtime, "_stop_runtime_of", failing_stop_runtime)
    result = await manager.stop_all("hepsi kapatıldı")
    assert SECOND in manager.live and rt.state["phase"] == "error"
    assert [e["name"] for e in manager.registry.entries().values()] == [SECOND]
    assert any(e.startswith(f"{SECOND}:") for e in result["errors"]) and SECOND not in result["stopped"]


async def test_leftover_sessions_from_a_previous_run_are_released(manager, monkeypatch):
    """Servis yeniden açılınca önceki çalışmadan kayıtta kalan ek oturumlar kapatılır ve slotları boşalır;
    bırakılamayan kayıtta kalır (sonraki açılışta yeniden denenir)."""
    manager.registry.reserve(1)
    manager.registry.reserve(2)
    stopped = []

    async def fake_stop_runtime(self):
        stopped.append(self.name)
        if self.slot == 2:
            raise RuntimeError("ağ yok")

    monkeypatch.setattr(runtime.SessionRuntime, "stop_runtime", fake_stop_runtime)
    result = await manager.reap_leftovers()
    assert stopped == [SECOND, f"{runtime.SESSION}-3"]
    assert result["released"] == [SECOND] and len(result["errors"]) == 1
    assert list(manager.registry.entries()) == [2]
    assert manager.names() == [DEFAULT]  # temizlik oturum listesine bir şey eklemez


async def test_service_shutdown_stops_the_extra_sessions(manager, monkeypatch):
    """Servis durunca ek oturumlar da kapanır: Docker'da ve run.sh'ta systemd'nin ExecStopPost adımı yok."""
    import app as app_module

    rt = manager.start(pack=None)
    rt.state["phase"] = "ready"
    stopped = []

    async def fake_stop_runtime(s):
        stopped.append(s.name)

    monkeypatch.setattr(runtime, "_stop_runtime_of", fake_stop_runtime)
    await app_module.on_shutdown({"tasks": []})
    assert stopped == [SECOND]
    assert SECOND not in manager.live and manager.registry.entries() == {}


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
