"""Stüdyo ayarlarından ComfyUI API iş akışları üretir.

Düğüm ve giriş adları ComfyUI'nin resmi Qwen-Image 2.1 şablonlarından alındı
(Comfy-Org/workflow_templates: image_qwen_image_2_1_t2i, image_qwen_image_2_1_image_edit).
"""
import sys
from pathlib import Path

import config

sys.path.insert(0, str(config.REPO_DIR / "station"))
import catalog

PROMPTS = Path(__file__).parent / "prompts"

# Model dosyaları katalogdan (models.toml); runtime aynı dosyaları indirir.
try:
    CATALOG = catalog.load(config.MODELS_FILE)
except catalog.CatalogError as e:
    sys.exit(f"Colab Studio: the model catalog is not valid: {e}\n"
             "Fix the file (see docs/models.md) and start the studio again.")
_f = lambda mode, slot: catalog.filename(CATALOG, mode, slot)

IMAGE_MODEL = _f("image", "diffusion_model")
TEXT_ENCODER = _f("image", "text_encoder")
VAE = _f("image", "vae")
IMAGE_TURBO_LORA = _f("image", "turbo_lora")
PE_T2I = _f("enhance", "prompt_enhancer_t2i")
PE_I2I = _f("enhance", "prompt_enhancer_i2i")

# Sonuçları okuyacağımız düğümlerin kimlikleri
SAVE_NODE = "save"
TEXT_NODE = "text"


def _model_nodes(turbo=False):
    wf = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": IMAGE_MODEL, "weight_dtype": "default"}},
        "cache": {"class_type": "QwenImage21Cache", "inputs": {"model": ["lora" if turbo else "unet", 0], "device": "auto", "dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "qwen_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
    }
    if turbo:
        wf["lora"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet", 0], "lora_name": IMAGE_TURBO_LORA, "strength_model": 1.0}}
    return wf


def _sample_and_save(p, latent):
    return {
        "sampler": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["cache", 0],
                "positive": ["encode", 0],
                "negative": ["encode", 1],
                "latent_image": latent,
                "seed": p["seed"],
                "steps": p["steps"],
                "cfg": p["cfg"],
                "sampler_name": p["sampler"],
                "scheduler": p["scheduler"],
                "denoise": 1.0,
            },
        },
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]}},
        SAVE_NODE: {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": "studio/image"}},
    }


def image_generate(p):
    """p: prompt, negative, width, height, steps, cfg, seed, turbo (isteğe bağlı)"""
    wf = _model_nodes(p.get("turbo", False))
    wf["encode"] = {
        "class_type": "TextEncodeQwenImage21",
        "inputs": {"clip": ["clip", 0], "prompt": p["prompt"], "negative_prompt": p["negative"], "resolution": 1024},
    }
    wf["latent"] = {"class_type": "EmptyLatentImage", "inputs": {"width": p["width"], "height": p["height"], "batch_size": 1}}
    wf.update(_sample_and_save(p, ["latent", 0]))
    return wf


def image_warmup():
    """GPU hazır olunca varsayılan görsel modellerini (UNET, CLIP, VAE) önceden belleğe yükleyen
    1 adımlık küçük iş; diske dosya kaydetmez."""
    wf = image_generate({"prompt": "warmup", "negative": "", "width": 256, "height": 256,
                         "steps": 1, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "seed": 1})
    wf[SAVE_NODE] = {"class_type": "PreviewImage", "inputs": {"images": ["decode", 0]}}
    return wf


def image_edit(p, images):
    """images: ComfyUI input klasörüne yüklenmiş referans görsellerin adları (en fazla 10).
    Referanslar ve çıktı p["edit_pixels"]² piksel civarına küçültülür, oran ilk referanstan gelir:
    telefon fotoğrafı (ör. 3072x2304) kendi boyutunda kalırsa her adım ~100 kat yavaşlar."""
    wf = _model_nodes(p.get("turbo", False))
    inputs = {"clip": ["clip", 0], "vae": ["vae", 0], "prompt": p["prompt"], "negative_prompt": p["negative"], "resolution": p["edit_pixels"]}
    for i, name in enumerate(images, 1):
        wf[f"load{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        inputs[f"images.image_{i}"] = [f"load{i}", 0]
    wf["encode"] = {"class_type": "TextEncodeQwenImage21", "inputs": inputs}
    wf.update(_sample_and_save(p, ["encode", 2]))
    return wf


def enhance_prompt(prompt, images, seed):
    """Kısa isteği ayrıntılı İngilizce istem paragrafına çevirir. Referans görsel varsa düzenleme
    talimatı olarak ele alınır."""
    edit = bool(images)
    wf = {
        "pe": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": PE_I2I if edit else PE_T2I, "type": "qwen_image" if edit else "stable_diffusion", "device": "default"},
        },
        "generate": {
            "class_type": "TextGenerate",
            "inputs": {
                "clip": ["pe", 0],
                "prompt": prompt,
                "system_prompt": (PROMPTS / ("pe_i2i.md" if edit else "pe_t2i.md")).read_text(),
                "max_length": 4096,
                "thinking": False,
                "use_default_template": True,
                "mtp": "auto",
                "sampling_mode": "on",
                "sampling_mode.temperature": 1.0,
                "sampling_mode.top_k": 20,
                "sampling_mode.top_p": 0.95,
                "sampling_mode.min_p": 0.0,
                "sampling_mode.repetition_penalty": 1.0,
                "sampling_mode.seed": seed,
                "sampling_mode.presence_penalty": 1.5,
            },
        },
        TEXT_NODE: {"class_type": "PreviewAny", "inputs": {"source": ["generate", 0]}},
    }
    if edit:
        batch = {}
        for i, name in enumerate(images):
            wf[f"load{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            batch[f"images.image{i}"] = [f"load{i}", 0]
        wf["batch"] = {"class_type": "BatchImagesNode", "inputs": batch}
        wf["generate"]["inputs"]["image"] = ["batch", 0]
    return wf


# --- video: MiniMax H3 (Comfy-Org/workflow_templates: video_minimax_h3_t2v, video_minimax_h3_i2v) ---

VIDEO_MODEL = _f("video", "diffusion_model")
VIDEO_TEXT_ENCODER = _f("video", "text_encoder")
VIDEO_VAE = _f("video", "video_vae")
VIDEO_AUDIO_VAE = _f("video", "audio_vae")
VIDEO_TURBO_LORA = _f("video", "turbo_lora")
VIDEO_FPS = 24


def video_length(seconds):
    """Saniyeyi H3'ün 17k+5 kare ızgarasına yuvarlar (şablondaki Math Expression ile aynı)."""
    n = max(5, round(seconds * VIDEO_FPS))
    return n + (5 - n % 17) % 17


def video_generate(p, images):
    """p: prompt, width, height, seconds, turbo, seed. images: ilk kare ve isteğe bağlı son kare.
    Turbo: 8 adımlık LoRA ile ~2.5 kat hızlı."""
    model = ["lora", 0] if p["turbo"] else ["unet", 0]
    wf = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": VIDEO_MODEL, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": VIDEO_TEXT_ENCODER, "type": "minimax", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "audio_vae": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_AUDIO_VAE}},
        "encode": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["clip", 0], "vae": ["vae", 0], "prompt": p["prompt"],
                "width": p["width"], "height": p["height"], "length": video_length(p["seconds"]),
            },
        },
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": p["seed"]}},
        "sampler_select": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "guider": {"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": ["encode", 0]}},
        "scheduler": {
            "class_type": "BasicScheduler",
            "inputs": {"model": model, "scheduler": "simple", "steps": 8 if p["turbo"] else 20, "denoise": 1.0},
        },
        "sampler": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler_select", 0],
                "sigmas": ["scheduler", 0], "latent_image": ["encode", 1],
            },
        },
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]}},
        "decode_audio": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["sampler", 0], "vae": ["audio_vae", 0]}},
        "video": {"class_type": "CreateVideo", "inputs": {"images": ["decode", 0], "audio": ["decode_audio", 0], "fps": VIDEO_FPS}},
        SAVE_NODE: {
            "class_type": "SaveVideo",
            "inputs": {"video": ["video", 0], "filename_prefix": "studio/video", "format": "auto", "format.codec": "auto"},
        },
    }
    if p["turbo"]:
        wf["lora"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["unet", 0], "lora_name": VIDEO_TURBO_LORA, "strength_model": 1.0},
        }
    for slot, name in zip(("first_frame", "last_frame"), images[:2]):
        wf[f"load_{slot}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf["encode"]["inputs"][slot] = [f"load_{slot}", 0]
    return wf


# --- müzik: YuE2 (Comfy-Org/workflow_templates: audio_yue2_text2music, ABC planlama kapalı) ---

MUSIC_MODEL = _f("music", "checkpoint")


def music_generate(p):
    """p: style, lyrics, seconds (en çok), seed. Şarkı sözleri [Verse]/[Chorus] etiketleriyle yazılır."""
    return {
        "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": MUSIC_MODEL}},
        "encode": {
            "class_type": "YuE2GenerateMusic",
            "inputs": {
                "clip": ["ckpt", 1], "style": p["style"], "lyrics": p["lyrics"], "abc": "", "seed": p["seed"],
                "mode": "full", "max_duration": p["seconds"], "temperature": 1.0, "top_p": 0.95, "top_k": 100,
                "repetition_penalty": 1.2,
            },
        },
        "negative": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["encode", 0]}},
        "latent": {"class_type": "EmptyYuE2LatentAudio", "inputs": {"seconds": ["encode", 1], "batch_size": 1}},
        "sampler": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["ckpt", 0], "positive": ["encode", 0], "negative": ["negative", 0], "latent_image": ["latent", 0],
                "seed": p["seed"], "steps": 32, "cfg": 1.0, "sampler_name": "dpm_2", "scheduler": "sgm_uniform", "denoise": 1.0,
            },
        },
        "decode": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["sampler", 0], "vae": ["ckpt", 2]}},
        SAVE_NODE: {"class_type": "SaveAudioMP3", "inputs": {"audio": ["decode", 0], "filename_prefix": "studio/music", "quality": "V0"}},
    }


# --- seslendirme ve ses klonlama: TTS-Audio-Suite (Chatterbox 23 dil, Qwen3-TTS) ---

def speech_generate(p, voice):
    """p: text, engine (chatterbox|qwen3), language, seed ve motorun ayarları.
    voice: ComfyUI input klasörüne yüklenmiş referans ses (klonlama) ya da None."""
    if p["engine"] == "chatterbox":
        engine = {
            "class_type": "ChatterBoxOfficial23LangEngineNode",
            "inputs": {
                "model_version": "v3", "language": p["language"], "device": "auto",
                "exaggeration": p["exaggeration"], "temperature": p["temperature"], "cfg_weight": p["cfg_weight"],
                "repetition_penalty": 1.2, "min_p": 0.05, "top_p": 1.0,
            },
        }
    else:
        engine = {
            "class_type": "Qwen3TTSEngineNode",
            "inputs": {
                # Referans ses varsa klonlayan temel model, yoksa hazır sesler + talimat modeli.
                "model_variant": "TTS - Base 1.7B (Voice Clone)" if voice else "TTS - CustomVoice 1.7B (Presets + Instruction)",
                "device": "auto", "voice_preset": "None (Zero-shot / Custom)" if voice else p["voice_preset"],
                "language": p["language"], "instruct": p["instruct"], "top_k": 50, "top_p": 1.0,
                "temperature": p["temperature"], "repetition_penalty": 1.05, "max_new_tokens": 2048,
                # Referans sesin dökümü verilmezse yalnızca ses rengi (x-vector) kullanılır.
                "x_vector_only_mode": bool(voice) and not p["reference_text"],
                # Qwen3-TTS transformers 4 ister; eklentinin ayrı sanal ortamında ("Shared Runtime")
                # çalışır. ComfyUI'nin ortamı transformers 5 kullanır.
                "runtime_mode": "⚠️ Shared Runtime",
            },
        }
    wf = {
        "engine": engine,
        "tts": {
            "class_type": "UnifiedTTSTextNode",
            "inputs": {
                "TTS_engine": ["engine", 0], "text": p["text"], "narrator_voice": "none", "seed": p["seed"],
                "enable_chunking": True, "max_chars_per_chunk": 400, "chunk_combination_method": "auto",
                "silence_between_chunks_ms": 100, "enable_audio_cache": False, "batch_size": 0,
            },
        },
        SAVE_NODE: {"class_type": "SaveAudioMP3", "inputs": {"audio": ["tts", 0], "filename_prefix": "studio/speech", "quality": "V0"}},
    }
    if voice:
        wf["voice_audio"] = {"class_type": "LoadAudio", "inputs": {"audio": voice}}
        wf["voice"] = {
            "class_type": "CharacterVoicesNode",
            "inputs": {
                "voice_name": "none", "reference_text": p["reference_text"], "trim_start": 0.0, "trim_end": 0.0,
                "customized": False, "opt_audio_input": ["voice_audio", 0],
            },
        }
        wf["tts"]["inputs"]["opt_narrator"] = ["voice", 0]
    return wf


# --- yazıya dökme: ComfyUI-Whisper (large-v3-turbo) ---

def transcribe(audio, language):
    return {
        "audio": {"class_type": "LoadAudio", "inputs": {"audio": audio}},
        "whisper": {"class_type": "Apply Whisper", "inputs": {"audio": ["audio", 0], "model": "large-v3-turbo", "language": language, "prompt": ""}},
        TEXT_NODE: {"class_type": "PreviewAny", "inputs": {"source": ["whisper", 0]}},
    }


# --- görselden 3D: TRELLIS.2 (Comfy-Org/workflow_templates: 3d_pixal3d_trellis2_image_to_model,
# "Switch to Trellis2" açık) ---

MODEL3D = _f("model3d", "diffusion_model")
MODEL3D_VISION = _f("model3d", "clip_vision")
MODEL3D_SHAPE_VAE = _f("model3d", "shape_vae")
MODEL3D_TEXTURE_VAE = _f("model3d", "texture_vae")
BG_REMOVAL = _f("model3d", "background_removal")


def model3d_generate(p, image):
    """p: resolution (şekil ızgarası 1024-2048), texture ("pbr": UV açılıp doku haritaları pişirilir,
    "vertex": renkler köşelere boyanır, hızlı), faces (hedef üçgen sayısı), seed.
    image: ComfyUI input klasörüne yüklenmiş görsel; arka planı BiRefNet ile ayrılır."""
    def sample(title, model, cond, steps, cfg, scheduler="normal"):
        return {
            "class_type": "KSampler",
            "_meta": {"title": title},
            "inputs": {
                "model": model, "positive": [cond, 0], "negative": [cond, 1], "latent_image": [cond, 2],
                "seed": p["seed"], "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": scheduler, "denoise": 1.0,
            },
        }

    wf = {
        "load": {"class_type": "LoadImage", "inputs": {"image": image}},
        "bg_model": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": BG_REMOVAL}},
        "mask": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["bg_model", 0], "image": ["load", 0]}},
        "crop": {
            "class_type": "ImageCropToMask",
            "inputs": {"images": ["load", 0], "masks": ["mask", 0], "width": 1024, "height": 1024, "pad_factor": 1.0,
                       "grow_mask": 0, "background": "#000000"},
        },
        "vision": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": MODEL3D_VISION}},
        "cond": {"class_type": "Trellis2Conditioning", "inputs": {"clip_vision_model": ["vision", 0], "image": ["crop", 0]}},
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL3D, "weight_dtype": "default"}},
        "shape_vae": {"class_type": "VAELoader", "inputs": {"vae_name": MODEL3D_SHAPE_VAE}},
        "texture_vae": {"class_type": "VAELoader", "inputs": {"vae_name": MODEL3D_TEXTURE_VAE}},
        # 1) kaba yapı (32³ doluluk ızgarası)
        "structure_cfg": {"class_type": "CFGOverride", "inputs": {"model": ["unet", 0], "cfg": 1.0, "start_percent": 0.667, "end_percent": 1.0}},
        "structure_rescale": {"class_type": "RescaleCFG", "inputs": {"model": ["structure_cfg", 0], "multiplier": 0.7}},
        "structure_model": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["structure_rescale", 0], "shift": 5.0}},
        "structure_latent": {"class_type": "EmptyTrellis2LatentStructure", "inputs": {"batch_size": 1}},
        "structure_sample": {
            "class_type": "KSampler",
            "_meta": {"title": "Kaba yapı üretiliyor"},
            "inputs": {
                "model": ["structure_model", 0], "positive": ["cond", 0], "negative": ["cond", 1], "latent_image": ["structure_latent", 0],
                "seed": p["seed"], "steps": 12, "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
            },
        },
        "structure": {"class_type": "VaeDecodeStructureTrellis2", "inputs": {"samples": ["structure_sample", 0], "vae": ["shape_vae", 0], "resolution": "32"}},
        # 2) şekil: düşük çözünürlük, sonra hedef çözünürlüğe büyütme
        "shape_cfg": {"class_type": "CFGOverride", "inputs": {"model": ["unet", 0], "cfg": 1.0, "start_percent": 0.769, "end_percent": 1.0}},
        "shape_model": {"class_type": "RescaleCFG", "inputs": {"model": ["shape_cfg", 0], "multiplier": 0.5}},
        "shape_stage": {"class_type": "Trellis2ShapeStage", "inputs": {"positive": ["cond", 0], "negative": ["cond", 1], "voxel": ["structure", 0]}},
        "shape_sample": sample("Şekil üretiliyor", ["shape_model", 0], "shape_stage", 20, 7.5),
        "upsample_stage": {
            "class_type": "Trellis2UpsampleStage",
            "inputs": {"positive": ["shape_stage", 0], "negative": ["shape_stage", 1], "shape_latent": ["shape_sample", 0],
                       "vae": ["shape_vae", 0], "target_resolution": p["resolution"]},
        },
        "upsample_sample": sample("Şekil ayrıntılandırılıyor", ["shape_model", 0], "upsample_stage", 12, 7.5, "simple"),
        "shape": {"class_type": "VaeDecodeShapeTrellis", "inputs": {"samples": ["upsample_sample", 0], "vae": ["shape_vae", 0]}},
        # 3) doku (renk, metalik, pürüzlülük vokselleri)
        "texture_stage": {"class_type": "Trellis2TextureStage", "inputs": {"positive": ["upsample_stage", 0], "negative": ["upsample_stage", 1], "shape_latent": ["upsample_sample", 0]}},
        "texture_sample": sample("Doku üretiliyor", ["unet", 0], "texture_stage", 12, 1.0),
        "texture": {"class_type": "VaeDecodeTextureTrellis", "inputs": {"samples": ["texture_sample", 0], "vae": ["texture_vae", 0], "shape_subdivides": ["shape", 1]}},
        # 4) ağ temizliği: kapalı yüzeye yeniden örme, sadeleştirme, yumuşak normaller
        "remesh": {
            "class_type": "RemeshMesh",
            "inputs": {"mesh": ["shape", 0], "resolution": 768, "sign_mode": "solid", "sign_mode.fill": 20.0, "sign_mode.qef": False,
                       "band": 1.0, "project_back": 0.0, "fix_poles": False, "smooth_iters": 20, "drop_small_components": 0.01,
                       "precluster_max_verts": 20000000},
        },
        "decimate": {"class_type": "DecimateMesh", "inputs": {"mesh": ["remesh", 0], "target_face_count": p["faces"], "placement_mode": "midpoint"}},
        "smooth": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["decimate", 0], "crease_angle": 180.0}},
    }
    if p["texture"] == "vertex":
        wf["painted"] = {"class_type": "PaintMesh", "inputs": {"mesh": ["smooth", 0], "voxel_colors": ["texture", 0]}}
        final = ["painted", 0]
    else:
        wf.update({
            # Atlas boyutu içeriğe göre (resolution 0, ~1200 px): şablondaki 2048 hedefiyle dokuda gözle
            # görülür fark yok. Yerleştirme numba ile derlenir; 200K üçgende UV açma ~5 sn.
            "unwrap": {"class_type": "UnwrapMesh", "inputs": {"mesh": ["smooth", 0], "segmenter": "pec", "resolution": 0, "padding": 1, "weld_distance": 0.0002}},
            "bake": {"class_type": "BakeTextureFromVoxel", "inputs": {"mesh": ["unwrap", 0], "voxel_colors": ["texture", 0], "texture_size": 2048, "reference_mesh": ["shape", 0]}},
            "normal_map": {"class_type": "BakeNormalMapFromMesh", "inputs": {"low_poly": ["unwrap", 0], "high_poly": ["remesh", 0], "resolution": 2048, "cage_distance": 0.05, "ignore_backfaces": True}},
            "occlusion": {"class_type": "BakeAmbientOcclusion", "inputs": {"low_poly": ["unwrap", 0], "high_poly": ["remesh", 0], "resolution": 1024, "samples": 64, "max_distance": 0.71, "strength": 1.0, "bias": 0.01}},
            "textured": {
                "class_type": "ApplyTextureToMesh",
                "inputs": {"mesh": ["unwrap", 0], "base_color": ["bake", 0], "metallic": ["bake", 1], "roughness": ["bake", 2],
                           "occlusion": ["occlusion", 0], "normal_map": ["normal_map", 0]},
            },
            "textured_smooth": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["textured", 0], "crease_angle": 180.0}},
        })
        final = ["textured_smooth", 0]
    wf[SAVE_NODE] = {"class_type": "SaveGLB", "inputs": {"mesh": final, "filename_prefix": "studio/model3d"}}
    return wf
