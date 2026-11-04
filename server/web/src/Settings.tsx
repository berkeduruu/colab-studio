import { Dices, Info, Save, Trash2, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { EDIT_RESOLUTIONS, facesLabel, IMAGE_SIZES, MODEL3D_FACES, MODEL3D_RESOLUTIONS, MODEL3D_TEXTURES, MUSIC_SECONDS_RANGE, VIDEO_SECONDS_RANGE } from "./Chips";
import { api, type ImageParams, type Mode, type Model3DParams, type ModelInfo, type MusicParams, type Preset, type SpeechParams, type VideoParams } from "./api";
import { lang, t } from "./i18n";

export const DEFAULT_NEGATIVE =
  "artifacts, gpt-image, washed-out colors, low quality, low resolution, AI slop, deviantart, sloppy lines, rough sketch, blurry, indistinct, missing fingers, badly drawn hands, wrong number of fingers";

export const DEFAULT_PARAMS: ImageParams = {
  ratio: "2:3",
  size: "1.5K",
  count: 1,
  steps: 25,
  cfg: 3,
  turbo: false,
  sampler: "euler",
  scheduler: "simple",
  seed: null,
  negative: DEFAULT_NEGATIVE,
  edit_resolution: "1080p",
};

const SAMPLERS: [string, string][] = [
  ["euler", t("Euler: Noct-Q için önerilen, dengeli ve öngörülebilir")],
  ["euler_ancestral", t("Euler A: her adımda biraz gürültü ekler; daha canlı ama her seferinde biraz farklı")],
  ["res_multistep", t("RES multistep: az adımda keskin sonuç; 15-20 adımda iyi")],
  ["dpmpp_2m", t("DPM++ 2M: ayrıntılı ve temiz; çoğu modelde güvenli seçim")],
  ["dpmpp_2m_sde", t("DPM++ 2M SDE: daha dokulu, gerçekçi fotoğraflarda iyi; biraz yavaş")],
  ["er_sde", t("ER SDE: yumuşak geçişler, portrelerde iyi")],
  ["uni_pc", t("UniPC: çok az adımda (10-15) hızlı önizleme için")],
];

// Hız / kalite düğmeleri: CFG, adım, sampler ve turbo LoRA'yı birlikte ayarlar.
const SPEED_PRESETS: [string, Pick<ImageParams, "cfg" | "steps" | "sampler" | "turbo">][] = [
  ["Turbo", { cfg: 1, steps: 8, sampler: "euler", turbo: true }],
  ["Hızlı", { cfg: 1, steps: 25, sampler: "euler", turbo: false }],
  ["Kaliteli", { cfg: 3, steps: 25, sampler: "euler", turbo: false }],
  ["Taslak", { cfg: 1, steps: 12, sampler: "uni_pc", turbo: false }],
];

const SCHEDULERS: [string, string][] = [
  ["simple", t("Simple: Qwen-Image için önerilen")],
  ["beta", t("Beta: başta ve sonda daha çok adım; ayrıntıyı artırabilir")],
  ["normal", t("Normal: eşit aralıklı")],
  ["karras", t("Karras: son adımlarda ince ayrıntı; SD tarzı modellerde yaygın")],
  ["sgm_uniform", t("SGM uniform: düzgün dağılım, bazı flow modellerinde iyi")],
];

function Field({ label, value, help, children }: { label: string; value?: ReactNode; help: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <label>
        <span className="label-text">
          {label}
          <span className="info" tabIndex={0} aria-label={t("{label} hakkında bilgi", { label })}>
            <Info size={13} />
            <span className="tip" role="tooltip">{help}</span>
          </span>
        </span>
        {value !== undefined && <span className="field-value">{value}</span>}
      </label>
      {children}
    </div>
  );
}

// models.toml'daki model adı ve açıklaması (/api/models); istek bir kez yapılır.
let modelsRequest: Promise<Record<string, ModelInfo>> | null = null;

function ModelField({ mode }: { mode: string }) {
  const [info, setInfo] = useState<ModelInfo | null>(null);
  useEffect(() => {
    (modelsRequest ??= api.models()).then((m) => setInfo(m[mode] ?? null)).catch(() => {
      modelsRequest = null;
    });
  }, [mode]);
  if (!info) return null;
  const d = info.description;
  return (
    <Field label={t("Model")} help={d[lang] ?? d.en ?? Object.values(d)[0] ?? ""}>
      <div>{info.label}</div>
    </Field>
  );
}

// Sunucu seed'i 0 ile 2^32-1 arasında kabul eder; dışındakiler bu aralığa sıkıştırılır.
const SEED_MAX = 2 ** 32 - 1;

function SeedField({ seed, onChange, help }: { seed: number | null; onChange: (s: number | null) => void; help: string }) {
  return (
    <Field label={t("Seed")} help={help}>
      <div className="row">
        <input
          type="number"
          min={0}
          max={SEED_MAX}
          step={1}
          placeholder={t("rastgele")}
          value={seed ?? ""}
          onChange={(e) => onChange(e.target.value === "" ? null : Math.min(SEED_MAX, Math.max(0, Math.floor(Number(e.target.value)))))}
        />
        <button className="icon-btn" title={t("Rastgele")} aria-label={t("Rastgele seed")} onClick={() => onChange(null)}><Dices size={16} /></button>
      </div>
    </Field>
  );
}

interface Props {
  mode: Mode;
  params: ImageParams;
  setParams: (p: ImageParams) => void;
  videoParams: VideoParams;
  setVideoParams: (p: VideoParams) => void;
  musicParams: MusicParams;
  setMusicParams: (p: MusicParams) => void;
  speechParams: SpeechParams;
  setSpeechParams: (p: SpeechParams) => void;
  model3dParams: Model3DParams;
  setModel3DParams: (p: Model3DParams) => void;
  presets: Preset[];
  editing: boolean;
  onSavePreset: (name: string) => void;
  onDeletePreset: (id: string) => void;
  onClose: () => void;
}

const TITLES: Record<Mode, string> = {
  image: t("Görsel ayarları"), video: t("Video ayarları"), model3d: t("3D model ayarları"), music: t("Müzik ayarları"), speech: t("Seslendirme ayarları"), transcribe: t("Yazıya dökme"),
};

export function Settings(props: Props) {
  return (
    <aside className="settings">
      <h3>{TITLES[props.mode]} <button className="icon-btn" onClick={props.onClose} aria-label={t("Kapat")}><X size={16} /></button></h3>
      {props.mode === "image" && <ImageSettings {...props} />}
      {props.mode === "video" && <VideoSettings params={props.videoParams} setParams={props.setVideoParams} />}
      {props.mode === "model3d" && <Model3DSettings params={props.model3dParams} setParams={props.setModel3DParams} />}
      {props.mode === "music" && <MusicSettings params={props.musicParams} setParams={props.setMusicParams} />}
      {props.mode === "speech" && <SpeechSettings params={props.speechParams} setParams={props.setSpeechParams} />}
      {props.mode === "transcribe" && <TranscribeSettings />}
    </aside>
  );
}

function ImageSettings({ params, setParams, presets, editing, onSavePreset, onDeletePreset }: Props) {
  const [presetName, setPresetName] = useState("");
  const set = (patch: Partial<ImageParams>) => setParams({ ...params, ...patch });
  const samplerHelp = SAMPLERS.find(([k]) => k === params.sampler)?.[1];
  const schedulerHelp = SCHEDULERS.find(([k]) => k === params.scheduler)?.[1];

  return (
    <>
      <ModelField mode="image" />
      <Field label={t("Hız / kalite")} help={t("Turbo: 8 adımlık LoRA ile ~5 kat hızlı (1.5K ≈ 12 sn). Hızlı: CFG 1, negatif istem yok sayılır, yaklaşık 2 kat hızlı (~30 sn). Kaliteli: CFG 3, negatif istem devrede (~1 dk). Taslak: 12 adım UniPC, hızlı önizleme.")}>
        <div className="row">
          {SPEED_PRESETS.map(([name, p]) => (
            <button
              key={name}
              className={`chip ${params.cfg === p.cfg && params.steps === p.steps && params.sampler === p.sampler && Boolean(params.turbo) === Boolean(p.turbo) ? "on" : ""}`}
              onClick={() => set(p)}
            >
              {t(name)}
            </button>
          ))}
        </div>
      </Field>
      {!editing && (
        <Field
          label={t("Çözünürlük")}
          value={params.size}
          help={t("Toplam piksel; oran sohbet kutusundaki oran seçiminden gelir. Süre (CFG 3; Hızlı modda yarısı): {list}. 25 adımlık 1.5K ≈ 1 dk.", { list: IMAGE_SIZES.map(([k, mp, x, sec]) => `${k} (${mp}) ${x}, ${sec}`).join(" · ") })}
        >
          <div className="row">
            {IMAGE_SIZES.map(([k, , x, sec]) => (
              <button key={k} className={`chip ${params.size === k ? "on" : ""}`} onClick={() => set({ size: k })} title={t("{x} süre, {sec}", { x, sec })}>
                {k}
              </button>
            ))}
          </div>
        </Field>
      )}
      {editing && (
        <Field
          label={t("Düzenleme çözünürlüğü")}
          value={params.edit_resolution}
          help={t("Eklediğin görsel ve sonuç bu boyuta küçültülür, oran görselden gelir. Referans da işlendiği için düzenleme aynı boyuttaki üretimden ağırdır. Süre (CFG 3; Hızlı modda yarısı): {list}. 25 adımlık 1080p ≈ 3.5 dk.", { list: EDIT_RESOLUTIONS.map(([r, x, sec]) => `${r} ${x}, ${sec}`).join(" · ") })}
        >
          <div className="row">
            {EDIT_RESOLUTIONS.map(([r, x, sec]) => (
              <button key={r} className={`chip ${params.edit_resolution === r ? "on" : ""}`} onClick={() => set({ edit_resolution: r })} title={t("{x} süre, {sec}", { x, sec })}>
                {r}
              </button>
            ))}
          </div>
        </Field>
      )}
      {/* Turbo LoRA 8 adım / CFG 1 için damıtıldı; elle değiştirilince kapanır (yoksa hiçbir hız düğmesi seçili görünmeden açık kalırdı). */}
      <Field label={t("Adım")} value={params.steps} help={t("Gürültüden görsele kaç adımda gidileceği. Fazlası daha ayrıntılı ama orantılı yavaş; 20-30 yeterli, 40 üstü nadiren fark eder.")}>
        <input type="range" min={8} max={50} step={1} value={params.steps} onChange={(e) => set({ steps: Number(e.target.value), turbo: false })} />
      </Field>
      <Field label="CFG" value={params.cfg.toFixed(1)} help={t("İsteme ne kadar sıkı uyulacağı. 1: negatif istem kapalı ve 2 kat hızlı. 2.5-4: dengeli. Çok yüksek değerler yanık, aşırı doygun renk verir.")}>
        <input type="range" min={1} max={8} step={0.5} value={params.cfg} onChange={(e) => set({ cfg: Number(e.target.value), turbo: false })} />
      </Field>
      <Field label="Sampler" help={samplerHelp}>
        <select value={params.sampler} onChange={(e) => set({ sampler: e.target.value })}>
          {SAMPLERS.map(([k]) => <option key={k} value={k}>{k}</option>)}
        </select>
      </Field>
      <Field label="Scheduler" help={schedulerHelp}>
        <select value={params.scheduler} onChange={(e) => set({ scheduler: e.target.value })}>
          {SCHEDULERS.map(([k]) => <option key={k} value={k}>{k}</option>)}
        </select>
      </Field>
      <SeedField
        seed={params.seed}
        onChange={(seed) => set({ seed })}
        help={t("Aynı seed + aynı ayarlar = aynı görsel. Beğendiğin bir sonucun seed'ini sabitleyip yalnızca istemi değiştirerek kontrollü varyasyon alırsın. Birden çok adet üretilirse seed her görselde 1 artar.")}
      />
      <Field label={t("Negatif istem")} value={params.cfg === 1 ? t("CFG 1'de etkisiz") : undefined} help={t("Görselde istemediklerin (İngilizce daha etkili). Yalnızca CFG 1'den büyükken çalışır.")}>
        <AutoTextarea value={params.negative} onChange={(v) => set({ negative: v })} />
        <button className="btn ghost" style={{ alignSelf: "flex-start" }} onClick={() => set({ negative: DEFAULT_NEGATIVE })}>{t("Varsayılana dön")}</button>
      </Field>
      <Field label={t("Ön ayarlar")} help={t("Bu paneldeki tüm ayarları adıyla kaydeder; sohbet kutusundaki 'Ön ayar' menüsünden tek tıkla uygularsın.")}>
        {presets.map((p) => (
          <div key={p.id} className="preset">
            <button onClick={() => setParams({ ...DEFAULT_PARAMS, ...p.params })} title={t("Uygula")}>{p.name}</button>
            <button className="icon-btn" aria-label={t("Sil")} onClick={() => onDeletePreset(p.id)}><Trash2 size={14} /></button>
          </div>
        ))}
        <div className="row">
          <input type="text" placeholder={t("Ön ayar adı")} value={presetName} onChange={(e) => setPresetName(e.target.value)} />
          <button
            className="icon-btn"
            aria-label={t("Kaydet")}
            title={t("Mevcut ayarları kaydet")}
            disabled={!presetName.trim()}
            onClick={() => {
              onSavePreset(presetName.trim());
              setPresetName("");
            }}
          >
            <Save size={16} />
          </button>
        </div>
      </Field>
      <button className="btn ghost" onClick={() => setParams({ ...DEFAULT_PARAMS })}>{t("Tüm ayarları sıfırla")}</button>
    </>
  );
}

function VideoSettings({ params, setParams }: { params: VideoParams; setParams: (p: VideoParams) => void }) {
  const set = (patch: Partial<VideoParams>) => setParams({ ...params, ...patch });
  return (
    <>
      <ModelField mode="video" />
      <Field label={t("Mod")} help={t("Turbo: 8 adımlık LoRA ile ~2.5 kat hızlı, çoğu sahne için yeterli. Kaliteli: 20 adım, hareket ve ayrıntı daha tutarlı ama uzun sürer.")}>
        <div className="row">
          <button className={`chip ${params.turbo ? "on" : ""}`} onClick={() => set({ turbo: true })}>{t("Turbo (8 adım)")}</button>
          <button className={`chip ${!params.turbo ? "on" : ""}`} onClick={() => set({ turbo: false })}>{t("Kaliteli (20 adım)")}</button>
        </div>
      </Field>
      <Field label={t("Çözünürlük")} help={t("480p L4'te hızlıdır (~3.5 dk). 768p modelin doğal çözünürlüğü; 10 sn 768p Turbo'da ~15 dk sürer; Kaliteli (20 adım) modda 8 sn ~25 dk, daha uzunu 30 dk sınırını aşar.")}>
        <div className="row">
          {["480p", "768p"].map((q) => (
            <button key={q} className={`chip ${params.quality === q ? "on" : ""}`} onClick={() => set({ quality: q })}>{q}</button>
          ))}
        </div>
      </Field>
      <Field label={t("Süre")} value={t("{n} sn", { n: params.seconds })} help={t("Model 17 karelik bloklarla çalışır; süre en yakın geçerli uzunluğa yuvarlanır. Uzun video süreyi doğrusal artırır; 5 sn eğitimdeki tipik uzunluk.")}>
        <input type="range" {...VIDEO_SECONDS_RANGE} value={params.seconds} onChange={(e) => set({ seconds: Number(e.target.value) })} />
      </Field>
      <SeedField seed={params.seed} onChange={(seed) => set({ seed })} help={t("Aynı seed ve ayarlarla aynı video çıkar. Beğendiğin bir sahneyi sabitleyip istemi ince ayarlamak için kullan.")} />
      <div className="help">
        {t('İpucu: İstemde sahneyi, kamera hareketini ve sesi ayrı ayrı anlat. Zaman çizelgesi yazabilirsin, örneğin "[0s-2s] yakın çekim… [2s-5s] kamera geri çekilir…". Bir görsel eklersen ilk kare, ikinci görsel son kare olur.')}
      </div>
    </>
  );
}

function Model3DSettings({ params, setParams }: { params: Model3DParams; setParams: (p: Model3DParams) => void }) {
  const set = (patch: Partial<Model3DParams>) => setParams({ ...params, ...patch });
  const textureHelp = MODEL3D_TEXTURES.map(([, label, help]) => `${label}: ${help}`).join(" ");
  return (
    <>
      <ModelField mode="model3d" />
      <Field
        label={t("Şekil çözünürlüğü")}
        value={params.resolution}
        help={t("Şeklin oluşturulduğu voksel ızgarası. Yükseldikçe ince ayrıntılar (yazı, kenar, kabartma) netleşir ama süre ve bellek artar. Toplam süre (PBR doku ile): {list}. GPU açıldıktan sonraki ilk 3D işi modelleri yüklediği için ~30 sn daha uzun sürer (1024'te ölçüm: 194.3 sn soğuk, 162.2 sn sıcak).", { list: MODEL3D_RESOLUTIONS.map(([r, d]) => `${r} ${d}`).join(" · ") })}
      >
        <div className="row">
          {MODEL3D_RESOLUTIONS.map(([r, d]) => (
            <button key={r} className={`chip ${params.resolution === r ? "on" : ""}`} onClick={() => set({ resolution: r })} title={d}>{r}</button>
          ))}
        </div>
      </Field>
      <Field label={t("Doku")} help={textureHelp}>
        <div className="row">
          {MODEL3D_TEXTURES.map(([k, label]) => (
            <button key={k} className={`chip ${params.texture === k ? "on" : ""}`} onClick={() => set({ texture: k })}>{label}</button>
          ))}
        </div>
      </Field>
      <Field label={t("Üçgen sayısı")} value={facesLabel(params.faces)} help={t("Ağ bu sayıya sadeleştirilir. 50K: web ve oyun için hafif. 200K: dengeli. 500K-1M: baskı ve yakın çekim için ayrıntılı ama dosya büyür, görüntüleyici yavaşlar.")}>
        <div className="row">
          {MODEL3D_FACES.map((n) => (
            <button key={n} className={`chip ${params.faces === n ? "on" : ""}`} onClick={() => set({ faces: n })}>{facesLabel(n)}</button>
          ))}
        </div>
      </Field>
      <SeedField seed={params.seed} onChange={(seed) => set({ seed })} help={t("Aynı görsel, seed ve ayarlarla aynı model çıkar. Görünmeyen yüzler (arka taraf) seed'e göre farklı tahmin edilir; beğenmezsen yeni seed dene.")} />
      <div className="help">
        {t("İpucu: Nesnenin tamamı görünsün, kesilmesin; düz ya da sade arka plan ve yumuşak ışık en iyisi. Görsel modunda ürettiğin bir görselin üstündeki küp düğmesiyle onu doğrudan buraya gönderebilirsin.")}
      </div>
    </>
  );
}

function MusicSettings({ params, setParams }: { params: MusicParams; setParams: (p: MusicParams) => void }) {
  const set = (patch: Partial<MusicParams>) => setParams({ ...params, ...patch });
  return (
    <>
      <ModelField mode="music" />
      <Field label={t("En uzun süre")} value={t("{n} sn", { n: params.seconds })} help={t("Üst sınırdır; model sözler bitince daha erken durabilir. Uzun şarkılar üretim süresini artırır.")}>
        <input type="range" {...MUSIC_SECONDS_RANGE} value={params.seconds} onChange={(e) => set({ seconds: Number(e.target.value) })} />
      </Field>
      <SeedField seed={params.seed} onChange={(seed) => set({ seed })} help={t("Aynı seed ve ayarlarla aynı şarkı çıkar.")} />
      <div className="help">
        {t("İpucu: Tarz kutusuna dil, vokal türü, tür, tempo (BPM) ve enstrümanları yaz. Sözlerde [Verse], [Chorus], [Bridge] etiketleri yapıyı belirler. Sözleri boş bırakırsan enstrümantal olur.")}
      </div>
    </>
  );
}

function SpeechSettings({ params, setParams }: { params: SpeechParams; setParams: (p: SpeechParams) => void }) {
  const set = (patch: Partial<SpeechParams>) => setParams({ ...params, ...patch });
  const chatterbox = params.engine === "chatterbox";
  return (
    <>
      <Field
        label={t("Motor")}
        help={t("Chatterbox: 23 dil (Türkçe dahil), herhangi bir sesi birkaç saniyelik örnekten klonlar, duygu yoğunluğu ayarlanabilir. Qwen3-TTS: 10 dil (Türkçe yok), 9 hazır ses ve 'nasıl konuşsun' talimatı; klonlamada referans metni verilirse daha isabetli.")}
      >
        <div className="row">
          <button className={`chip ${chatterbox ? "on" : ""}`} onClick={() => set({ engine: "chatterbox", language: "Turkish", temperature: 0.8 })}>Chatterbox</button>
          <button className={`chip ${!chatterbox ? "on" : ""}`} onClick={() => set({ engine: "qwen3", language: "Auto", temperature: 0.9 })}>Qwen3-TTS</button>
        </div>
      </Field>
      {chatterbox && (
        <Field label={t("Duygu yoğunluğu")} value={params.exaggeration.toFixed(2)} help={t("0.5 doğal konuşma. Yükseldikçe daha coşkulu ve dramatik; 1'in üstü abartılı olabilir. Yüksek değerlerde konuşma da hızlanır, tempoyu dengelemek için CFG'yi düşür.")}>
          <input type="range" min={0.25} max={2} step={0.05} value={params.exaggeration} onChange={(e) => set({ exaggeration: Number(e.target.value) })} />
        </Field>
      )}
      {chatterbox && (
        <Field label={t("CFG / tempo")} value={params.cfg_weight.toFixed(2)} help={t("Referans sese ve metne ne kadar sıkı uyulacağı. Düşük değer (0.3) daha yavaş, sakin ve akıcı konuşma verir; hızlı konuşan bir referans seste düşürmek iyi olur. 0 dil aksanını referanstan taşımaz.")}>
          <input type="range" min={0} max={1} step={0.05} value={params.cfg_weight} onChange={(e) => set({ cfg_weight: Number(e.target.value) })} />
        </Field>
      )}
      <Field label={t("Sıcaklık")} value={params.temperature.toFixed(2)} help={t("Çeşitlilik. Düşük: tutarlı ve düz; yüksek: daha canlı tonlama ama hata (yanlış vurgu, takılma) olasılığı artar. Chatterbox için 0.8, Qwen3 için 0.9 iyi başlangıç.")}>
        <input type="range" min={0.05} max={1.5} step={0.05} value={params.temperature} onChange={(e) => set({ temperature: Number(e.target.value) })} />
      </Field>
      {!chatterbox && (
        <Field label={t("Konuşma talimatı")} help={t("Hazır seslerde nasıl konuşacağını tarif et (İngilizce/Çince en iyi): 'Speak warmly and slowly', 'Excited, fast-paced sports commentary'. Klonlamada kullanılmaz.")}>
          <AutoTextarea value={params.instruct} onChange={(v) => set({ instruct: v })} />
        </Field>
      )}
      <Field label={t("Referans sesin metni")} help={t("Ses klonlarken eklediğin kayıtta tam olarak ne söylendiğini yazarsan Qwen3 sesi çok daha isabetli kopyalar. Boş bırakırsan yalnızca ses rengi kullanılır. Chatterbox metne ihtiyaç duymaz.")}>
        <AutoTextarea value={params.reference_text} onChange={(v) => set({ reference_text: v })} />
      </Field>
      <SeedField seed={params.seed} onChange={(seed) => set({ seed })} help={t("Aynı seed ve ayarlarla aynı okuma çıkar. Beğenmediğin bir okumayı yeni seed ile tekrar dene.")} />
      <div className="help">
        {t("İpucu: Klonlama için 5-15 saniyelik, tek kişinin konuştuğu, müziksiz ve yankısız bir kayıt ekle. Uzun metinler otomatik olarak cümlelere bölünüp birleştirilir. Metinde [pause:1s] ile duraklama ekleyebilirsin.")}
      </div>
    </>
  );
}

function TranscribeSettings() {
  return (
    <>
      <Field label={t("Model")} help={t("OpenAI Whisper large-v3-turbo: large-v3 kalitesine yakın, ~8 kat hızlı. 99 dil; Türkçe'de iyi sonuç verir.")}>
        <div>Whisper large-v3-turbo</div>
      </Field>
      <div className="help">
        {t("Dil: Kaydın dilini biliyorsan seç; otomatik algılama kısa kayıtlarda yanılabilir. Uzun kayıtlar otomatik olarak 30 saniyelik parçalarla işlenir. Sonuç metni kopyalayabilir ya da .txt olarak indirebilirsin.")}
      </div>
    </>
  );
}

// Yazının tamamı görünsün diye yüksekliği içeriğe göre ayarlanır.
function AutoTextarea({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [value]);
  return <textarea ref={ref} className="autogrow" value={value} onChange={(e) => onChange(e.target.value)} />;
}
