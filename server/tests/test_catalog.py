"""Model kataloğu (models.toml): varsayılan dosya geçerli, hatalı dosya açık bir mesajla reddedilir."""
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "station"))
import catalog  # noqa: E402

DEFAULT = ROOT / "station" / "models.default.toml"


def _data():
    return tomllib.loads(DEFAULT.read_text())


def test_default_catalog_fills_every_slot():
    c = catalog.load(DEFAULT)
    assert set(c) == set(catalog.SLOTS)
    for mode, slots in catalog.SLOTS.items():
        assert set(c[mode]["files"]) == set(slots)
        assert c[mode]["label"] and c[mode]["description"]["en"] and c[mode]["description"]["tr"]


def test_missing_slot_is_rejected():
    data = _data()
    del data["image"]["files"]["vae"]
    with pytest.raises(catalog.CatalogError, match=r"\[image.files\] missing slot\(s\): vae"):
        catalog.parse(data)


def test_unknown_slot_and_mode_are_rejected():
    data = _data()
    data["image"]["files"]["refiner"] = {"repo": "a/b", "file": "x.safetensors", "dir": "loras"}
    with pytest.raises(catalog.CatalogError, match="unknown slot"):
        catalog.parse(data)
    data = _data()
    data["upscale"] = {}
    with pytest.raises(catalog.CatalogError, match="unknown mode"):
        catalog.parse(data)


def test_typos_in_keys_are_rejected():
    data = _data()
    data["image"]["labell"] = data["image"].pop("label")
    with pytest.raises(catalog.CatalogError, match=r"\[image\] unknown key\(s\): labell"):
        catalog.parse(data)
    data = _data()
    data["music"]["files"]["checkpoint"]["fiel"] = "x"
    with pytest.raises(catalog.CatalogError, match=r"unknown key\(s\): fiel"):
        catalog.parse(data)
    data = _data()
    data["transcribe"] = {}
    with pytest.raises(catalog.CatalogError, match="Whisper model is downloaded by the ComfyUI plugin"):
        catalog.parse(data)


def test_missing_file_is_a_catalog_error(tmp_path):
    with pytest.raises(catalog.CatalogError, match=r"cannot read the model catalog.*STUDIO_MODELS"):
        catalog.load(tmp_path / "nope.toml")


@pytest.mark.parametrize("entry, message", [
    ({"repo": "a/b", "file": "x"}, "missing dir"),
    ({"repo": "nope", "file": "x", "dir": "vae"}, "owner/name"),
    ({"repo": "a/b", "file": "x", "dir": "../etc"}, "relative folder"),
    ({"repo": "a/b", "file": "x", "dir": "/abs"}, "relative folder"),
    ({"repo": "a/b", "file": "../x", "dir": "vae"}, "inside the repository"),
    ({"repo": "a/b", "file": "/x", "dir": "vae"}, "inside the repository"),
])
def test_bad_entries_are_rejected(entry, message):
    data = _data()
    data["music"]["files"]["checkpoint"] = entry
    with pytest.raises(catalog.CatalogError, match=message):
        catalog.parse(data)


def test_replacing_a_model_changes_the_workflow_file(tmp_path):
    text = DEFAULT.read_text().replace("NoctQ_V4_int8_convrot.safetensors", "diffusion_models/qwen_image_2.1_int8_convrot.safetensors")
    path = tmp_path / "models.toml"
    path.write_text(text)
    c = catalog.load(path)
    assert catalog.filename(c, "image", "diffusion_model") == "qwen_image_2.1_int8_convrot.safetensors"


def test_toml_syntax_error_names_the_file(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text("[image\n")
    with pytest.raises(catalog.CatalogError, match=r"models\.toml"):
        catalog.load(path)


async def test_models_endpoint_lists_labels(client):
    r = await client.get("/api/models")
    assert r.status == 200
    body = await r.json()
    assert body["image"]["label"] == "Noct-Q V4"
    assert body["video"]["description"]["tr"]
