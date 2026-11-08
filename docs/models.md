# Models

[← Back to the README](../README.md)

Which files are downloaded, and which file fills each slot of each ComfyUI workflow, is defined in
[`station/models.default.toml`](../station/models.default.toml). To change a model:

```bash
cp station/models.default.toml models.toml   # git-ignored, survives updates
# edit models.toml, then restart the studio
```

Docker does not see this file until you uncomment the `./models.toml:/app/models.toml:ro` line in
`docker-compose.yml`; then run `docker compose up -d`.

Each slot is a Hugging Face file:

```toml
[image]
label = "Qwen-Image 2.1"                       # shown in the settings panel
description.en = "Official base model."
description.tr = "Resmi temel model."
[image.files]
diffusion_model = { repo = "Comfy-Org/Qwen-Image-2.1", file = "diffusion_models/qwen_image_2.1_int8_convrot.safetensors", dir = "diffusion_models" }
text_encoder    = { repo = "Comfy-Org/Qwen-Image-2.1", file = "text_encoders/qwen3vl_8b_int8_convrot.safetensors", dir = "text_encoders" }
vae             = { repo = "Comfy-Org/Qwen-Image-2.1", file = "vae/qwen_image_2.1_vae_bf16.safetensors", dir = "vae" }
turbo_lora      = { repo = "Comfy-Org/Qwen-Image-2.1", file = "loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors", dir = "loras" }
```

| Mode | Slots |
| --- | --- |
| `image` | `diffusion_model`, `text_encoder`, `vae`, `turbo_lora` |
| `enhance` | `prompt_enhancer_t2i`, `prompt_enhancer_i2i` |
| `video` | `diffusion_model`, `text_encoder`, `video_vae`, `audio_vae`, `turbo_lora` |
| `model3d` | `diffusion_model`, `clip_vision`, `shape_vae`, `texture_vae`, `background_removal` |
| `music` | `checkpoint` |
| `speech` | none; files listed as `[[speech.extra]]` are downloaded as-is |
| `transcribe` | no section: the Whisper model is downloaded by its ComfyUI plugin (a `[transcribe]` section is rejected) |

Rules:

- A replacement must have the **same architecture** as the default (for `image`: another Qwen-Image
  2.1 model or fine-tune). The workflows are fixed ComfyUI graphs; an incompatible file fails when
  the job runs, with ComfyUI's error shown in the chat.
- It must fit the L4's 24 GB of VRAM; the defaults are quantized (int8/w6a8/nvfp4) for this reason.
- The file is checked when the studio starts: invalid TOML (with the line), a missing section or slot,
  an unknown key (e.g. `labell`) or a malformed entry stops it with a one-line message naming the
  file. Whether a `file = ...` really exists in its Hugging Face repo is only found out when the GPU
  downloads it, so copy file names exactly.
- Gated repositories need `HF_TOKEN` in `.env`, from an account that accepted the model's terms.

The default image model, Noct-Q V4, is an **uncensored** fine-tune. Switch to the official
Qwen-Image 2.1 file shown above if that is not what you want. Each model has its own license; check
it on its Hugging Face page before using outputs commercially.
