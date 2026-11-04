"""API sözleşmesi: geçerli istekler kabul edilir, bozuk istekler 400/404 alır ve hiçbir girdi 500
hatasına yol açmaz."""
import asyncio

import pytest

import runtime
import studio

IMAGE = {"ratio": "2:3", "size": "1.5K", "count": 1, "steps": 25, "cfg": 3, "sampler": "euler", "scheduler": "simple", "seed": None, "negative": "blurry"}
VIDEO = {"quality": "480p", "ratio": "16:9", "seconds": 3, "turbo": True, "seed": None}
MUSIC = {"lyrics": "[Verse]\nla la", "seconds": 30, "seed": 1}
MODEL3D = {"resolution": 1536, "texture": "pbr", "faces": 200000, "seed": None}
SPEECH = {"engine": "chatterbox", "language": "Turkish", "voice_preset": "Vivian", "instruct": "", "reference_text": "",
          "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "seed": None}


async def create(client, **body):
    body = {"thread_id": None, "prompt": "test", "inputs": [], **body}
    return await client.post("/api/jobs", json=body)


async def test_image_job_is_queued_with_derived_size(client):
    r = await create(client, kind="image", params=IMAGE)
    assert r.status == 200
    job = await r.json()
    assert job["status"] == "queued"
    assert (job["params"]["width"], job["params"]["height"]) == (1024, 1536)
    assert job["thread_id"]
    assert studio.queue_info()["order"] == [job["id"]]


@pytest.mark.parametrize("ratio", ["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"])
@pytest.mark.parametrize("size", ["1K", "1.5K", "2K"])
async def test_image_sizes_are_multiples_of_32(client, ratio, size):
    job = await (await create(client, kind="image", params={**IMAGE, "ratio": ratio, "size": size})).json()
    assert job["params"]["width"] % 32 == 0 and job["params"]["height"] % 32 == 0


async def test_image_edit_with_uploaded_reference(client, upload):
    name = await upload()
    r = await create(client, kind="image", params=IMAGE, inputs=[name])
    assert r.status == 200
    job = await r.json()
    assert job["inputs"] == [name]
    assert (job["params"]["edit_resolution"], job["params"]["edit_pixels"]) == ("1080p", 1440)
    job = await (await create(client, kind="image", params={**IMAGE, "edit_resolution": "720p"}, inputs=[name])).json()
    assert job["params"]["edit_pixels"] == 960


async def test_video_music_speech_transcribe_jobs(client, upload):
    img = await upload()
    voice = await upload("voice.mp3", b"ID3 fake", "audio/mpeg")
    for kind, params, inputs in [
        ("video", VIDEO, [img]),
        ("music", MUSIC, []),
        ("speech", SPEECH, []),
        ("speech", {**SPEECH, "engine": "qwen3", "language": "English"}, [voice]),
        ("transcribe", {"language": "Turkish"}, [voice]),
        ("model3d", MODEL3D, [img]),
        ("model3d", {"resolution": 1024, "texture": "vertex", "faces": 50000, "seed": 5}, [img]),
    ]:
        r = await create(client, kind=kind, params=params, inputs=inputs)
        assert r.status == 200, (kind, await r.text())
        assert (await r.json())["kind"] == kind


async def test_transcribe_without_prompt_gets_default_title(client, upload):
    voice = await upload("voice.mp3", b"ID3", "audio/mpeg")
    job = await (await create(client, kind="transcribe", prompt="", params={}, inputs=[voice])).json()
    assert job["prompt"] == "Ses kaydı"


async def test_model3d_without_prompt_gets_default_title(client, upload):
    img = await upload()
    job = await (await create(client, kind="model3d", prompt="", params={}, inputs=[img])).json()
    assert (job["prompt"], job["mode"]) == ("3D model", "model3d")
    assert job["params"] == {"resolution": 1024, "texture": "pbr", "faces": 200000, "seed": None}


async def test_previous_output_can_be_an_input(client, data_dir):
    """Görsel sonucu doğrudan 3D'ye (ya da düzenlemeye) verilebilir."""
    (data_dir / "media").mkdir(exist_ok=True)
    (data_dir / "media" / "abc_0.png").write_bytes(b"png")
    r = await create(client, kind="model3d", params=MODEL3D, inputs=["abc_0.png"])
    assert r.status == 200, await r.text()


async def test_enhance_job_has_no_thread(client):
    job = await (await create(client, kind="enhance")).json()
    assert job["thread_id"] is None
    assert job["mode"] == "image"


async def test_job_joins_existing_thread(client):
    first = await (await create(client, kind="image", params=IMAGE)).json()
    second = await (await create(client, kind="image", params=IMAGE, thread_id=first["thread_id"])).json()
    assert second["thread_id"] == first["thread_id"]
    thread = await (await client.get(f"/api/threads/{first['thread_id']}")).json()
    assert [j["id"] for j in thread["jobs"]] == [first["id"], second["id"]]


BAD_JOBS = [
    {"kind": "image", "prompt": ""},
    {"kind": "image", "prompt": "   "},
    {"kind": "nope"},
    {"kind": None},
    {"kind": "image", "params": "x"},
    {"kind": "image", "params": [1, 2]},
    {"kind": "image", "params": {**IMAGE, "ratio": "5:1"}},
    {"kind": "image", "params": {**IMAGE, "size": "8K"}},
    {"kind": "image", "params": {**IMAGE, "count": 5}},
    {"kind": "image", "params": {**IMAGE, "count": "çok"}},
    {"kind": "image", "params": {**IMAGE, "steps": 0}},
    {"kind": "image", "params": {**IMAGE, "steps": None}},
    {"kind": "image", "params": {**IMAGE, "cfg": 11}},
    {"kind": "image", "params": {**IMAGE, "cfg": "abc"}},
    {"kind": "image", "params": {**IMAGE, "seed": -1}},
    {"kind": "image", "params": {**IMAGE, "seed": 2**32}},
    {"kind": "image", "params": {**IMAGE, "seed": "12"}},
    {"kind": "image", "params": {**IMAGE, "seed": True}},
    {"kind": "image", "params": {**IMAGE, "seed": 1.5}},
    {"kind": "image", "params": {**IMAGE, "sampler": "magic"}},
    {"kind": "image", "params": {**IMAGE, "scheduler": "magic"}},
    {"kind": "image", "params": {**IMAGE, "edit_resolution": "8K"}},
    {"kind": "image", "params": IMAGE, "inputs": "a.png"},
    {"kind": "image", "params": IMAGE, "inputs": [1]},
    {"kind": "image", "params": IMAGE, "inputs": ["uploads/yok.png"]},
    {"kind": "image", "params": IMAGE, "inputs": [f"uploads/{i}.png" for i in range(11)]},
    {"kind": "video", "params": {**VIDEO, "quality": "4k"}},
    {"kind": "video", "params": {**VIDEO, "ratio": "4:3"}},
    {"kind": "video", "params": {**VIDEO, "seconds": 1}},
    {"kind": "video", "params": {**VIDEO, "seconds": 11}},
    {"kind": "video", "params": {**VIDEO, "seconds": "uzun"}},
    {"kind": "music", "params": {**MUSIC, "seconds": 5}},
    {"kind": "music", "params": {**MUSIC, "seconds": 400}},
    {"kind": "speech", "params": {**SPEECH, "engine": "bark"}},
    {"kind": "speech", "params": {**SPEECH, "engine": "qwen3", "language": "Turkish"}},
    {"kind": "speech", "params": {**SPEECH, "voice_preset": "Nobody"}},
    {"kind": "speech", "params": {**SPEECH, "exaggeration": 3}},
    {"kind": "speech", "params": {**SPEECH, "cfg_weight": -0.1}},
    {"kind": "speech", "params": {**SPEECH, "temperature": 0}},
    {"kind": "speech", "params": {**SPEECH, "temperature": {}}},
    {"kind": "transcribe", "params": {}},
    {"kind": "transcribe", "params": {}, "inputs": ["a.png"]},
    {"kind": "model3d", "params": MODEL3D},
    {"kind": "model3d", "params": {**MODEL3D, "resolution": 512}},
    {"kind": "model3d", "params": {**MODEL3D, "resolution": "1536"}},
    {"kind": "model3d", "params": {**MODEL3D, "texture": "gold"}},
    {"kind": "model3d", "params": {**MODEL3D, "faces": 10}},
    {"kind": "model3d", "params": {**MODEL3D, "faces": 5_000_000}},
    {"kind": "model3d", "params": {**MODEL3D, "seed": -3}},
    {"kind": "image", "params": IMAGE, "inputs": ["../studio.db"]},
    {"kind": "image", "params": IMAGE, "inputs": ["/etc/hostname"]},
]


@pytest.mark.parametrize("body", BAD_JOBS)
async def test_bad_job_requests_are_400(client, body):
    r = await create(client, **body)
    assert r.status == 400, await r.text()
    assert (await r.text()).strip()


async def test_input_kind_rules(client, upload):
    img = await upload()
    voice = await upload("v.mp3", b"ID3", "audio/mpeg")
    cases = [
        ("video", VIDEO, [img, img, img]),
        ("music", MUSIC, [img]),
        ("speech", SPEECH, [img]),
        ("speech", SPEECH, [voice, voice]),
        ("transcribe", {}, [img]),
        ("transcribe", {}, [voice, voice]),
        ("model3d", MODEL3D, [voice]),
        ("model3d", MODEL3D, [img, img]),
    ]
    for kind, params, inputs in cases:
        r = await create(client, kind=kind, params=params, inputs=inputs)
        assert r.status == 400, (kind, inputs)


async def test_transcribe_language_must_be_known(client, upload):
    voice = await upload("v.mp3", b"ID3", "audio/mpeg")
    assert (await create(client, kind="transcribe", params={"language": "Klingon"}, inputs=[voice])).status == 400
    assert (await create(client, kind="transcribe", params={"language": "auto"}, inputs=[voice])).status == 200


async def test_unknown_thread_is_404(client):
    r = await create(client, kind="image", params=IMAGE, thread_id="yok")
    assert r.status == 404


@pytest.mark.parametrize("raw", [b"", b"{", b"null", b"[]", b'"x"', b"42"])
async def test_malformed_json_bodies_are_400(client, raw):
    for path in ("/api/jobs", "/api/presets"):
        r = await client.post(path, data=raw, headers={"Content-Type": "application/json"})
        assert r.status == 400, (path, raw, await r.text())


async def test_unknown_job_and_thread_are_404(client):
    assert (await client.get("/api/jobs/yok")).status == 404
    assert (await client.get("/api/threads/yok")).status == 404


async def test_threads_listing_accepts_missing_mode_and_rejects_unknown(client):
    assert (await client.get("/api/threads")).status == 200  # modsuz: tüm modlar
    assert (await client.get("/api/threads?mode=foo")).status == 400
    await create(client, kind="image", params=IMAGE, prompt="kahve fincanı")
    threads = await (await client.get("/api/threads?mode=image")).json()
    assert [t["title"] for t in threads] == ["kahve fincanı"]
    assert await (await client.get("/api/threads?mode=video")).json() == []


async def test_thread_delete_removes_jobs_outputs_and_references(client, upload):
    ref = await upload()
    job = await (await create(client, kind="image", params=IMAGE, inputs=[ref])).json()
    out = studio.MEDIA_DIR / f"{job['id']}_0.png"
    out.write_bytes(b"png")
    studio._update(job["id"], status="done", outputs=f'["{out.name}"]')
    assert (await client.delete(f"/api/threads/{job['thread_id']}")).status == 200
    assert not out.exists()
    assert not (studio.MEDIA_DIR / ref).exists()
    assert (await client.get(f"/api/jobs/{job['id']}")).status == 404


async def test_thread_delete_keeps_output_used_by_another_thread(client):
    # "Bu görselden 3D model yap": görselin çıktısı başka sohbetteki 3D işinin girdisi olur.
    img = await (await create(client, kind="image", params=IMAGE)).json()
    out = studio.MEDIA_DIR / f"{img['id']}_0.png"
    out.write_bytes(b"png")
    studio._update(img["id"], status="done", outputs=f'["{out.name}"]')
    m3d = await (await create(client, kind="model3d", prompt="", params=MODEL3D, inputs=[out.name])).json()
    assert m3d["thread_id"] != img["thread_id"]
    assert (await client.delete(f"/api/threads/{img['thread_id']}")).status == 200
    assert out.exists()


async def test_upload_types(client, upload):
    from aiohttp import FormData

    assert (await upload("a.webp", b"x", "image/webp")).endswith(".webp")
    assert (await upload("a.m4a", b"x", "audio/mp4")).endswith(".m4a")
    form = FormData()
    form.add_field("file", b"x", filename="a.txt", content_type="text/plain")
    assert (await client.post("/api/uploads", data=form)).status == 400
    form = FormData()
    form.add_field("other", b"x", filename="a.png", content_type="image/png")
    assert (await client.post("/api/uploads", data=form)).status == 400


async def test_uploaded_file_is_served(client, upload):
    name = await upload(data=b"PNGDATA")
    r = await client.get(f"/media/{name}")
    assert r.status == 200 and await r.read() == b"PNGDATA"


@pytest.mark.parametrize("mode", ["image", "video", "model3d", "music", "speech", "transcribe"])
async def test_every_mode_can_save_its_params(client, mode):
    """Arayüz her modun ayarını `<mod>.params` anahtarıyla kaydeder; sunucu hiçbirini reddetmemeli."""
    import app

    assert mode in app.MODES
    r = await client.put(f"/api/settings/{mode}.params", json={"x": 1})
    assert r.status == 200, await r.text()
    assert (await (await client.get("/api/settings")).json())[f"{mode}.params"] == {"x": 1}


async def test_settings_roundtrip(client):
    assert await (await client.get("/api/settings")).json() == {}
    assert (await client.put("/api/settings/image.params", json={**IMAGE, "negative": "ugly"})).status == 200
    assert (await client.put("/api/settings/mode", json="video")).status == 200
    s = await (await client.get("/api/settings")).json()
    assert s["image.params"]["negative"] == "ugly" and s["mode"] == "video"
    assert (await client.put("/api/settings/bilinmeyen", json=1)).status == 400
    assert (await client.put("/api/settings/mode", data=b"{", headers={"Content-Type": "application/json"})).status == 400


async def test_presets(client):
    r = await client.post("/api/presets", json={"mode": "image", "name": " Portre ", "params": IMAGE})
    pid = (await r.json())["id"]
    presets = await (await client.get("/api/presets?mode=image")).json()
    assert [(p["name"], p["params"]["steps"]) for p in presets] == [("Portre", 25)]
    assert (await client.post("/api/presets", json={"mode": "image", "name": "", "params": IMAGE})).status == 400
    assert (await client.post("/api/presets", json={"mode": "x", "name": "a", "params": IMAGE})).status == 400
    assert (await client.post("/api/presets", json={"mode": "image", "name": "a", "params": 3})).status == 400
    assert sorted(p["name"] for p in await (await client.get("/api/presets")).json()) == ["Portre"]  # modsuz: hepsi
    assert (await client.delete(f"/api/presets/{pid}")).status == 200
    assert await (await client.get("/api/presets?mode=image")).json() == []


async def test_runtime_state_shape(client):
    s = await (await client.get("/api/runtime")).json()
    assert s["phase"] == "off"
    assert s["queue"] == {"order": [], "current": None, "starts_in": {}}
    assert {"credits", "credit_stats", "startup", "auto_shutdown", "idle_minutes", "gpu"} <= s.keys()


async def test_toggle_auto_shutdown(client):
    before = runtime.state["auto_shutdown"]
    s = await (await client.post("/api/runtime/toggle-auto")).json()
    assert s["auto_shutdown"] is (not before)
    await client.post("/api/runtime/toggle-auto")


async def test_spa_routes_serve_index(client):
    for path in ("/", "/video", "/herhangi/bir/yol"):
        r = await client.get(path)
        assert r.status == 200 and "<div id=\"root\">" in await r.text()
    assert (await client.get("/api/yok")).status == 404


async def test_cancel_endpoint(client):
    job = await (await create(client, kind="image", params=IMAGE)).json()
    r = await client.post(f"/api/jobs/{job['id']}/cancel")
    assert r.status == 200
    assert (await r.json())["error"] == studio.CANCELLED
    assert studio.queue_info()["order"] == []
    assert (await client.post("/api/jobs/yok/cancel")).status == 404


async def test_jobs_that_surely_exceed_30_minutes_are_rejected(client, upload):
    """Ölçülen süreleri 30 dk'yı aşan işler kabul edilmez; Turbo önerilir. Aşmayanlar kabul edilir."""
    img = await upload()
    slow_video = {**VIDEO, "quality": "768p", "seconds": 10, "turbo": False}
    r = await create(client, kind="video", params=slow_video)
    assert r.status == 400 and "Turbo" in await r.text()
    # 9 sn de aşar (L4: 20 adım örnekleme ~29 dk + diğer düğümler); 8 sn ~25 dk ile sığar.
    assert (await create(client, kind="video", params={**slow_video, "seconds": 9})).status == 400
    for ok in ({**slow_video, "turbo": True}, {**slow_video, "seconds": 8}, {**slow_video, "quality": "480p"}):
        assert (await create(client, kind="video", params=ok)).status == 200

    edit4k = {**IMAGE, "edit_resolution": "2160p"}
    r = await create(client, kind="image", params=edit4k, inputs=[img])
    assert r.status == 400 and "Turbo" in await r.text()
    assert (await create(client, kind="image", params={**edit4k, "cfg": 1, "turbo": True, "steps": 8}, inputs=[img])).status == 200
    assert (await create(client, kind="image", params={**edit4k, "edit_resolution": "1440p"}, inputs=[img])).status == 200
    # Sıfırdan üretimde (referanssız) 2160p ayarı etkisizdir.
    assert (await create(client, kind="image", params=edit4k)).status == 200


@pytest.mark.parametrize("method,path", [("post", "/api/runtime/start"), ("post", "/api/jobs"), ("put", "/api/settings/mode"), ("delete", "/api/threads/x")])
@pytest.mark.parametrize("headers", [{"Origin": "https://kotu-site.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}])
async def test_cross_site_writes_are_forbidden(client, method, path, headers):
    r = await getattr(client, method)(path, headers=headers, json={})
    assert r.status == 403


async def test_same_origin_and_non_browser_writes_pass_and_reads_are_never_blocked(client):
    own = f"http://{client.host}:{client.port}"
    for headers in ({}, {"Origin": own}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}):
        r = await client.put("/api/settings/mode", headers=headers, json="image")
        assert r.status == 200, headers
    # Okuma istekleri (GET) başka siteden de yanıtlanır; kural yalnızca yazma isteklerine uygulanır.
    r = await client.get("/api/settings", headers={"Origin": "https://kotu-site.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status == 200


@pytest.mark.parametrize("host", ["kotu-site.example", "kotu-site.example:8190", "127.0.0.1.kotu-site.example", "localhost.kotu-site.example"])
@pytest.mark.parametrize("method,path", [("get", "/api/threads"), ("get", "/"), ("get", "/media/x.png"), ("post", "/api/runtime/start")])
async def test_unknown_host_names_are_refused_dns_rebinding(client, host, method, path):
    """DNS rebinding: saldırganın alan adı 127.0.0.1'e çevrilir, tarayıcı isteği "aynı site" sayar
    (Origin ve Sec-Fetch-Site aynı-köken görünür). Host başlığı tanınmadığı için okuma da yazma da reddedilir."""
    headers = {"Host": host, "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"}
    r = await getattr(client, method)(path, headers=headers)
    assert r.status == 403
    assert "STUDIO_ALLOWED_HOSTS" in await r.text()


@pytest.mark.parametrize("host", ["127.0.0.1:8190", "localhost:8190", "LOCALHOST", "studio.localhost", "[::1]:8190", "192.168.1.20:8190", "10.0.0.5"])
async def test_ip_and_localhost_hosts_are_allowed(client, host):
    r = await client.get("/api/settings", headers={"Host": host})
    assert r.status == 200, host


async def test_allowed_hosts_setting_lets_a_domain_in(client, monkeypatch):
    import config

    monkeypatch.setattr(config, "ALLOWED_HOSTS", {"studio.example.com"})
    assert (await client.get("/api/settings", headers={"Host": "Studio.Example.com"})).status == 200
    assert (await client.get("/api/settings", headers={"Host": "other.example.com"})).status == 403
    monkeypatch.setattr(config, "ALLOWED_HOSTS", {"*"})
    assert (await client.get("/api/settings", headers={"Host": "other.example.com"})).status == 200


async def test_presets_listing_without_mode_returns_all(client):
    await client.post("/api/presets", json={"mode": "image", "name": "a", "params": {"steps": 10}})
    await client.post("/api/presets", json={"mode": "video", "name": "b", "params": {"seconds": 5}})
    all_presets = await (await client.get("/api/presets")).json()
    assert sorted(p["name"] for p in all_presets) == ["a", "b"]
    assert [p["name"] for p in await (await client.get("/api/presets?mode=video")).json()] == ["b"]


async def test_gpu_stop_returns_immediately_and_shuts_down_in_background(client, monkeypatch):
    """Kapanış 3 dakikaya kadar sürebilir; istek nginx'in 120 sn zaman aşımına takılmasın diye hemen
    döner, faz hemen "stopping" olur ve kapanış arka planda yürür."""
    reasons, started, release = [], asyncio.Event(), asyncio.Event()

    async def fake_run_stop(reason):
        reasons.append(reason)
        started.set()
        await release.wait()

    monkeypatch.setattr(runtime, "run_stop", fake_run_stop)
    runtime.set_phase("ready", "GPU hazır")
    r = await asyncio.wait_for(client.post("/api/runtime/stop"), 5)
    assert r.status == 200 and (await r.json())["phase"] == "stopping"
    await asyncio.wait_for(started.wait(), 5)
    assert reasons == ["elle kapatıldı"]
    # Kapanış sürerken ikinci basış yeni bir kapanış başlatmaz.
    assert (await client.post("/api/runtime/stop")).status == 200
    assert reasons == ["elle kapatıldı"]
    release.set()
    await asyncio.sleep(0.05)
    runtime.set_phase("off")
