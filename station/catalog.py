"""Model catalog (models.toml): which Hugging Face files each mode downloads and which file fills
each slot of its ComfyUI workflow.

Shared by the server (workflows use the file names) and the GPU runtime (station/models.py
downloads the files). Standard library only; Python 3.11+ (tomllib).

Format (see models.default.toml):

    [image]
    label = "Noct-Q V4"                     # shown in the UI
    description = { en = "...", tr = "..." } # optional, shown in the UI (or a plain string)
    [image.files]
    diffusion_model = { repo = "owner/name", file = "path/in/repo.safetensors", dir = "diffusion_models" }
    ...
    [[image.extra]]                          # optional: more files to download, not tied to a slot
    repo = "owner/name"
    file = "config.json"
    dir = "some/folder"
"""
import tomllib
from pathlib import Path

# mode -> workflow slots that must be filled. A ComfyUI workflow loads exactly these files, so a
# replacement must be the same architecture (e.g. another Qwen-Image 2.1 fine-tune for image).
SLOTS = {
    "image": ["diffusion_model", "text_encoder", "vae", "turbo_lora"],
    "enhance": ["prompt_enhancer_t2i", "prompt_enhancer_i2i"],
    "video": ["diffusion_model", "text_encoder", "video_vae", "audio_vae", "turbo_lora"],
    "music": ["checkpoint"],
    "model3d": ["diffusion_model", "clip_vision", "shape_vae", "texture_vae", "background_removal"],
    "speech": [],
}


class CatalogError(ValueError):
    pass


def _entry(mode, where, value):
    if not isinstance(value, dict):
        raise CatalogError(f"{mode}.{where}: expected {{ repo = ..., file = ..., dir = ... }}")
    unknown = set(value) - {"repo", "file", "dir"}
    if unknown:
        raise CatalogError(f"{mode}.{where}: unknown key(s): {', '.join(sorted(unknown))} (expected: repo, file, dir)")
    missing =[k for k in ("repo", "file", "dir") if not isinstance(value.get(k), str) or not value[k].strip()]
    if missing:
        raise CatalogError(f"{mode}.{where}: missing {', '.join(missing)}")
    if value["repo"].count("/") != 1:
        raise CatalogError(f"{mode}.{where}: repo must look like 'owner/name', got {value['repo']!r}")
    if value["dir"].startswith("/") or ".." in Path(value["dir"]).parts:
        raise CatalogError(f"{mode}.{where}: dir must be a relative folder under ComfyUI/models")
    return (value["repo"], value["file"], value["dir"])


def _text(mode, value):
    """description: a string, or a table per UI language ({ en = "...", tr = "..." })."""
    if isinstance(value, str):
        return {"en": value.strip()} if value.strip() else {}
    if isinstance(value, dict) and all(isinstance(v, str) for v in value.values()):
        return {k: v.strip() for k, v in value.items() if v.strip()}
    raise CatalogError(f"{mode}.description must be a string or a table like {{ en = \"...\", tr = \"...\" }}")


def parse(data):
    """Validated catalog: {mode: {"label", "description", "files": {slot: (repo, file, dir)}, "pack": [...]}}."""
    unknown = set(data) - set(SLOTS)
    if unknown:
        hint = " (transcribe needs no entry: its Whisper model is downloaded by the ComfyUI plugin)" if "transcribe" in unknown else ""
        raise CatalogError(f"unknown mode(s): {', '.join(sorted(unknown))} (known: {', '.join(SLOTS)}){hint}")
    catalog = {}
    for mode, slots in SLOTS.items():
        section = data.get(mode)
        if not isinstance(section, dict):
            raise CatalogError(f"[{mode}] section is missing")
        typos = set(section) - {"label", "description", "files", "extra"}
        if typos:
            raise CatalogError(f"[{mode}] unknown key(s): {', '.join(sorted(typos))} (expected: label, description, files, extra)")
        files = section.get("files", {})
        if not isinstance(files, dict):
            raise CatalogError(f"[{mode}.files] must be a table")
        missing = [s for s in slots if s not in files]
        if missing:
            raise CatalogError(f"[{mode}.files] missing slot(s): {', '.join(missing)}")
        extra_slots = set(files) - set(slots)
        if extra_slots:
            raise CatalogError(f"[{mode}.files] unknown slot(s): {', '.join(sorted(extra_slots))} (expected: {', '.join(slots) or 'none, use [[' + mode + '.extra]]'})")
        resolved = {slot: _entry(mode, f"files.{slot}", files[slot]) for slot in slots}
        extra = section.get("extra", [])
        if not isinstance(extra, list):
            raise CatalogError(f"{mode}.extra must be an array of tables ([[{mode}.extra]])")
        pack = list(resolved.values()) + [_entry(mode, f"extra[{i}]", e) for i, e in enumerate(extra)]
        if not pack:
            raise CatalogError(f"[{mode}] downloads no files")
        catalog[mode] = {
            "label": str(section.get("label", "")).strip() or mode,
            "description": _text(mode, section.get("description", "")),
            "files": resolved,
            "pack": pack,
        }
    return catalog


def load(path):
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except OSError as e:
        raise CatalogError(f"{path}: cannot read the model catalog ({e.strerror}); check STUDIO_MODELS in .env") from None
    except tomllib.TOMLDecodeError as e:
        raise CatalogError(f"{path}: {e}") from None
    try:
        return parse(data)
    except CatalogError as e:
        raise CatalogError(f"{path}: {e}") from None


def filename(catalog, mode, slot):
    """Name ComfyUI sees: the file's base name inside its models/<dir> folder."""
    return Path(catalog[mode]["files"][slot][1]).name


def packs(catalog):
    """mode -> [(repo, file, dir)] in download order."""
    return {mode: entry["pack"] for mode, entry in catalog.items()}
