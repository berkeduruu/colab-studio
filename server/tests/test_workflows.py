"""ComfyUI grafikleri: her bağlantı var olan bir düğüme gider, sonuç düğümleri doğru türde ve
modeller runtime'da indirilen dosyalarla aynı adı taşır."""
import re
from pathlib import Path

import pytest

import workflows

IMAGE = {"prompt": "kahve", "negative": "blurry", "width": 1024, "height": 1536, "steps": 25, "cfg": 3.0,
         "seed": 7, "sampler": "euler", "scheduler": "simple", "edit_pixels": 1440}
VIDEO = {"prompt": "tramvay", "width": 864, "height": 480, "seconds": 3, "turbo": True, "seed": 1}
MODEL3D = {"resolution": 1536, "texture": "pbr", "faces": 200000, "seed": 3}
SPEECH = {"text": "Merhaba", "engine": "chatterbox", "language": "Turkish", "voice_preset": "Vivian", "instruct": "",
          "reference_text": "", "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "seed": 1}


def assert_links_valid(wf):
    for node_id, node in wf.items():
        assert "class_type" in node and "inputs" in node, node_id
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                assert value[0] in wf, f"{node_id}.{key} -> {value[0]} yok"


GRAPHS = {
    "image": lambda: workflows.image_generate(IMAGE),
    "image_turbo": lambda: workflows.image_generate({**IMAGE, "turbo": True, "steps": 8, "cfg": 1.0}),
    "image_edit_1": lambda: workflows.image_edit(IMAGE, ["a.png"]),
    "image_edit_3": lambda: workflows.image_edit(IMAGE, ["a.png", "b.png", "c.png"]),
    "image_edit_turbo": lambda: workflows.image_edit({**IMAGE, "turbo": True, "steps": 8, "cfg": 1.0}, ["a.png"]),
    "enhance": lambda: workflows.enhance_prompt("kahve", [], 1),
    "enhance_edit": lambda: workflows.enhance_prompt("kırmızı yap", ["a.png"], 1),
    "video": lambda: workflows.video_generate(VIDEO, []),
    "video_first": lambda: workflows.video_generate({**VIDEO, "turbo": False}, ["a.png"]),
    "video_first_last": lambda: workflows.video_generate(VIDEO, ["a.png", "b.png"]),
    "music": lambda: workflows.music_generate({"style": "lo-fi", "lyrics": "", "seconds": 30, "seed": 1}),
    "speech": lambda: workflows.speech_generate(SPEECH, None),
    "speech_clone": lambda: workflows.speech_generate(SPEECH, "voice.mp3"),
    "qwen3_preset": lambda: workflows.speech_generate({**SPEECH, "engine": "qwen3", "language": "English"}, None),
    "qwen3_clone": lambda: workflows.speech_generate({**SPEECH, "engine": "qwen3", "language": "English", "reference_text": "hi"}, "v.mp3"),
    "transcribe": lambda: workflows.transcribe("voice.mp3", "Turkish"),
    "model3d": lambda: workflows.model3d_generate(MODEL3D, "a.png"),
    "model3d_vertex": lambda: workflows.model3d_generate({**MODEL3D, "texture": "vertex", "resolution": 1024}, "a.png"),
}
RESULT_NODE = {
    "image": ("save", "SaveImage"), "image_turbo": ("save", "SaveImage"),
    "image_edit_1": ("save", "SaveImage"), "image_edit_turbo": ("save", "SaveImage"), "image_edit_3": ("save", "SaveImage"),
    "enhance": ("text", "PreviewAny"), "enhance_edit": ("text", "PreviewAny"),
    "video": ("save", "SaveVideo"), "video_first": ("save", "SaveVideo"), "video_first_last": ("save", "SaveVideo"),
    "music": ("save", "SaveAudioMP3"), "speech": ("save", "SaveAudioMP3"), "speech_clone": ("save", "SaveAudioMP3"),
    "qwen3_preset": ("save", "SaveAudioMP3"), "qwen3_clone": ("save", "SaveAudioMP3"), "transcribe": ("text", "PreviewAny"),
    "model3d": ("save", "SaveGLB"), "model3d_vertex": ("save", "SaveGLB"),
}


@pytest.mark.parametrize("name", GRAPHS)
def test_graph_links_and_result_node(name):
    wf = GRAPHS[name]()
    assert_links_valid(wf)
    node, cls = RESULT_NODE[name]
    assert wf[node]["class_type"] == cls


def test_image_params_reach_sampler():
    s = workflows.image_generate(IMAGE)["sampler"]["inputs"]
    assert (s["seed"], s["steps"], s["cfg"], s["sampler_name"], s["scheduler"]) == (7, 25, 3.0, "euler", "simple")

    turbo = workflows.image_generate({**IMAGE, "turbo": True, "steps": 8, "cfg": 1.0})
    assert turbo["lora"]["inputs"]["lora_name"] == workflows.IMAGE_TURBO_LORA
    assert turbo["cache"]["inputs"]["model"] == ["lora", 0]
    warm = workflows.image_warmup()
    assert_links_valid(warm)
    assert warm[workflows.SAVE_NODE]["class_type"] == "PreviewImage"

def test_image_edit_scales_references_to_edit_resolution():
    """Büyük bir fotoğraf referans olduğunda referans ve çıktı seçilen düzenleme çözünürlüğüne iner."""
    import app

    for name, pixels in app.EDIT_RESOLUTIONS.items():
        assert pixels % 32 == 0, name
        wf = workflows.image_edit({**IMAGE, "edit_pixels": pixels}, ["a.png"])
        assert wf["encode"]["inputs"]["resolution"] == pixels
    assert app.EDIT_RESOLUTIONS["1080p"] ** 2 >= 1920 * 1080


def test_image_edit_uses_all_references():
    wf = workflows.image_edit(IMAGE, ["a.png", "b.png"])
    assert [wf[f"load{i}"]["inputs"]["image"] for i in (1, 2)] == ["a.png", "b.png"]
    assert wf["encode"]["inputs"]["images.image_2"] == ["load2", 0]


def test_enhance_uses_matching_model_and_system_prompt():
    t2i = workflows.enhance_prompt("x", [], 1)
    i2i = workflows.enhance_prompt("x", ["a.png"], 1)
    assert t2i["pe"]["inputs"]["clip_name"] == workflows.PE_T2I
    assert i2i["pe"]["inputs"]["clip_name"] == workflows.PE_I2I
    assert "Image Prompt Rewriting Expert" in t2i["generate"]["inputs"]["system_prompt"]
    assert "Edit Prompt Enhancer" in i2i["generate"]["inputs"]["system_prompt"]


def test_enhance_prompts_translate_turkish_by_meaning():
    for f in ("pe_t2i.md", "pe_i2i.md"):
        text = (workflows.PROMPTS / f).read_text()
        assert "foggy" in text and "sisli" in text, f


@pytest.mark.parametrize("seconds", [2, 2.5, 3, 4, 5, 7, 10])
def test_video_length_is_valid_block_count(seconds):
    n = workflows.video_length(seconds)
    # H3 kare sayısı 17'nin katı + 5 olmalı ve istenen süreye yakın.
    assert n % 17 == 5
    assert abs(n - seconds * 24) <= 17


def test_video_turbo_switches_lora_and_steps():
    turbo = workflows.video_generate(VIDEO, [])
    full = workflows.video_generate({**VIDEO, "turbo": False}, [])
    assert any(n["class_type"] == "LoraLoaderModelOnly" for n in turbo.values())
    assert not any(n["class_type"] == "LoraLoaderModelOnly" for n in full.values())
    steps = {wf_name: next(n["inputs"]["steps"] for n in wf.values() if n["class_type"] == "BasicScheduler")
             for wf_name, wf in (("turbo", turbo), ("full", full))}
    assert steps == {"turbo": 8, "full": 20}


def test_speech_engines():
    cb = workflows.speech_generate(SPEECH, None)
    assert any(n["class_type"] == "ChatterBoxOfficial23LangEngineNode" for n in cb.values())
    clone = workflows.speech_generate(SPEECH, "voice.mp3")
    assert any(n["class_type"] == "LoadAudio" and n["inputs"]["audio"] == "voice.mp3" for n in clone.values())
    q = workflows.speech_generate({**SPEECH, "engine": "qwen3", "language": "English"}, None)
    engine = next(n for n in q.values() if n["class_type"] == "Qwen3TTSEngineNode")
    assert engine["inputs"]["runtime_mode"] == "⚠️ Shared Runtime"
    assert "CustomVoice" in engine["inputs"]["model_variant"] and engine["inputs"]["voice_preset"] == "Vivian"
    qc = workflows.speech_generate({**SPEECH, "engine": "qwen3"}, "v.mp3")
    engine = next(n for n in qc.values() if n["class_type"] == "Qwen3TTSEngineNode")
    assert "Voice Clone" in engine["inputs"]["model_variant"] and engine["inputs"]["x_vector_only_mode"] is True
    qc = workflows.speech_generate({**SPEECH, "engine": "qwen3", "reference_text": "merhaba"}, "v.mp3")
    engine = next(n for n in qc.values() if n["class_type"] == "Qwen3TTSEngineNode")
    assert engine["inputs"]["x_vector_only_mode"] is False


def _station_files():
    """Katalogun (models.toml) her paket için indirdiği dosya adları."""
    return {pack: {Path(f).name for _, f, _ in files} for pack, files in workflows.catalog.packs(workflows.CATALOG).items()}


def test_workflow_models_are_downloaded_by_station():
    files = _station_files()
    assert {workflows.IMAGE_MODEL, workflows.TEXT_ENCODER, workflows.VAE, workflows.IMAGE_TURBO_LORA} <= files["image"]
    assert {workflows.PE_T2I, workflows.PE_I2I} <= files["enhance"]
    assert {workflows.VIDEO_MODEL, workflows.VIDEO_TEXT_ENCODER, workflows.VIDEO_VAE, workflows.VIDEO_AUDIO_VAE,
            workflows.VIDEO_TURBO_LORA} <= files["video"]
    assert {workflows.MUSIC_MODEL} <= files["music"]
    assert {workflows.MODEL3D, workflows.MODEL3D_VISION, workflows.MODEL3D_SHAPE_VAE, workflows.MODEL3D_TEXTURE_VAE,
            workflows.BG_REMOVAL} <= files["model3d"]


# --- ComfyUI düğüm şemasına uyum ---
# fixtures/object_info.json, çalışan runtime'daki ComfyUI'nin /object_info yanıtından kullandığımız
# düğümlerin kaydı. ComfyUI ya da eklentiler güncellenince yeniden alınır:
#   ssh colab "curl -s 127.0.0.1:8188/object_info" ve tests/test_workflows.py'deki düğümlere süzülür.
SCHEMA = __import__("json").loads((Path(__file__).parent / "fixtures" / "object_info.json").read_text())
FILE_SUFFIXES = (".safetensors", ".png", ".mp3", ".wav")  # yüklenen/indirilen dosyalar; anlık listeye bağlı


def _declared(inputs, prefix=""):
    """Düğümün kabul ettiği girişler: ad -> (spec, zorunlu mu). Dinamik seçimlerin alt girişleri
    'ana.alt' adıyla, büyüyen girişler 'ana.ad' adıyla gelir."""
    out = {}
    for section in ("required", "optional"):
        for name, spec in inputs.get(section, {}).items():
            out[prefix + name] = (spec, section == "required")
    return out


def _check_value(where, spec, value):
    kind, opts = spec[0], (spec[1] if len(spec) > 1 else {})
    if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
        return kind  # bağlantı; türü aşağıda karşılaştırılır
    if isinstance(kind, list) or kind == "COMBO":
        options = kind if isinstance(kind, list) else opts["options"]
        if not (isinstance(value, str) and value.endswith(FILE_SUFFIXES)):
            assert value in options, f"{where}: {value!r} seçeneklerde yok"
    elif kind == "INT":
        assert isinstance(value, int) and not isinstance(value, bool), f"{where}: tam sayı değil"
        assert opts.get("min", value) <= value <= opts.get("max", value), f"{where}: {value} aralık dışı"
    elif kind == "FLOAT":
        assert isinstance(value, (int, float)) and not isinstance(value, bool), f"{where}: sayı değil"
        assert opts.get("min", value) <= value <= opts.get("max", value), f"{where}: {value} aralık dışı"
    elif kind == "BOOLEAN":
        assert isinstance(value, bool), f"{where}: bool değil"
    elif kind in ("STRING", "COLOR"):
        assert isinstance(value, str), f"{where}: metin değil"
    else:
        raise AssertionError(f"{where}: {kind} türü bir bağlantı ister, değer verildi: {value!r}")
    return None


def validate_against_schema(wf):
    for node_id, node in wf.items():
        cls = node["class_type"]
        assert cls in SCHEMA, f"{node_id}: {cls} düğümü runtime'da yok"
        declared = _declared(SCHEMA[cls]["input"])
        given = dict(node["inputs"])
        # Dinamik seçimler (V3): seçilen seçeneğin alt girişleri 'ana.alt' olarak eklenir.
        for name, (spec, _) in list(declared.items()):
            if spec[0] == "COMFY_DYNAMICCOMBO_V3" and name in given:
                option = next(o for o in spec[1]["options"] if o["key"] == given[name])
                declared.update(_declared(option["inputs"], f"{name}."))
            if spec[0] == "COMFY_AUTOGROW_V3":
                t = spec[1]["template"]
                for sub in t.get("names") or [f"{t['prefix']}{i}" for i in range(t["max"])]:
                    declared[f"{name}.{sub}"] = (spec[1]["template"]["input"]["required"]["image"], False)
        for name, value in given.items():
            assert name in declared, f"{node_id} ({cls}): bilinmeyen giriş {name}"
            spec = declared[name][0]
            if spec[0] in ("COMFY_DYNAMICCOMBO_V3",):
                assert value in [o["key"] for o in spec[1]["options"]], f"{node_id}.{name}: {value!r}"
                continue
            link_type = _check_value(f"{node_id}.{name}", spec, value)
            if link_type is not None and link_type != "*":
                src = wf[value[0]]
                produced = SCHEMA[src["class_type"]]["output"][value[1]]
                assert produced == "*" or produced in link_type.split(",") or link_type in ("COMBO",), \
                    f"{node_id}.{name}: {link_type} bekleniyor, {value[0]} {produced} veriyor"
        for name, (spec, required) in declared.items():
            if required and "." not in name and spec[0] != "COMFY_AUTOGROW_V3":
                assert name in given, f"{node_id} ({cls}): zorunlu giriş eksik: {name}"


@pytest.mark.parametrize("name", GRAPHS)
def test_graph_matches_comfyui_schema(name):
    validate_against_schema(GRAPHS[name]())


def test_ui_samplers_and_schedulers_exist_in_comfyui():
    import app

    ks = SCHEMA["KSampler"]["input"]["required"]
    assert set(app.SAMPLERS) <= set(ks["sampler_name"][0])
    assert set(app.SCHEDULERS) <= set(ks["scheduler"][0])


def test_language_lists_exist_in_nodes():
    import app

    whisper = SCHEMA["Apply Whisper"]["input"]["optional"]["language"][0]
    assert set(app.TRANSCRIBE_LANGUAGES) <= set(whisper)
    cb = SCHEMA["ChatterBoxOfficial23LangEngineNode"]["input"]["required"]["language"][0]
    assert set(app.SPEECH_LANGUAGES["chatterbox"]) <= set(cb)
    q = SCHEMA["Qwen3TTSEngineNode"]["input"]["required"]
    assert set(app.SPEECH_LANGUAGES["qwen3"]) <= set(q["language"][0])
    assert set(app.QWEN3_PRESETS) <= set(q["voice_preset"][0])


def test_model3d_params_reach_graph():
    """Şekil çözünürlüğü büyütme aşamasına, üçgen sayısı sadeleştirmeye gider; doku türü
    sonuç ağını seçer (PBR: UV + pişirilmiş haritalar, vertex: köşe boyama)."""
    import app

    for resolution in app.MODEL3D_RESOLUTIONS:
        wf = workflows.model3d_generate({**MODEL3D, "resolution": resolution}, "a.png")
        assert wf["upsample_stage"]["inputs"]["target_resolution"] == resolution
        validate_against_schema(wf)
    wf = workflows.model3d_generate({**MODEL3D, "faces": 50000}, "a.png")
    assert wf["decimate"]["inputs"]["target_face_count"] == 50000
    assert wf["save"]["inputs"]["mesh"] == ["textured_smooth", 0] and "painted" not in wf
    assert {n["inputs"]["seed"] for n in wf.values() if n["class_type"] == "KSampler"} == {3}
    wf = workflows.model3d_generate({**MODEL3D, "texture": "vertex"}, "a.png")
    assert wf["save"]["inputs"]["mesh"] == ["painted", 0] and "bake" not in wf
    assert wf["load"]["inputs"]["image"] == "a.png"


def test_every_pack_is_downloaded_at_startup():
    """Runtime'ın beklediği her paket station'da tanımlı ve açılışta indirme listesinde."""
    import runtime

    packs = set(_station_files())
    assert packs == runtime.PACKS
    setup = (Path(workflows.__file__).resolve().parent.parent / "station" / "runtime_setup.sh").read_text()
    line = next(l for l in setup.splitlines() if "models.py" in l and "nohup" in l)
    assert set(line.split("models.py")[1].split(">")[0].split()) == packs


def test_setup_script_pins_comfyui_and_plugins():
    """Kurulum her açılışta master'ı çekmez: ComfyUI ve eklentiler sabit commit'ten indirilir."""
    setup = (Path(workflows.__file__).resolve().parent.parent / "station" / "runtime_setup.sh").read_text()
    assert "git clone" not in setup
    github = [l for l in setup.splitlines() if "github.com" in l and "archive/" in l]
    assert len(github) == 3
    for line in github:
        assert "archive/${" in line and "_COMMIT}.tar.gz" in line
    for name in ("COMFY_COMMIT", "TTS_SUITE_COMMIT", "WHISPER_COMMIT"):
        assert re.search(rf"^{name}=[0-9a-f]{{40}}$", setup, re.M)


def test_setup_script_downloads_wheels_over_https_and_verifies_sha256():
    root = Path(workflows.__file__).resolve().parent.parent
    setup = (root / "station" / "runtime_setup.sh").read_text()
    assert "http://" not in setup
    assert re.search(r"^WHEELS_SHA256=[0-9a-f]{64}$", setup, re.M)
    assert "sha256sum -c" in setup and "wheels.tar.gz" in setup
    assert re.search(r"^WHEELS_URL=https://github\.com/[^/]+/[^/]+/releases/download/\S+/wheels\.tar\.gz$", setup, re.M)


def test_setup_script_can_skip_waiting_for_a_pack():
    setup = (Path(workflows.__file__).resolve().parent.parent / "station" / "runtime_setup.sh").read_text()
    assert 'WANTED} == none' in setup and "exit 0" in setup


# --- Kurulum betiğinin dayanıklılığı (idempotent, atomik aşama, sabit sürüm) ---

def _setup_text():
    return (Path(workflows.__file__).resolve().parent.parent / "station" / "runtime_setup.sh").read_text()


def test_setup_script_writes_the_phase_atomically():
    """sunucu /content/setup_phase'ı 3 sn'de bir okuyor: truncate + yazma boş/yarım aşama okutuyordu."""
    setup = _setup_text()
    assert ('printf \'%s\\n\' "$1" > /content/setup_phase.tmp && '
            "mv /content/setup_phase.tmp /content/setup_phase") in setup
    assert "> /content/setup_phase\n" not in setup  # doğrudan truncate eden yazma kalmadı


def test_setup_script_starts_background_workers_without_a_stdin():
    """Model indiricisi ssh'ın stdin'ini tutarsa ssh dönmez: arka plan işi </dev/null ile başlar."""
    line = next(l for l in _setup_text().splitlines() if "models.py" in l and "nohup" in l)
    assert "</dev/null" in line


def test_setup_script_is_rerunnable_in_the_same_runtime():
    """Aynı runtime'da yeniden koşma: var olan venv'ler --clear ile, ComfyUI ağacı silinerek kurulur."""
    setup = _setup_text()
    assert "uv venv -q --clear /content/hfenv" in setup
    assert 'uv venv -q --clear --python "${VENV}/bin/python" "${SHARED_RT}"' in setup
    assert 'rm -rf "${COMFY}"' in setup
    # .pth yolu python'dan türetilir: sabit lib/python3.13 sürüm değişince sessizce yanlış yere yazardı.
    assert 'sysconfig.get_paths()["purelib"]' in setup
    assert "lib/python3.13/site-packages/_tts_audio_suite_base_runtime.pth" not in setup


def test_setup_script_verifies_the_np_bool_patch():
    """sed desen bulamazsa da 0 döner: yama kalktı mı doğrulanır, sessiz ses bozukluğu yerine hata verilir."""
    setup = _setup_text()
    assert "grep -q 'np\\.bool = bool'" in setup
    assert 'exit 1' in setup.split("grep -q 'np\\.bool = bool'")[1][:200]


def test_setup_script_truncates_logs_and_surfaces_the_shared_runtime_error():
    """comfy.log sınırsız büyüyordu; Qwen3-TTS hatası ise yalnızca log dosyasına gidiyordu."""
    setup = _setup_text()
    assert ": > /content/comfy.log" in setup
    assert "|| { cat /content/shared_runtime.log >&2; exit 1; }" in setup


def test_setup_script_pins_the_download_stack_versions():
    """İndirme hattı get_hf_file_metadata/hf_hub_url/.location davranışına bağlı: sürümler sabit."""
    setup = _setup_text()
    audio = (Path(workflows.__file__).resolve().parent.parent / "station" / "audio_requirements.txt").read_text()
    audio_hub = re.search(r"^huggingface_hub==([0-9][^\s]*)$", audio, re.M).group(1)
    assert re.search(rf'^HUGGINGFACE_HUB_VERSION={re.escape(audio_hub)}$', setup, re.M)
    assert '"huggingface_hub==${HUGGINGFACE_HUB_VERSION}"' in setup
    assert re.search(r"^UV_VERSION=[0-9]+\.[0-9]+\.[0-9]+$", setup, re.M)
    assert 'pip install -q "uv==${UV_VERSION}"' in setup
    assert not re.search(r"^uv pip install -q -p /content/hfenv huggingface_hub$", setup, re.M)
