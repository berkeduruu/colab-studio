import { Box, Copy, Download, Loader2, Lock, Pencil, RefreshCw, X } from "lucide-react";
import { isDone, isFailed, isPending, mediaUrl, type Job, type Mode, type RuntimeState } from "./api";
import { facesLabel, MODEL3D_TEXTURES } from "./Chips";
import { isAudio } from "./Composer";
import { downloadText, formatDuration, pct } from "./format";
import { lang, t, tServer } from "./i18n";
import { Model3DTile } from "./Model3D";

const EMPTY: Record<Mode, { title: string; text: string; suggestions: string[] }> = {
  image: {
    title: t("Ne üretmek istiyorsun?"),
    text: t('Bir istem yaz ya da düzenlemek istediğin görseli ekle. Türkçe yazabilirsin; "İstemi geliştir" ayrıntılı bir istem hazırlar.'),
    suggestions: [
      t("Yağmurlu İstanbul sokağı, neon tabelalar, sinematik gece fotoğrafı"),
      t("Ahşap masada seramik kahve fincanı, sabah ışığı, ürün çekimi"),
      t("Sisli çam ormanında yürüyen kırmızı montlu bir kadın, geniş açı"),
    ],
  },
  video: {
    title: t("Hangi sahneyi çekelim?"),
    text: t("MiniMax H3 sesiyle birlikte video üretir. Sahneyi, kamera hareketini ve sesi anlat. Bir görsel eklersen ilk kare, ikinciyi eklersen son kare olur."),
    suggestions: [
      t("Boğaz'da gün batımında ilerleyen bir vapur, martı sesleri, yavaş kamera kaydırma"),
      t("Yağmurlu gece sokağında şemsiyeli bir adam yürüyor, ayak sesleri ve uzaktan caz müziği"),
      t("Mutfakta tavada cızırdayan yumurta, yakın çekim, yağ cızırtısı"),
    ],
  },
  model3d: {
    title: t("Hangi nesneyi 3D yapalım?"),
    text: t("TRELLIS.2 tek bir görselden dokulu 3D model (GLB) üretir; arka planı kendisi ayırır. Görseli ekle ya da yapıştır; sonucu sohbette döndürüp tam ekran görüntüleyicide inceleyebilir, Blender ya da oyun motoru için indirebilirsin. Görsel modunda ürettiğin bir görselin küp düğmesi onu buraya gönderir."),
    suggestions: [],
  },
  speech: {
    title: t("Ne seslendirelim?"),
    text: t("Metni yaz, dili ve motoru seç. Bir ses kaydı eklersen o ses klonlanır (5-15 sn'lik temiz kayıt en iyisi). Türkçe için Chatterbox kullan."),
    suggestions: [
      t("Merhaba! Bugün size yeni ürünümüzü tanıtmak istiyorum. Hazırsanız başlayalım."),
      t("Bir varmış bir yokmuş, evvel zaman içinde, kalbur saman içinde..."),
      "Welcome back to the channel. Today we are building something special.",
    ],
  },
  transcribe: {
    title: t("Hangi kaydı yazıya dökelim?"),
    text: t("Ses dosyasını ekle (MP3, WAV, M4A, OGG, FLAC, WebM). Whisper large-v3-turbo metne çevirir. Kutuya isteğe bağlı bir not ya da başlık yazabilirsin."),
    suggestions: [],
  },
  music: {
    title: t("Nasıl bir şarkı?"),
    text: t("YuE2 vokalli ve enstrümantal şarkı üretir. Kutuya tarzı yaz, sözleri üstteki alana [Verse] ve [Chorus] etiketleriyle ekle."),
    suggestions: [
      t("Türkçe, sıcak erkek vokal, akustik folk, 90 BPM, bağlama ve gitar"),
      "English, female vocal, upbeat indie pop, 120 BPM, bright guitars, punchy drums",
      "Instrumental lo-fi hip hop, 80 BPM, mellow piano, vinyl crackle",
    ],
  },
};

interface Props {
  mode: Mode;
  jobs: Job[];
  rt: RuntimeState | null;
  onSuggestion: (text: string) => void;
  onRetry: (job: Job) => void;
  onEdit: (name: string) => void;
  onReusePrompt: (job: Job) => void;
  onUseSettings: (job: Job) => void;
  onCancel: (job: Job) => void;
  onZoom: (url: string) => void;
  onOpen3D: (url: string) => void;
  onMake3D: (name: string) => void;
}

const KIND_LABELS: Record<string, string> = {
  image: t("Görsel"), video: t("Video"), model3d: t("3D model"), music: t("Müzik"), speech: t("Seslendirme"), transcribe: t("Yazıya dökme"), enhance: t("İstem geliştirme"),
};

function queuedText(job: Job, rt: RuntimeState | null) {
  const q = rt?.queue;
  if (!q) return t("Sırada");
  // Yeni gönderilen iş, sıra durumu yenilenene kadar (~3 sn) listede olmayabilir; o zaman önündeki iş sayısı bilinmez.
  const index = q.order.indexOf(job.id);
  const ahead = index < 0 ? "" : ` · ${t("önünde {n} iş", { n: index })}`;
  const cur = q.current;
  const now = !cur
    ? ""
    : cur.status === "waiting_gpu"
      ? ` · ${t("şu an: GPU açılıyor")}`
      : ` · ${t("şu an: {kind} — {stage} {pct}", { kind: KIND_LABELS[cur.kind] ?? cur.kind, stage: cur.stage ? tServer(cur.stage) : t("başlıyor"), pct: pct(cur.progress * 100) })}`;
  const startsIn = q.starts_in?.[job.id];
  return `${t("Sırada")}${ahead}${startsIn != null ? ` · ${t("~{d} sonra başlar", { d: formatDuration(startsIn) })}` : ""}${now}`;
}

function statusText(job: Job, rt: RuntimeState | null) {
  if (job.status === "queued") return queuedText(job, rt);
  if (job.status === "waiting_gpu") {
    const s = rt?.startup;
    return s ? t("GPU açılıyor: {step} · {pct} · ~{left} kaldı", { step: tServer(s.steps[s.step]), pct: pct(s.progress * 100), left: formatDuration(s.remaining_seconds) }) : t("GPU açılıyor");
  }
  if (job.status === "running") {
    const eta = job.live?.eta;
    const left = eta == null ? ` · ${t("süre ilk kez ölçülüyor")}` : eta > 0 ? ` · ${t("~{d} kaldı", { d: formatDuration(eta) })}` : ` · ${t("bitmek üzere")}`;
    return `${tServer(job.stage ?? "Çalışıyor")} · ${pct(job.progress * 100)}${left}`;
  }
  return "";
}

/** İşin gerçekten ilerlediğini gösteren ikinci satır: aşamanın süresi (genelde ne kadar sürdüğüyle),
 * GPU kullanımı; aşama olağandan çok uzun sürüyorsa uyarı. */
function statusDetail(job: Job, rt: RuntimeState | null): { text: string; warn: boolean } | null {
  if (job.status === "waiting_gpu") {
    const s = rt?.startup;
    if (!s) return null;
    const parts = [s.step_elapsed_seconds != null ? t("bu adımda {d}", { d: formatDuration(s.step_elapsed_seconds) }) : null,
      s.setup ? t("kurulum {step}", { step: tServer(s.setup) }) : null,
      s.download ? t("{label} modelleri {progress}", { label: tServer(rt?.packs?.[s.pack ?? "image"]?.label ?? "Görsel").toLocaleLowerCase(lang), progress: downloadText(s.download) }) : null];
    return { text: parts.filter(Boolean).join(" · "), warn: false };
  }
  if (job.status !== "running" || !job.live) return null;
  const l = job.live;
  const g = rt?.gpu_now;
  const parts: string[] = [];
  if (l.stage_seconds != null) {
    parts.push(`${t("bu aşamada {d}", { d: formatDuration(l.stage_seconds) })}${l.stage_expected ? ` (${t("genelde ~{d}", { d: formatDuration(l.stage_expected) })})` : ""}`);
  }
  if (g) parts.push(`GPU ${pct(g.gpu)} · VRAM ${(g.vram_used / 1024 ** 3).toFixed(1)}/${(g.vram_total / 1024 ** 3).toFixed(0)} GB`);
  const slow = l.stage_seconds != null && l.stage_expected != null && l.stage_seconds > l.stage_expected * 2 + 60;
  if (slow) parts.push(g && g.gpu < 5 ? t("beklenenden çok uzun sürüyor ve GPU boşta; takılmış olabilir, iptal edip yeniden dene") : t("beklenenden uzun sürüyor ama çalışıyor"));
  return parts.length ? { text: parts.join(" · "), warn: slow } : null;
}

function meta(job: Job) {
  const p = job.params;
  const took = job.finished_at ? t("{n} sn sürdü", { n: Math.round(job.finished_at - job.created_at) }) : null;
  if (job.kind === "speech") {
    const engine = p.engine === "qwen3" ? "Qwen3-TTS" : "Chatterbox";
    const voice = job.inputs.length ? t("klonlanmış ses") : p.engine === "qwen3" ? p.voice_preset : t("varsayılan ses");
    return [engine, p.language, voice, p.seed !== null ? `seed ${p.seed}` : null, took].filter(Boolean).join(" · ");
  }
  if (job.kind === "transcribe") {
    return ["Whisper large-v3-turbo", p.language === "auto" ? t("dil otomatik") : p.language, took].filter(Boolean).join(" · ");
  }
  if (job.kind === "music") {
    return [p.lyrics ? t("sözlü") : t("enstrümantal"), t("en çok {d}", { d: t("{n} sn", { n: p.seconds ?? 0 }) }), p.seed !== null ? `seed ${p.seed}` : null, took]
      .filter(Boolean)
      .join(" · ");
  }
  if (job.kind === "model3d") {
    const texture = MODEL3D_TEXTURES.find(([k]) => k === p.texture)?.[1];
    return ["TRELLIS.2", t("şekil {n}", { n: p.resolution ?? 0 }), texture, p.faces ? t("{n} üçgen", { n: facesLabel(p.faces) }) : null, p.seed !== null ? `seed ${p.seed}` : null, took]
      .filter(Boolean)
      .join(" · ");
  }
  if (job.kind === "video") {
    return [`${p.width}×${p.height}`, t("{n} sn", { n: p.seconds ?? 0 }), p.turbo ? "turbo" : t("20 adım"), p.seed !== null ? `seed ${p.seed}` : null, took]
      .filter(Boolean)
      .join(" · ");
  }
  const parts = [
    job.inputs.length ? `${t("Düzenleme")}${p.edit_resolution ? ` · ${p.edit_resolution}` : ""}` : `${p.width}×${p.height}`,
    t("{n} adım", { n: p.steps ?? 0 }),
    `CFG ${p.cfg}`,
    p.seed !== null ? `seed ${p.seed}` : null,
    job.finished_at ? t("{n} sn", { n: Math.round(job.finished_at - job.created_at) }) : null,
  ];
  return parts.filter(Boolean).join(" · ");
}

export function Feed({ mode, jobs, rt, onSuggestion, onRetry, onEdit, onReusePrompt, onUseSettings, onCancel, onZoom, onOpen3D, onMake3D }: Props) {
  if (jobs.length === 0) {
    return (
      <div className="empty">
        <h2>{EMPTY[mode].title}</h2>
        <p>{EMPTY[mode].text}</p>
        <div className="suggestions">
          {EMPTY[mode].suggestions.map((s) => (
            <button key={s} className="suggestion" onClick={() => onSuggestion(s)}>{s}</button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <>
      {jobs.map((job) => (
        <div key={job.id} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="bubble">
            {job.inputs.length > 0 && (
              <div className="refs">
                {job.inputs.map((n) => (isAudio(n) ? <audio key={n} src={mediaUrl(n)} controls preload="metadata" /> : <img key={n} src={mediaUrl(n)} alt="" />))}
              </div>
            )}
            {job.prompt}
          </div>
          <div className="result">
            {isDone(job) && (
              <>
                {job.kind === "transcribe" ? (
                  <div className="transcript">
                    <p>{job.text}</p>
                    <div className="row">
                      <button className="btn ghost" onClick={() => navigator.clipboard.writeText(job.text ?? "")}><Copy size={14} /> {t("Metni kopyala")}</button>
                      <a className="btn ghost" href={`data:text/plain;charset=utf-8,${encodeURIComponent(job.text ?? "")}`} download={`${t("dokum")}-${job.id.slice(0, 6)}.txt`}>
                        <Download size={14} /> {t(".txt indir")}
                      </a>
                    </div>
                  </div>
                ) : job.kind === "music" || job.kind === "speech" ? (
                  job.outputs.map((name) => (
                    <div key={name} className="audio-row">
                      <audio src={mediaUrl(name)} controls preload="metadata" />
                      <a className="icon-btn" title={t("İndir")} href={mediaUrl(name)} download><Download size={15} /></a>
                    </div>
                  ))
                ) : job.kind === "model3d" ? (
                  job.outputs.map((name) => <Model3DTile key={name} src={mediaUrl(name)} onOpen={() => onOpen3D(mediaUrl(name))} />)
                ) : job.kind === "video" ? (
                  job.outputs.map((name) => (
                    <div key={name} className="tile video">
                      <video src={mediaUrl(name)} controls playsInline preload="metadata" />
                      <div className="tile-actions">
                        <a title={t("İndir")} href={mediaUrl(name)} download><Download size={15} /></a>
                      </div>
                    </div>
                  ))
                ) : (
                <div className={`grid n${Math.min(job.outputs.length, 4)}`}>
                  {job.outputs.map((name) => (
                    <div key={name} className="tile">
                      {/* Büyütme gerçek bir düğme: klavyeyle odaklanır, odak halkası görünür. */}
                      <button className="thumb" aria-label={t("Görseli büyüt")} onClick={() => onZoom(mediaUrl(name))}>
                        <img src={mediaUrl(name)} alt="" loading="lazy" />
                      </button>
                      <div className="tile-actions">
                        <button title={t("Bu görseli düzenle")} onClick={() => onEdit(name)}><Pencil size={15} /></button>
                        <button title={t("Bu görselden 3D model yap")} aria-label={t("Bu görselden 3D model yap")} onClick={() => onMake3D(name)}><Box size={15} /></button>
                        <a title={t("İndir")} href={mediaUrl(name)} download><Download size={15} /></a>
                      </div>
                    </div>
                  ))}
                </div>
                )}
                <div className="actions">
                  <button className="btn ghost" onClick={() => onRetry(job)}><RefreshCw size={14} /> {t("Yeniden üret")}</button>
                  <button className="btn ghost" onClick={() => onReusePrompt(job)}><Copy size={14} /> {t("İstemi kullan")}</button>
                  <button className="btn ghost" onClick={() => onUseSettings(job)} title={t("Bu sonucun seed'ini ve ayarlarını geri yükler; istemi değiştirip kontrollü varyasyon alırsın")}>
                    <Lock size={14} /> {t("Seed'i sabitle")}
                  </button>
                </div>
                <div className="meta">{meta(job)}</div>
              </>
            )}
            {isFailed(job) && (
              <>
                <div className="error-text" role="alert">{tServer(job.error)}</div>
                <div className="actions">
                  <button className="btn ghost" onClick={() => onRetry(job)}><RefreshCw size={14} /> {t("Tekrar dene")}</button>
                </div>
              </>
            )}
            {isPending(job) && (
              <>
                <div className="status">
                  <Loader2 size={15} className="spin" /> <span className="status-text" role="status">{statusText(job, rt)}</span>
                  <button className="btn ghost cancel" onClick={() => onCancel(job)} title={t("İşi durdurur; GPU açık kalır")}>
                    <X size={14} /> {t("İptal")}
                  </button>
                </div>
                {/* Süresi henüz bilinmeyen ve adım bildirmeyen aşamada (ilk kez model yükleme) çubuk
                    %0'da durmak yerine hareket eder: iş sürüyor. */}
                <div className={`progress ${job.status === "running" && job.live?.eta == null && job.progress === 0 ? "indeterminate" : ""}`}>
                  <div style={{ width: `${Math.round((job.status === "waiting_gpu" ? rt?.startup?.progress ?? 0 : job.progress) * 100)}%` }} />
                </div>
                {(() => {
                  const d = statusDetail(job, rt);
                  return d && <div className={`status-sub ${d.warn ? "warn" : ""}`}>{d.text}</div>;
                })()}
              </>
            )}
          </div>
        </div>
      ))}
    </>
  );
}
