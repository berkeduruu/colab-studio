"""Oturum API'si: liste, plan, açma/kapatma ve iş kayıtlarında oturum alanı.

Gerçek `colab` çağrılmaz: oturum başlatma `SessionRuntime.start` üzerinden sahtelenir, böylece
API'nin sözleşmesi (yanıt biçimi, 409/404, plan sınırı) GPU'suz doğrulanır.
"""
import asyncio
import sqlite3

import pytest

import runtime
import sessions
import studio

DEFAULT = runtime.SESSION
SECOND = f"{runtime.SESSION}-2"
UI = {"X-Studio-UI": "1"}  # arayüz isteği: sunucu metinleri İngilizceye çevrilmez


@pytest.fixture
async def manager(db, monkeypatch, tmp_path):
    """Her test kendi boş oturum kaydıyla başlar (port kontrolü yok: gerçek soket açılmaz)."""
    mgr = sessions.Manager(sessions.Registry(tmp_path / "sessions.json", check_ports=False))
    monkeypatch.setattr(sessions, "_manager", mgr)

    async def quiet_loop(*args, **kwargs):
        """İzleme döngüleri gerçek `colab`/ssh çağırmasın: testte yalnız uyurlar."""
        await asyncio.sleep(3600)

    monkeypatch.setattr(runtime, "watchdog", quiet_loop)
    monkeypatch.setattr(runtime, "metrics_loop", quiet_loop)
    yield mgr
    for tasks in list(mgr.tasks.values()):
        for task in tasks:
            task.cancel()
    mgr.tasks.clear()
    for rt in mgr.live.values():
        rt.state["phase"] = "off"


@pytest.fixture
def started(monkeypatch):
    """Oturum açılışını kaydeden sahte: gerçek Colab/SSH çağrısı yapılmaz."""
    names = []

    def fake_start(self, pack="image", packs=None):
        names.append((self.name, pack))
        self.state["phase"] = "starting"
        self.state["detail"] = "test"
        self.state["startup"] = None

    monkeypatch.setattr(runtime.SessionRuntime, "start", fake_start)
    return names


async def test_sessions_list_starts_with_the_default_session(client, manager):
    """Varsayılan oturum her zaman listede: arayüz tek oturumda da aynı veriyi görür."""
    body = await (await client.get("/api/sessions")).json()
    assert [s["name"] for s in body["sessions"]] == [DEFAULT]
    assert body["sessions"][0]["slot"] == 0
    assert body["sessions"][0]["port"] == 8188
    assert body["limits"] == {"max": sessions.MAX_SESSIONS, "default": sessions.DEFAULT_SESSIONS,
                              "plan": sessions.DEFAULT_SESSIONS, "active": 0}


async def test_runtime_endpoint_keeps_legacy_shape_and_adds_sessions(client, manager):
    """Eski istemciler bozulmaz: üst düzey alanlar aynı, yanına oturum listesi eklenir."""
    body = await (await client.get("/api/runtime")).json()
    for key in ("phase", "detail", "auto_shutdown", "idle_seconds", "idle_minutes", "credits", "session_spent",
                "session_seconds", "startup", "gpu", "packs", "gpu_now", "credit_stats", "queue"):
        assert key in body, key
    assert body["phase"] == "off"
    assert body["sessions"][0]["name"] == DEFAULT
    assert body["limits"]["max"] == sessions.MAX_SESSIONS


async def test_session_plan_roundtrip(client, manager):
    """Plan kaydedilir ve "soruldu" damgası kalıcıdır (soru bir kez sorulur)."""
    first = await (await client.get("/api/sessions/plan")).json()
    assert first["plan"] == sessions.DEFAULT_SESSIONS and first["asked"] is False

    saved = await (await client.put("/api/sessions/plan", json={"plan": 2})).json()
    assert saved["plan"] == 2 and saved["asked"] is True

    again = await (await client.get("/api/sessions/plan")).json()
    assert again["plan"] == 2 and again["asked"] is True


async def test_session_plan_is_clamped(client, manager):
    """Arayüz 9 gönderse bile sert üst sınır uygulanır."""
    body = await (await client.put("/api/sessions/plan", json={"plan": 99})).json()
    assert body["plan"] == sessions.MAX_SESSIONS
    body = await (await client.put("/api/sessions/plan", json={"plan": 0})).json()
    assert body["plan"] == 1


async def test_session_start_opens_a_new_slot(client, manager, started):
    """Yeni oturum ikinci slotu alır ve listeye girer; portları çakışmaz."""
    response = await client.post("/api/sessions", json={"count": 1})
    assert response.status == 200
    body = await response.json()
    assert body["started"] == [SECOND]
    assert started == [(SECOND, "image")]
    assert [s["name"] for s in body["sessions"]] == [DEFAULT, SECOND]
    assert {s["port"] for s in body["sessions"]} == {8188, 8200}


async def test_session_start_respects_the_plan(client, manager, started):
    """Plan 2 iken iki oturum açılır, üçüncü istek 409 alır (sessizce taşmaz)."""
    await client.put("/api/sessions/plan", json={"plan": 2})
    assert (await client.post("/api/sessions", json={"count": 3})).status == 200
    assert len(started) == 2
    again = await client.post("/api/sessions", json={"count": 1}, headers=UI)
    assert again.status == 409
    assert "eşzamanlı oturum sınırı dolu (plan 2)" in await again.text()


async def test_session_start_rejects_an_unknown_pack(client, manager):
    response = await client.post("/api/sessions", json={"profile": "yok-boyle-paket"}, headers=UI)
    assert response.status == 400
    assert "bilinmeyen model paketi: yok-boyle-paket" in await response.text()


async def test_session_can_be_opened_with_a_pack_subset(client, manager, monkeypatch):
    """`packs` verilirse yalnız o paketler iner; kurulum betiğine profil olarak geçer."""
    seen = {}

    def fake_start(self, pack="image", packs=None):
        seen["packs"] = packs
        self.state["phase"] = "starting"

    monkeypatch.setattr(runtime.SessionRuntime, "start", fake_start)
    response = await client.post("/api/sessions", json={"count": 1, "packs": ["image", "video"]})
    assert response.status == 200
    assert seen["packs"] == ["image", "video"]


async def test_unknown_pack_in_the_profile_is_rejected(client, manager):
    response = await client.post("/api/sessions", json={"count": 1, "packs": ["image", "yok"]}, headers=UI)
    assert response.status == 400
    assert "bilinmeyen model paketi: yok" in await response.text()


async def test_empty_pack_profile_is_rejected(client, manager):
    """Boş liste "hiçbir paket inmesin" demek değil: yanlışlıkla boş oturum açılmasın."""
    response = await client.post("/api/sessions", json={"count": 1, "packs": []})
    assert response.status == 400


async def test_session_stop_releases_the_slot(client, manager, started, monkeypatch):
    """Kapatılan oturumun kaydı düşer, varsayılan oturum yerinde kalır."""
    await client.post("/api/sessions", json={"count": 1})
    stopped = []

    async def fake_stop(self, name, reason="elle kapatıldı"):
        stopped.append(name)
        self.registry.release(self.get(name).slot)
        self.live.pop(name, None)

    monkeypatch.setattr(sessions.Manager, "stop", fake_stop)
    response = await client.post(f"/api/sessions/{SECOND}/stop")
    assert response.status == 200
    await asyncio.sleep(0)  # arka plandaki kapanış görevi çalışsın
    assert stopped == [SECOND]
    assert [s["name"] for s in manager.snapshot()] == [DEFAULT]
    assert manager.registry.entries() == {}


async def test_stopping_an_unknown_session_is_404(client, manager):
    response = await client.post(f"/api/sessions/{runtime.SESSION}-9/stop", headers=UI)
    assert response.status == 404
    assert f"oturum bulunamadı: {runtime.SESSION}-9" in await response.text()


async def test_stopping_the_default_session_stops_the_runtime(client, manager, monkeypatch):
    """Varsayılan oturumun kapanışı eski yolu izler: faz hemen "stopping", kapanış arka planda.

    Bekleyen işler iptal edilir (kalsalardı ilki GPU'yu hemen yeniden açardı), ama **yalnız bu
    oturumun** işleri: bir oturumu kapatmak başka oturumun sırasını boşaltmaz."""
    mine = studio.create_job(None, "image", "image", "a", {"count": 1}, [])
    other = studio.create_job(None, "image", "image", "b", {"count": 1}, [], session=SECOND)
    reasons = []

    async def fake_run_stop(reason):
        reasons.append(reason)

    monkeypatch.setattr(runtime, "run_stop", fake_run_stop)
    response = await client.post(f"/api/sessions/{DEFAULT}/stop")
    assert response.status == 200
    await asyncio.sleep(0)
    assert reasons == ["elle kapatıldı"]
    assert runtime.state["phase"] == "stopping"
    assert studio.get_job(mine["id"])["error"] == studio.CANCELLED
    assert studio.get_job(other["id"])["status"] == "queued"


async def test_stop_all_accepts_the_call(client, manager, started, monkeypatch):
    """Tümünü kapat: istek anında döner, kapanış arka planda yürür."""
    stopped = []

    async def fake_stop_all(self, reason="all closed"):
        stopped.append(reason)
        return {"stopped": [], "errors": []}

    monkeypatch.setattr(sessions.Manager, "stop_all", fake_stop_all)
    response = await client.post("/api/sessions/stop-all")
    assert response.status == 200
    assert (await response.json())["sessions"][0]["name"] == DEFAULT
    await asyncio.sleep(0)
    assert stopped == ["hepsi kapatıldı"]


async def test_jobs_carry_the_session_that_ran_them(db):
    """İş kaydı hangi oturumda çalıştığını taşır (arayüzde rozet, MCP'de rapor)."""
    job = studio.create_job(None, "image", "image", "test", {"count": 1}, [], session=SECOND, group_id="g1")
    assert job["session"] == SECOND and job["group_id"] == "g1"
    assert studio.get_job(job["id"])["session"] == SECOND


def test_old_jobs_get_the_new_columns_on_init(tmp_path, monkeypatch):
    """Eski veritabanı açılışta kendini günceller: oturum alanları olmayan tabloya eklenir."""
    path = tmp_path / "eski.db"
    old = sqlite3.connect(path)
    old.executescript("""
    CREATE TABLE threads (id TEXT PRIMARY KEY, mode TEXT NOT NULL, title TEXT NOT NULL, created_at REAL NOT NULL);
    CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, mode TEXT NOT NULL, kind TEXT NOT NULL,
      prompt TEXT NOT NULL, params TEXT NOT NULL, inputs TEXT NOT NULL, status TEXT NOT NULL,
      progress REAL NOT NULL DEFAULT 0, error TEXT, outputs TEXT NOT NULL DEFAULT '[]', text TEXT,
      created_at REAL NOT NULL, finished_at REAL, stage TEXT);
    INSERT INTO jobs VALUES ('j1', NULL, 'image', 'image', 'eski', '{}', '[]', 'done', 1, NULL, '[]', NULL, 0, 0, NULL);
    """)
    old.commit()
    old.close()

    monkeypatch.setattr(studio, "DB_PATH", path)
    monkeypatch.setattr(studio, "MEDIA_DIR", tmp_path / "media")
    studio.init()
    cols = {r["name"] for r in studio.db.execute("PRAGMA table_info(jobs)")}
    assert {"session", "group_id"} <= cols
    assert studio.get_job("j1")["session"] is None  # eski iş "oturum yok" olarak görünür
    studio.db.close()
