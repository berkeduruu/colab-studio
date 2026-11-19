"""Paralel çalışma: iki oturumun işi **aynı anda**, aynı oturumun işleri **sırayla** yürür.

Çoklu oturumun asıl vaadi: bir videoyla birlikte bir görsel üretilebilmeli. Testler gerçek
ComfyUI/Colab çağırmaz; `studio._run` sahtelenir ve iki işin aynı anda çalışıp çalışmadığı paylaşılan
bir olay (barrier) ile ölçülür. Aynı dosyada `parallel` ile bir istekte birden çok oturuma iş dağıtma
(`POST /api/jobs`) da doğrulanır: tek işte yanıt biçimi **değişmez**, paralel istekte
`{"jobs": [...], "group_id": ...}` döner.
"""
import asyncio

import pytest

import runtime
import sessions
import studio

IMAGE = {"ratio": "1:1", "size": "1K", "width": 1024, "height": 1024, "count": 1, "steps": 2, "cfg": 3.0,
         "edit_resolution": "1080p", "edit_pixels": 1440, "sampler": "euler", "scheduler": "simple",
         "seed": 1, "negative": ""}
DEFAULT = runtime.SESSION
SECOND = f"{runtime.SESSION}-2"
THIRD = f"{runtime.SESSION}-3"
UI = {"X-Studio-UI": "1"}  # arayüz isteği: sunucu metinleri İngilizceye çevrilmez


@pytest.fixture
async def two_sessions(db, monkeypatch, tmp_path):
    """Varsayılan oturum + slot 1 hazır; ikisi de "ready" (GPU açılışı beklenmez)."""
    manager = sessions.Manager(sessions.Registry(tmp_path / "sessions.json", check_ports=False))
    monkeypatch.setattr(sessions, "_manager", manager)
    second = manager.ensure(1)
    for rt in (runtime.DEFAULT, second):
        rt.state["phase"] = "ready"
        rt.ready.set()
        rt.local_modes.update(runtime.PACKS)
    yield manager
    for rt in (runtime.DEFAULT, second):
        rt.state["phase"] = "off"
        rt.ready.clear()
        rt.local_modes.clear()


async def _wait_status(job_id, timeout=10):
    for _ in range(int(timeout / 0.02)):
        job = studio.get_job(job_id)
        if job["status"] in ("done", "error"):
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(f"iş bitmedi: {studio.get_job(job_id)['status']}")


# --- iş dağıtıcısı (worker) ---


async def test_two_sessions_run_their_jobs_at_the_same_time(two_sessions, monkeypatch):
    """Bir iş diğerini beklemez: ikisi de aynı anda çalışmaya başlar."""
    started, finished = [], []
    both_running = asyncio.Event()

    async def fake_run(job, session=None):
        started.append((job["id"], (session or runtime.DEFAULT).name))
        if len(started) == 2:
            both_running.set()
        # İki iş de başlamadan hiçbiri bitmez: sıralı çalışsaydı bu bekleme zaman aşımına düşerdi.
        await asyncio.wait_for(both_running.wait(), 5)
        finished.append(job["id"])

    monkeypatch.setattr(studio, "_run", fake_run)
    task = asyncio.create_task(studio.worker())
    try:
        first = studio.create_job("t", "image", "image", "a", IMAGE, [])
        second = studio.create_job("t", "image", "image", "b", IMAGE, [], session=SECOND)
        a = await _wait_status(first["id"])
        b = await _wait_status(second["id"])
    finally:
        task.cancel()
    assert a["status"] == "done" and b["status"] == "done"
    assert sorted(finished) == sorted([first["id"], second["id"]])
    # İşler ait oldukları oturumda çalıştı ve iş satırı bunu taşıyor.
    assert dict(started) == {first["id"]: DEFAULT, second["id"]: SECOND}
    assert studio.get_job(second["id"])["session"] == SECOND
    assert studio.get_job(first["id"])["session"] == DEFAULT


async def test_jobs_of_the_same_session_wait_for_each_other(two_sessions, monkeypatch):
    """Bir ComfyUI tek grafiği verimli çalıştırır: aynı oturumun işleri sıraya girer."""
    running, peak = 0, []

    async def fake_run(job, session=None):
        nonlocal running
        running += 1
        peak.append(running)
        await asyncio.sleep(0.05)
        running -= 1

    monkeypatch.setattr(studio, "_run", fake_run)
    task = asyncio.create_task(studio.worker())
    try:
        # Aynı oturuma yazılı üç iş (otomatik yönlendirme ikinci oturuma dağıtırdı).
        ids = [studio.create_job("t", "image", "image", p, IMAGE, [], session=DEFAULT)["id"] for p in ("a", "b", "c")]
        for jid in ids:
            assert (await _wait_status(jid))["status"] == "done"
    finally:
        task.cancel()
    assert max(peak) == 1  # hiçbir an iki iş birlikte çalışmadı


async def test_a_job_goes_to_the_session_recorded_on_it(two_sessions, monkeypatch):
    """Oturum kaydı işin nerede koşacağını belirler (yönlendirme bu alanı yazar)."""
    seen = []

    async def fake_run(job, session=None):
        seen.append((session or runtime.DEFAULT).name)

    monkeypatch.setattr(studio, "_run", fake_run)
    task = asyncio.create_task(studio.worker())
    try:
        job = studio.create_job("t", "image", "image", "a", IMAGE, [], session=SECOND)
        await _wait_status(job["id"])
    finally:
        task.cancel()
    assert seen == [SECOND]


async def test_a_new_session_opens_its_own_runtime(two_sessions, monkeypatch):
    """GPU yeni oturumda açılır: iş oturumun kendi runtime'ını başlatır, varsayılana dokunmaz."""
    second = two_sessions.get(SECOND)
    second.state["phase"] = "off"
    second.ready.clear()
    started = []

    def fake_start(self, pack="image", packs=None):
        started.append((self.name, pack))
        self.state["phase"] = "starting"  # "ready" hiç gelmez: iş GPU bekler

    monkeypatch.setattr(runtime.SessionRuntime, "start", fake_start)
    monkeypatch.setattr(runtime, "start", lambda *a, **k: pytest.fail("varsayılan oturum açılmamalı"))
    task = asyncio.create_task(studio.worker())
    try:
        job = studio.create_job("t", "image", "image", "a", IMAGE, [], session=SECOND)
        for _ in range(100):
            if started:
                break
            await asyncio.sleep(0.02)
        assert started == [(SECOND, "image")]
        assert studio.get_job(job["id"])["status"] == "waiting_gpu"
    finally:
        task.cancel()
        for dispatch in list(studio._tasks):
            dispatch.cancel()
        await asyncio.sleep(0)
    assert studio.get_job(job["id"])["session"] == SECOND  # iş satırı oturumu hemen taşır


async def test_pending_jobs_can_be_counted_per_session(two_sessions):
    """Boşta kapatma kararı oturum başına verilir: bir oturumun işi diğerini meşgul göstermez."""
    studio.create_job("t", "image", "image", "a", IMAGE, [], session=SECOND)
    assert studio.has_pending_jobs(SECOND) is True
    assert studio.has_pending_jobs(DEFAULT) is False
    assert studio.has_pending_jobs() is True  # genel bakış hâlâ doğru
    assert studio.pending_count(SECOND) == 1 and studio.pending_count(DEFAULT) == 0


async def test_a_job_without_a_session_runs_on_the_default_one(two_sessions, monkeypatch):
    """Oturum yazılmamış iş varsayılan oturuma aittir: tek oturumlu davranış korunur."""
    studio.create_job("t", "image", "image", "a", IMAGE, [])
    assert studio.has_pending_jobs(DEFAULT) is True
    # Yük sayımı yalnız oturuma yazılmış işleri sayar: yönlendirilmemiş iş hiçbir oturumu dolu göstermez.
    assert studio.pending_count(DEFAULT) == 0


# --- paralel üretim: POST /api/jobs parallel ---


async def test_a_single_job_keeps_the_old_response_shape(client, two_sessions):
    """Tek iş: gövde doğrudan iş kaydı (eski istemciler bozulmaz)."""
    response = await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE})
    job = await response.json()
    assert response.status == 200
    assert "jobs" not in job and job["kind"] == "image"


async def test_parallel_spreads_jobs_over_sessions(client, two_sessions):
    """`parallel: 2` iki işi iki ayrı oturuma dağıtır; aynı grupta ve aynı sohbette."""
    response = await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE, "parallel": 2})
    out = await response.json()
    assert response.status == 200
    jobs = out["jobs"]
    assert len(jobs) == 2 and out["group_id"]
    assert {j["session"] for j in jobs} == {DEFAULT, SECOND}
    assert {j["group_id"] for j in jobs} == {out["group_id"]}
    assert len({j["thread_id"] for j in jobs}) == 1  # tek sohbette iki sonuç


async def test_parallel_without_spare_sessions_creates_fewer_jobs(client, two_sessions):
    """Açık oturum sayısı istenenden azsa fazlası açılmaz (kredi harcamamak için)."""
    two_sessions.ensure(2)  # slot 2 açık; sonra kapatılır
    two_sessions.get(THIRD).state["phase"] = "off"
    out = await (await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE,
                                                      "parallel": 3})).json()
    assert len(out["jobs"]) == 2


async def test_a_job_can_pin_a_session(client, two_sessions):
    """Kullanıcı bir oturum seçtiyse iş oraya gider (yönlendirme karışmaz)."""
    out = await (await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE,
                                                      "session": SECOND})).json()
    assert out["session"] == SECOND


async def test_auto_session_is_left_to_the_router(client, two_sessions):
    """`session: "auto"` yönlendiriciye bırakır: iş kaydında oturum boş kalır."""
    out = await (await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE,
                                                      "session": "auto"})).json()
    assert out["session"] is None


async def test_an_unknown_session_is_404(client, two_sessions):
    response = await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE,
                                                    "session": f"{runtime.SESSION}-9"}, headers=UI)
    assert response.status == 404
    assert f"oturum bulunamadı: {runtime.SESSION}-9" in await response.text()


async def test_pinning_a_session_and_asking_for_parallel_is_rejected(client, two_sessions):
    response = await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE,
                                                    "session": SECOND, "parallel": 2}, headers=UI)
    assert response.status == 400
    assert "belirli bir oturum seçildiyse parallel 1 olmalı" in await response.text()


async def test_parallel_out_of_range_is_rejected(client, two_sessions):
    for parallel in (0, 99):
        response = await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE,
                                                        "parallel": parallel}, headers=UI)
        assert response.status == 400
        assert f"parallel 1-{sessions.MAX_SESSIONS} arası olmalı" in await response.text()


async def test_parallel_with_every_session_closed_still_runs(client, db, monkeypatch, tmp_path):
    """Hiç oturum açık değilse iş varsayılan oturuma düşer: istek hata almaz."""
    monkeypatch.setattr(sessions, "_manager",
                        sessions.Manager(sessions.Registry(tmp_path / "s.json", check_ports=False)))
    runtime.DEFAULT.state["phase"] = "off"
    out = await (await client.post("/api/jobs", json={"kind": "image", "prompt": "kedi", "params": IMAGE,
                                                      "parallel": 2})).json()
    assert len(out["jobs"]) == 1 and out["jobs"][0]["session"] is None and out["group_id"]


# --- kapatılan oturum ve yük dağılımı ---


async def test_a_closed_session_is_not_reopened_by_its_waiting_job(two_sessions, monkeypatch):
    """Kapatılan ek oturumun sırada bekleyen işi oturumu yeniden açmaz (açsaydı kayıtta, listede ve
    boşta kapatmada görünmeyen bir runtime kredi yakardı) ve varsayılan oturuma da kaymaz."""
    manager = two_sessions
    release = asyncio.Event()
    starts = []

    async def fake_run(job, session=None):
        await release.wait()
        raise RuntimeError("ComfyUI bağlantısı koptu")

    async def fake_stop_runtime(s):
        return None

    async def fake_start(s=None):
        starts.append(s.name if s else DEFAULT)

    monkeypatch.setattr(studio, "_run", fake_run)
    monkeypatch.setattr(runtime, "_stop_runtime_of", fake_stop_runtime)
    monkeypatch.setattr(runtime, "_start", fake_start)
    task = asyncio.create_task(studio.worker())
    try:
        first = studio.create_job("t", "image", "image", "a", IMAGE, [], session=SECOND)
        waiting = studio.create_job("t", "image", "image", "b", IMAGE, [], session=SECOND)
        for _ in range(100):
            if studio.get_job(first["id"])["status"] == "running":
                break
            await asyncio.sleep(0.02)
        await manager.stop(SECOND, "elle")
        release.set()
        await _wait_status(first["id"])
        job = await _wait_status(waiting["id"])
        late = studio.create_job("t", "image", "image", "c", IMAGE, [], session=SECOND)
        late_job = await _wait_status(late["id"])
    finally:
        task.cancel()
    assert starts == []
    assert job["error"] == studio.SESSION_CLOSED and late_job["error"] == studio.SESSION_CLOSED


async def test_closing_a_session_cancels_its_waiting_jobs(client, two_sessions, monkeypatch):
    """Oturumu kapat: o oturumun bekleyen işleri iptal edilir, başka oturumunkiler kalır."""
    mine = studio.create_job("t", "image", "image", "a", IMAGE, [], session=SECOND)
    other = studio.create_job("t", "image", "image", "b", IMAGE, [], session=DEFAULT)

    async def fake_stop(self, name, reason="elle kapatıldı"):
        return None

    monkeypatch.setattr(sessions.Manager, "stop", fake_stop)
    response = await client.post(f"/api/sessions/{SECOND}/stop")
    assert response.status == 200
    assert studio.get_job(mine["id"])["error"] == studio.CANCELLED
    assert studio.get_job(other["id"])["status"] == "queued"


async def test_automatic_jobs_spread_over_the_open_sessions(two_sessions, monkeypatch):
    """Otomatik yönlendirilen işler açık oturumlara dağılır: oturumu kapıda beklerken de yazılı olmalı,
    yoksa bekleyen işler varsayılan oturumun yükü sayılıp hepsi aynı ek oturuma yığılıyordu."""
    gate = asyncio.Event()

    async def fake_run(job, session=None):
        await gate.wait()

    monkeypatch.setattr(studio, "_run", fake_run)
    task = asyncio.create_task(studio.worker())
    try:
        ids = []
        for i in range(6):
            ids.append(studio.create_job("t", "image", "image", str(i), IMAGE, [])["id"])
            await asyncio.sleep(0.05)
        gate.set()
        for jid in ids:
            await _wait_status(jid)
    finally:
        task.cancel()
    names = [studio.get_job(j)["session"] for j in ids]
    assert names.count(DEFAULT) == 3 and names.count(SECOND) == 3, names
