# Colab Stüdyo

**Görsel, video, müzik, seslendirme ve 3D model** üreten, ses kayıtlarını yazıya döken, sohbet
tarzında, kendi makinende barındırdığın bir web stüdyosu. Üretim **Google Colab L4 GPU**'da yapılır.
Arayüz kendi bilgisayarında ya da sunucunda çalışır. GPU yalnızca kullanırken kiralanır, boşta
kalınca kendiliğinden kapatılır.

[English README](README.md)

| Mod | Varsayılan model |
| --- | --- |
| Görsel üretme ve düzenleme | Noct-Q V4 (Qwen-Image 2.1 ince ayarı) + turbo LoRA |
| İstem geliştirme | Qwen3.5 9B istem geliştirici |
| Video (sesli) | MiniMax H3 |
| Müzik (vokalli) | YuE2 |
| Seslendirme, ses klonlama | Chatterbox (23 dil, Türkçe dahil), Qwen3-TTS |
| Yazıya dökme | Whisper large-v3-turbo |
| Görselden 3D (GLB) | TRELLIS.2 |

Her model, bir yapılandırma dosyasından Hugging Face'teki uyumlu bir modelle değiştirilebilir
([Modeller](#modeller)). Arayüz İngilizce ve Türkçe; dili sağ üstten seçersin.

## Nasıl çalışır

```
 tarayıcın ──► Colab Stüdyo (bu repo, bilgisayarında ya da sunucunda)
                 │  colab CLI: L4 runtime'ı açar / kapatır
                 │  SSH tüneli: ComfyUI'yi kurar, işleri gönderir, sonuçları indirir
                 ▼
              Google Colab L4 runtime ──► modelleri Hugging Face'ten indirir
```

1. İlk iş (ya da **L4 başlat** düğmesi) Google'ın resmi [Colab CLI](https://pypi.org/project/google-colab-cli/)'ı
   ile bir L4 açar, SSH ile bağlanır, ComfyUI'yi kurar ve o modun modellerini indirir. Bu birkaç
   dakika sürer; ilerleme kenar çubuğunda görünür.
2. İşler sıraya girer ve tek tek çalışır. Sonuçlar senin makinene kopyalanır; GPU kapandıktan sonra
   da durur.
3. 15 dakika boşta kalınca (ayarlanabilir) runtime kapatılır ve kredi harcaması durur.

## Gereksinimler

- **Linux** (Windows'ta **WSL2**). macOS denenmedi.
- **Colab kredisi (compute unit) olan bir Google hesabı** (Colab Pro, Pro+ ya da kullandıkça öde).
  Ücretsiz katmanda L4 yok. L4 açık kaldığı her saat kredi harcar; kenar çubuğu bakiyeni, saatlik
  harcamayı ve ne kadar yeteceğini gösterir.
- `curl`, `ssh`, `tar` ve `python3` (`sudo apt install curl openssh-client tar python3`). Gerisini
  (uv, Python 3.12, Colab CLI) `install.sh` kurar.
- İsteğe bağlı: arayüzü yerelde derlemek için Node.js 20+ (yoksa hazır derlenmiş kopya indirilir).
- İsteğe bağlı: ücretsiz bir [Hugging Face token'ı](https://huggingface.co/settings/tokens) (okuma
  yetkisi). Modeller token olmadan da iner ama girişsiz indirme daha yavaş ve hız sınırına takılabilir.

## Hızlı başlangıç

```bash
git clone https://github.com/berkeduruu/colab-studio.git
cd colab-studio
./install.sh
./run.sh
```

`install.sh` üç soru sorar: arayüzü kimin açabileceği, port ve isteğe bağlı Hugging Face token'ı.
Cevapları `.env`'e yazar ve Colab CLI'da Google girişini yaptırır: bir bağlantı basar, onu herhangi
bir tarayıcıda açıp onaylarsın, çıkan kodu terminale yapıştırırsın. Sonra <http://127.0.0.1:8190/>
adresini aç ve bir istem gönder; ilk istek GPU'yu açar.

Stüdyonun arka planda çalışması ve açılışta kendiliğinden başlaması için:

```bash
./install.sh --service
systemctl --user status colab-studio       # kayıtlar: journalctl --user -u colab-studio -f
```

## Başka cihazlardan açmak

Stüdyonun **kendi giriş ekranı yok**. Sayfayı açabilen herkes GPU'yu başlatıp senin kredini
harcayabilir. Bu yüzden şunlardan birini seç:

| Nerede çalışıyor | Nasıl açılır |
| --- | --- |
| Kendi bilgisayarında | <http://127.0.0.1:8190/> (varsayılan, `STUDIO_HOST=127.0.0.1`) |
| Sunucuda, yalnızca sen | SSH tüneli: `ssh -L 8190:127.0.0.1:8190 sunucum`, sonra <http://127.0.0.1:8190/> |
| Sunucuda, güvendiğin bir ağda | `.env`'de `STUDIO_HOST=0.0.0.0`, sonra `http://<sunucu-ip>:8190/` (şifre yok: güvenlik duvarı kullan) |
| Alan adı olan bir sunucuda | `sudo ./deploy/setup-nginx.sh studio.ornek.com`: nginx ile HTTPS (Let's Encrypt) ve şifre |

nginx seçeneğinde `STUDIO_HOST=127.0.0.1` kalmalı. Sunucuyu gösteren her DNS adı olur;
`<tireli-ip>.sslip.io` gibi ücretsiz adlar da.

## Docker

```bash
cp .env.example .env                                # isteğe bağlı: port, HF token, ...
docker compose run --rm studio colab usage          # tek seferlik Google girişi (bir volume'da saklanır)
docker compose up -d                                # http://127.0.0.1:8190/
```

`.env`'de `STUDIO_HOST=0.0.0.0` yoksa port yalnızca `127.0.0.1`'de yayımlanır. Sohbetler ve medya
`studio-data` volume'unda durur. Özel bir `models.toml` bağlanabilir (bkz. `docker-compose.yml`).

## Yapılandırma

Tüm ayarlar `.env` dosyasında ([`.env.example`](.env.example)'dan oluşturulur). Değişiklikten sonra
yeniden başlat (`systemctl --user restart colab-studio`, `docker compose restart` ya da Ctrl+C ve `./run.sh`).

| Değişken | Varsayılan | Anlamı |
| --- | --- | --- |
| `STUDIO_HOST` | `127.0.0.1` | Dinlenen adres. `0.0.0.0` arayüzü IP ile erişilebilir yapar (şifresiz). |
| `STUDIO_PORT` | `8190` | Arayüz portu. |
| `HF_TOKEN` | boş | Hugging Face okuma token'ı; yalnızca erişimi kısıtlı (gated) repolar için gerekli, hız sınırına karşı önerilir. |
| `STUDIO_IDLE_MINUTES` | `15` | GPU bu kadar dakika boşta kalınca kapatılır (arayüzden de açılıp kapatılır). |
| `STUDIO_DATA` | `~/.local/share/colab-studio` | Sohbetler, medya, öğrenilmiş süreler ve stüdyonun kendi SSH anahtarı. |
| `STUDIO_MODELS` | varsa `./models.toml`, yoksa `station/models.default.toml` | Model kataloğu. |
| `COLAB_BIN` | `PATH`'teki `colab` | Colab CLI'ın yolu. |
| `STUDIO_SESSION` | `studio` | Stüdyonun sahip olduğu Colab oturumunun adı; diğer oturumlarına hiç dokunulmaz. |

Yalnızca L4 GPU desteklenir: iş akışları, VRAM bütçeleri ve süre tahminlerinin hepsi L4'te ölçüldü.

## Modeller

Hangi dosyaların indirileceği ve her ComfyUI iş akışındaki her yuvayı hangi dosyanın dolduracağı
[`station/models.default.toml`](station/models.default.toml) dosyasında tanımlı. Bir modeli değiştirmek için:

```bash
cp station/models.default.toml models.toml   # git'e girmez, güncellemelerde korunur
# models.toml'u düzenle, sonra stüdyoyu yeniden başlat
```

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

Kurallar:

- Yeni model varsayılanla **aynı mimaride** olmalı (`image` için başka bir Qwen-Image 2.1 modeli ya
  da ince ayarı). İş akışları sabit ComfyUI grafikleridir; uyumsuz bir dosya iş çalışırken hata
  verir ve ComfyUI'nin hatası sohbette görünür.
- L4'ün 24 GB VRAM'ine sığmalı; varsayılanlar bu yüzden nicemlenmiş (int8/w6a8/nvfp4) sürümler.
- Dosya stüdyo açılırken denetlenir: yazım hatası ya da eksik yuva açık bir mesajla açılışı durdurur.
- Erişimi kısıtlı (gated) repolar için `.env`'de, modelin şartlarını kabul etmiş bir hesabın `HF_TOKEN`'ı gerekir.

Varsayılan görsel modeli Noct-Q V4 **sansürsüz** bir ince ayardır. İstemiyorsan yukarıdaki resmi
Qwen-Image 2.1 dosyasına geç. Her modelin kendi lisansı var; çıktıları ticari kullanmadan önce
modelin Hugging Face sayfasına bak.

## Claude Code'dan kullanmak (MCP)

[`mcp/`](mcp) klasöründeki MCP sunucusu Claude Code ya da Claude Desktop'ın stüdyoyu kullanmasını
sağlar: GPU'yu başlatma, üretim, işleri bekleme ve sonuçlara bakma.

```bash
cd mcp && ./install.sh
claude mcp add colab-studio -- node "$PWD/dist/index.js"
```

Uzak (SSH) kurulum ve araç listesi için [mcp/README.md](mcp/README.md).

## Güncelleme

```bash
git pull
rm -rf server/web/dist && ./install.sh     # arayüzü yeniden derler; .env ve models.toml korunur
systemctl --user restart colab-studio      # ya da ./run.sh'i yeniden başlat / docker compose up -d --build
```

## Sorun giderme

| Belirti | Çözüm |
| --- | --- |
| "colab CLI'da Google girişi yok" | `colab usage` çalıştır (Docker: `docker compose run --rm studio colab usage`), giriş yap, yeniden başlat. |
| GPU açılışı "GPU ayrılıyor"da düşüyor | Boşta L4 yok ya da kredi bitti. `colab usage` ile bak, sonra tekrar dene. |
| İndirme yavaş ya da 429 hatası | `.env`'e `HF_TOKEN` yaz ve yeniden başlat. |
| `models.toml`'u değiştirdikten sonra iş hata veriyor | Yeni dosya büyük olasılıkla farklı bir mimaride; bkz. [Modeller](#modeller). |
| Diğer her şey | Sağ üstteki kayıtlar düğmesi GPU açılış kaydını, `journalctl --user -u colab-studio` servis kaydını gösterir. |

Stüdyo yalnızca kendi açtığı Colab oturumunu (`STUDIO_SESSION`) kapatır. GPU açıkken stüdyo
öldürüldüyse bir sonraki açılışta o runtime'ı kapatır.

## Gizlilik ve güvenlik

- `.env` dosyan (Hugging Face token'ı) ve Colab girişin senin makinende kalır; token yalnızca model
  indirebilmesi için kendi Colab runtime'ına kopyalanır.
- İstemler, yüklemeler ve sonuçlar senin makinende `STUDIO_DATA` altında durur. 30 günden eski
  sohbetler kendiliğinden silinir.
- ComfyUI ağa hiç açılmaz; stüdyo ona kendi SSH tüneliyle ulaşır.
- Başka sitelerden gelen yazma istekleri reddedilir (CSRF koruması).

## Geliştirme

```bash
./test.sh          # lint + Playwright arayüz testleri (sahte API) + pytest; GPU gerekmez
python3 server/tests/gpu_smoke.py   # gerçek GPU'da her moddan bir iş (kredi harcar)
```

| Yol | İçerik |
| --- | --- |
| `server/app.py` | aiohttp web servisi: API ve arayüz |
| `server/runtime.py` | GPU yaşam döngüsü: açma, bağlanma, kurulum, boşta kapanma, kredi |
| `server/studio.py` | iş kuyruğu, sohbetler, medya, temizlik |
| `server/workflows.py` | ComfyUI iş akışı grafikleri |
| `server/web/` | React arayüzü (`src/i18n.ts`, `src/en.ts`: çeviriler) |
| `station/` | GPU'da çalışır: kurulum betiği, model indirici, model kataloğu |
| `mcp/` | MCP sunucusu |
| `deploy/` | systemd servisi ve nginx şablonları |

## Lisans

[AGPL-3.0](LICENSE). Modellerin, ComfyUI'nin ve eklentilerinin kendi lisansları vardır. GPU
kurulumunda indirilen hazır wheel arşivi (L4 için ses paketleri ve SageAttention derleme önbelleği)
üçüncü taraf paketleri kendi lisanslarıyla içerir.
