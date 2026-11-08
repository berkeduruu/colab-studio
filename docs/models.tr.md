# Modeller

[← README'ye dön](../README.tr.md)

Hangi dosyaların indirileceği ve her ComfyUI iş akışındaki her yuvayı hangi dosyanın dolduracağı
[`station/models.default.toml`](../station/models.default.toml) dosyasında tanımlı. Bir modeli değiştirmek için:

```bash
cp station/models.default.toml models.toml   # git'e girmez, güncellemelerde korunur
# models.toml'u düzenle, sonra stüdyoyu yeniden başlat
```

Docker bu dosyayı, `docker-compose.yml` içindeki `./models.toml:/app/models.toml:ro` satırının
yorumunu kaldırana kadar görmez; sonra `docker compose up -d` çalıştır.

Her yuva bir Hugging Face dosyasıdır:

```toml
[image]
label = "Qwen-Image 2.1"                       # ayarlar panelinde görünür
description.en = "Official base model."
description.tr = "Resmi temel model."
[image.files]
diffusion_model = { repo = "Comfy-Org/Qwen-Image-2.1", file = "diffusion_models/qwen_image_2.1_int8_convrot.safetensors", dir = "diffusion_models" }
text_encoder    = { repo = "Comfy-Org/Qwen-Image-2.1", file = "text_encoders/qwen3vl_8b_int8_convrot.safetensors", dir = "text_encoders" }
vae             = { repo = "Comfy-Org/Qwen-Image-2.1", file = "vae/qwen_image_2.1_vae_bf16.safetensors", dir = "vae" }
turbo_lora      = { repo = "Comfy-Org/Qwen-Image-2.1", file = "loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors", dir = "loras" }
```

| Mod | Yuvalar |
| --- | --- |
| `image` | `diffusion_model`, `text_encoder`, `vae`, `turbo_lora` |
| `enhance` | `prompt_enhancer_t2i`, `prompt_enhancer_i2i` |
| `video` | `diffusion_model`, `text_encoder`, `video_vae`, `audio_vae`, `turbo_lora` |
| `model3d` | `diffusion_model`, `clip_vision`, `shape_vae`, `texture_vae`, `background_removal` |
| `music` | `checkpoint` |
| `speech` | yok; `[[speech.extra]]` olarak listelenen dosyalar olduğu gibi indirilir |
| `transcribe` | bölüm yok: Whisper modelini ComfyUI eklentisi kendisi indirir (`[transcribe]` bölümü reddedilir) |

Kurallar:

- Yeni model varsayılanla **aynı mimaride** olmalı (`image` için başka bir Qwen-Image 2.1 modeli ya
  da ince ayarı). İş akışları sabit ComfyUI grafikleridir; uyumsuz bir dosya iş çalışırken hata
  verir ve ComfyUI'nin hatası sohbette görünür.
- L4'ün 24 GB VRAM'ine sığmalı; varsayılanlar bu yüzden nicemlenmiş (int8/w6a8/nvfp4) sürümler.
- Dosya stüdyo açılırken denetlenir: geçersiz TOML (satırıyla), eksik bölüm ya da yuva, bilinmeyen
  bir anahtar (ör. `labell`) ya da bozuk bir giriş, dosyayı adıyla anan tek satırlık bir mesajla
  açılışı durdurur. Bir `file = ...` değerinin Hugging Face reposunda gerçekten var olup olmadığı
  ancak GPU onu indirirken anlaşılır; dosya adlarını birebir kopyala.
- Erişimi kısıtlı (gated) repolar için `.env`'de, modelin şartlarını kabul etmiş bir hesabın `HF_TOKEN`'ı gerekir.

Varsayılan görsel modeli Noct-Q V4 **sansürsüz** bir ince ayardır. İstemiyorsan yukarıdaki resmi
Qwen-Image 2.1 dosyasına geç. Her modelin kendi lisansı var; çıktıları ticari kullanmadan önce
modelin Hugging Face sayfasına bak.
