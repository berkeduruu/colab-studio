"""Testler GPU ve Colab olmadan çalışır: veri dizini geçici, runtime (Colab) hiç açılmaz."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.update(
    STUDIO_ENV_FILE="/nonexistent",  # geliştiricinin .env'i testlere sızmasın
    STUDIO_DATA=tempfile.mkdtemp(prefix="studio-test-"),
    STUDIO_IDLE_MINUTES="15",
    STUDIO_PORT="18190",  # yalnızca ayar olarak okunur; testler gerçek bir porta bağlanmaz
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module
import runtime
import studio


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Her test boş bir veritabanı ve medya klasörüyle başlar."""
    monkeypatch.setattr(studio, "DATA_DIR", tmp_path)
    monkeypatch.setattr(studio, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(studio, "DB_PATH", tmp_path / "studio.db")
    monkeypatch.setattr(runtime, "DATA_DIR", tmp_path)
    monkeypatch.setattr(runtime, "DURATIONS_FILE", tmp_path / "startup_durations.json")
    monkeypatch.setattr(runtime, "OWNED_FILE", tmp_path / "owned_endpoints.json")
    return tmp_path


@pytest.fixture
async def db(data_dir, monkeypatch):
    # Kuyruk ve olay nesneleri testin kendi döngüsünde oluşturulur.
    monkeypatch.setattr(runtime, "preflight", lambda: None)  # testlerde colab CLI yok
    studio.queue = asyncio.PriorityQueue()
    runtime.ready = asyncio.Event()
    runtime.state.update(phase="off", detail="", startup=None, credits=None, start_balance=None, ready_at=None, last_busy=None)
    runtime.local_modes.clear()
    runtime.warm.clear()
    runtime.packs.clear()
    runtime._pack_samples.clear()
    studio.live.clear()
    studio.init()
    yield studio.db
    studio.db.close()


@pytest.fixture
async def client(db, aiohttp_client):
    """Servisin API'si; açılıştaki runtime kapatma ve arka plan döngüleri çalışmaz."""
    app = app_module.create_app()
    app.on_startup.clear()
    app.on_shutdown.clear()
    return await aiohttp_client(app)


@pytest.fixture
async def upload(client):
    async def do(name="ref.png", data=b"\x89PNG fake", content_type="image/png"):
        from aiohttp import FormData

        form = FormData()
        form.add_field("file", data, filename=name, content_type=content_type)
        r = await client.post("/api/uploads", data=form)
        assert r.status == 200, await r.text()
        return (await r.json())["name"]

    return do
