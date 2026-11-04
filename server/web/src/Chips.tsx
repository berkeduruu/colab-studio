import { t } from "./i18n";
import type { ImageParams, Model3DParams, MusicParams, Preset, SpeechParams, TranscribeParams, VideoParams } from "./api";

// Düzenlemede referans ve çıktının küçültüleceği boyut; çarpan 1080p'ye göre adım başına süre
// (L4'te SageAttention ile ölçüldü, CFG 3; Hızlı/Turbo modda CFG 1). Referans da çıktıyla birlikte işlendiği için büyür.
export const EDIT_RESOLUTIONS: [string, string, string][] = [
  ["720p", "~0.2x", t("~{n} sn/adım", { n: 1.5 })], ["1080p", "1x", t("~{n} sn/adım", { n: 7.8 })], ["1440p", "~1.2x", t("~{n} sn/adım", { n: 9.1 })], ["2160p", "~10x", t("~78 sn/adım (CFG 1: ~16 sn)")],
];

// L4'te ölçülen süresi 30 dk'lık iş sınırını aşan ayarlar (sunucu da reddeder, server/app.py); Turbo önerilir.
export function tooSlowReason(mode: string, video: VideoParams, image: ImageParams, editing: boolean): string | null {
  if (mode === "video" && video.quality === "768p" && video.seconds > 8 && !video.turbo) {
    return t("768p Kaliteli video 8 sn'den uzunsa 30 dk sınırını aşar (9 sn ~31 dk, 10 sn ~35 dk): Turbo'yu seç ya da süreyi 8 sn'ye indir.");
  }
  if (mode === "image" && editing && image.edit_resolution === "2160p" && image.cfg > 1) {
    return t("2160p düzenleme CFG > 1 ile ~33 dk sürer, 30 dk sınırını aşar: Turbo'yu (CFG 1) seç ya da çözünürlüğü düşür.");
  }
  return null;
}

// Yeni görsel boyutları; çarpan 1.5K'ya göre adım başına süre (L4'te SageAttention ile ölçüldü, CFG 3).
export const IMAGE_SIZES: [string, string, string, string][] = [
  ["1K", "~1 MP", "~0.6x", t("~{n} sn/adım", { n: 1.4 })], ["1.5K", "~1.6 MP", "1x", t("~{n} sn/adım", { n: 2.3 })], ["2K", "~4 MP", "~3.4x", t("~{n} sn/adım", { n: 8 })],
];

// TRELLIS.2 şekil ızgarası ve doku türü; toplam süreler L4'te SageAttention ile PBR doku için ölçüldü
// (modeller yüklüyken, kahve fincanı). Köşe boyama çoğunlukla ~1 dk'ya kadar kısa: doku pişirme
// adımları (UV, normal, AO, bake) yok (1024'te ölçüm: köşe boyama 113–173 sn, PBR 162 sn).
export const MODEL3D_RESOLUTIONS: [number, string][] = [[1024, t("~{n} dk", { n: 2.5 })], [1536, t("~{n} dk", { n: 5 })], [2048, t("~{n} dk", { n: 12 })]];
export const MODEL3D_TEXTURES: [Model3DParams["texture"], string, string][] = [
  ["pbr", t("PBR doku"), t("UV açılır; renk, metalik, pürüzlülük, normal ve gölge haritaları pişirilir. Oyun motorlarına ve Blender'a hazır; UV, normal ve AO pişirme adımları süreyi uzatır.")],
  ["vertex", t("Köşe boyama"), t("Renkler doğrudan üçgen köşelerine boyanır; doku pişirme adımları olmadığı için çoğunlukla ~1 dk'ya kadar kısa sürer. Dosya küçük, ama ayrıntı üçgen sayısıyla sınırlı.")],
];
export const MODEL3D_FACES = [50000, 200000, 500000, 1000000];
export const DEFAULT_MODEL3D_PARAMS: Model3DParams = { resolution: 1024, texture: "pbr", faces: 200000, seed: null };
export const facesLabel = (n: number) => (n >= 1000000 ? `${n / 1000000}M` : `${n / 1000}K`);

export function Model3DChips({ params, setParams }: { params: Model3DParams; setParams: (p: Model3DParams) => void }) {
  return (
    <>
      <select className="chip" value={params.resolution} onChange={(e) => setParams({ ...params, resolution: Number(e.target.value) })} title={t("Şekil çözünürlüğü")}>
        {MODEL3D_RESOLUTIONS.map(([r, t]) => <option key={r} value={r}>{r} · {t}</option>)}
      </select>
      <select className="chip" value={params.texture} onChange={(e) => setParams({ ...params, texture: e.target.value as Model3DParams["texture"] })} title={t("Doku")}>
        {MODEL3D_TEXTURES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
      </select>
      <select className="chip" value={params.faces} onChange={(e) => setParams({ ...params, faces: Number(e.target.value) })} title={t("Üçgen sayısı")}>
        {MODEL3D_FACES.map((n) => <option key={n} value={n}>{t("{n} üçgen", { n: facesLabel(n) })}</option>)}
      </select>
    </>
  );
}

const IMAGE_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"];
const VIDEO_RATIOS = ["16:9", "9:16", "1:1"];
// Süre aralıkları; ayar kaydırıcısı da bunları kullanır. Çipte her kaydırıcı değeri bir seçenek olsun diye aynı aralıktan üretilir.
export const VIDEO_SECONDS_RANGE = { min: 2, max: 10, step: 1 };
export const MUSIC_SECONDS_RANGE = { min: 10, max: 360, step: 10 };
const rangeValues = ({ min, max, step }: { min: number; max: number; step: number }) =>
  Array.from({ length: (max - min) / step + 1 }, (_, i) => min + i * step);
const VIDEO_SECONDS = rangeValues(VIDEO_SECONDS_RANGE);

export const DEFAULT_MUSIC_PARAMS: MusicParams = { lyrics: "", seconds: 120, seed: null };
const MUSIC_SECONDS = rangeValues(MUSIC_SECONDS_RANGE);
const musicLabel = (s: number) => (s < 60 ? t("{n} sn", { n: s }) : s % 60 ? t("{m} dk {s} sn", { m: Math.floor(s / 60), s: s % 60 }) : t("{n} dk", { n: s / 60 }));

export function MusicChips({ params, setParams }: { params: MusicParams; setParams: (p: MusicParams) => void }) {
  return (
    <select className="chip" value={params.seconds} onChange={(e) => setParams({ ...params, seconds: Number(e.target.value) })} title={t("En uzun süre")}>
      {MUSIC_SECONDS.map((s) => <option key={s} value={s}>{t("en çok {d}", { d: musicLabel(s) })}</option>)}
    </select>
  );
}

export const DEFAULT_VIDEO_PARAMS: VideoParams = { ratio: "16:9", quality: "480p", seconds: 5, turbo: true, seed: null };

export function ImageChips({ params, setParams, presets, editing }: {
  params: ImageParams;
  setParams: (p: ImageParams) => void;
  presets: Preset[];
  editing: boolean;
}) {
  return (
    <>
      {!editing && (
        <select className="chip" value={params.ratio} onChange={(e) => setParams({ ...params, ratio: e.target.value })} title={t("En-boy oranı")}>
          {IMAGE_RATIOS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      )}
      {editing ? (
        <select
          className="chip"
          value={params.edit_resolution}
          onChange={(e) => setParams({ ...params, edit_resolution: e.target.value })}
          title={t("Düzenleme çözünürlüğü: referans görsel ve sonuç bu boyuta küçültülür")}
        >
          {EDIT_RESOLUTIONS.map(([r, x]) => <option key={r} value={r}>{r} · {x}</option>)}
        </select>
      ) : (
        <select className="chip" value={params.size} onChange={(e) => setParams({ ...params, size: e.target.value })} title={t("Çözünürlük")}>
          {IMAGE_SIZES.map(([k, , x]) => <option key={k} value={k}>{k} · {x}</option>)}
        </select>
      )}
      <select className="chip" value={params.count} onChange={(e) => setParams({ ...params, count: Number(e.target.value) })} title={t("Adet")}>
        {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{t("{n} adet", { n })}</option>)}
      </select>
      <select
        className="chip"
        value=""
        title={t("Ön ayar uygula")}
        onChange={(e) => {
          const p = presets.find((x) => x.id === e.target.value);
          if (p) setParams({ ...p.params });
        }}
      >
        <option value="">{t("Ön ayar")}</option>
        {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
    </>
  );
}

export function VideoChips({ params, setParams }: { params: VideoParams; setParams: (p: VideoParams) => void }) {
  return (
    <>
      <select className="chip" value={params.ratio} onChange={(e) => setParams({ ...params, ratio: e.target.value })} title={t("En-boy oranı")}>
        {VIDEO_RATIOS.map((r) => <option key={r} value={r}>{r}</option>)}
      </select>
      <select className="chip" value={params.quality} onChange={(e) => setParams({ ...params, quality: e.target.value })} title={t("Çözünürlük")}>
        <option value="480p">480p</option>
        <option value="768p">768p</option>
      </select>
      <select className="chip" value={params.seconds} onChange={(e) => setParams({ ...params, seconds: Number(e.target.value) })} title={t("Süre")}>
        {VIDEO_SECONDS.map((s) => <option key={s} value={s}>{t("{n} sn", { n: s })}</option>)}
      </select>
      <button
        className={`chip ${params.turbo ? "on" : ""}`}
        onClick={() => setParams({ ...params, turbo: !params.turbo })}
        title={t("Turbo: 8 adım, ~2.5 kat hızlı. Kapalı: 20 adım, daha ayrıntılı.")}
      >
        {params.turbo ? "Turbo" : t("Kaliteli")}
      </button>
    </>
  );
}

export const SPEECH_LANGUAGES: Record<SpeechParams["engine"], [string, string][]> = {
  chatterbox: [
    ["Turkish", t("Türkçe")], ["English", t("İngilizce")], ["German", t("Almanca")], ["French", t("Fransızca")], ["Spanish", t("İspanyolca")],
    ["Italian", t("İtalyanca")], ["Russian", t("Rusça")], ["Arabic", t("Arapça")], ["Portuguese", t("Portekizce")], ["Dutch", t("Felemenkçe")],
    ["Polish", t("Lehçe")], ["Swedish", t("İsveççe")], ["Danish", t("Danca")], ["Norwegian", t("Norveççe")], ["Finnish", t("Fince")],
    ["Greek", t("Yunanca")], ["Hebrew", t("İbranice")], ["Hindi", t("Hintçe")], ["Japanese", t("Japonca")], ["Korean", t("Korece")],
    ["Chinese", t("Çince")], ["Malay", t("Malayca")], ["Swahili", t("Svahili")],
  ],
  qwen3: [
    ["Auto", t("Otomatik")], ["English", t("İngilizce")], ["Chinese", t("Çince")], ["Japanese", t("Japonca")], ["Korean", t("Korece")],
    ["German", t("Almanca")], ["French", t("Fransızca")], ["Russian", t("Rusça")], ["Portuguese", t("Portekizce")], ["Spanish", t("İspanyolca")],
    ["Italian", t("İtalyanca")],
  ],
};
export const QWEN3_PRESETS = ["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"];

export const DEFAULT_SPEECH_PARAMS: SpeechParams = {
  engine: "chatterbox", language: "Turkish", voice_preset: "Vivian", instruct: "", reference_text: "",
  exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8, seed: null,
};

export function SpeechChips({ params, setParams, cloning }: { params: SpeechParams; setParams: (p: SpeechParams) => void; cloning: boolean }) {
  return (
    <>
      <select
        className="chip"
        value={params.engine}
        title={t("Motor")}
        onChange={(e) => {
          const engine = e.target.value as SpeechParams["engine"];
          setParams({ ...params, engine, language: SPEECH_LANGUAGES[engine][0][0], temperature: engine === "qwen3" ? 0.9 : 0.8 });
        }}
      >
        <option value="chatterbox">{t("Chatterbox (23 dil)")}</option>
        <option value="qwen3">Qwen3-TTS</option>
      </select>
      <select className="chip" value={params.language} onChange={(e) => setParams({ ...params, language: e.target.value })} title={t("Dil")}>
        {SPEECH_LANGUAGES[params.engine].map(([k, label]) => <option key={k} value={k}>{label}</option>)}
      </select>
      {params.engine === "qwen3" && !cloning && (
        <select className="chip" value={params.voice_preset} onChange={(e) => setParams({ ...params, voice_preset: e.target.value })} title={t("Hazır ses")}>
          {QWEN3_PRESETS.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      )}
      {cloning && <span className="chip on" title={t("Eklenen ses dosyasının sesi kullanılır")}>{t("Ses klonlama")}</span>}
    </>
  );
}

export const TRANSCRIBE_LANGUAGES: [string, string][] = [
  ["auto", t("Dili otomatik bul")], ["Turkish", t("Türkçe")], ["English", t("İngilizce")], ["German", t("Almanca")], ["French", t("Fransızca")],
  ["Spanish", t("İspanyolca")], ["Italian", t("İtalyanca")], ["Russian", t("Rusça")], ["Arabic", t("Arapça")], ["Japanese", t("Japonca")],
];

export function TranscribeChips({ params, setParams }: { params: TranscribeParams; setParams: (p: TranscribeParams) => void }) {
  return (
    <select className="chip" value={params.language} onChange={(e) => setParams({ ...params, language: e.target.value })} title={t("Kaydın dili")}>
      {TRANSCRIBE_LANGUAGES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
    </select>
  );
}
