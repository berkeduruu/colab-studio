import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  api, ApiError, type ImageParams, type Job, type Mode, type Model3DParams, type MusicParams, type Preset, type RuntimeState, type SpeechParams, type Thread,
  type ThreadDetail, type TranscribeParams, type VideoParams,
} from "./api";
import {
  DEFAULT_MODEL3D_PARAMS, DEFAULT_MUSIC_PARAMS, DEFAULT_SPEECH_PARAMS, DEFAULT_VIDEO_PARAMS, ImageChips, Model3DChips, MusicChips,
  SpeechChips, TranscribeChips, tooSlowReason, VideoChips,
} from "./Chips";
import { Composer } from "./Composer";
import { Feed } from "./Feed";
import { Viewer3D } from "./Model3D";
import { DEFAULT_PARAMS, Settings } from "./Settings";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { t, tServer } from "./i18n";

// Ayarlar sunucuda tutulur: her cihazda ve oturumda aynı kalır. Kayıt, yazarken her tuşta
// değil, kısa bir aradan sonra gider; sekme kapanırken bekleyen kayıt hemen gönderilir.
const pendingSaves: Record<string, { timer: ReturnType<typeof setTimeout>; value: unknown }> = {};

/** Bekleyen ayar kayıtlarını hemen gönderir: sekme kapanırken son ≤400 ms'lik ayar kaybolmaz.
 * Sayfa kapanırken yanıt beklenemez; hata burada yutulur, kalıcı hata normal kayıtta görünür. */
function flushSaves() {
  for (const key of Object.keys(pendingSaves)) {
    const { timer, value } = pendingSaves[key];
    delete pendingSaves[key];
    clearTimeout(timer);
    api.saveSetting(key, value).catch(() => {});
  }
}

const isPending = (j: Job) => j.status !== "done" && j.status !== "error";

/** Açılır katman (görsel büyütme, kayıtlar): Esc kapatır, açılırken odak katmana geçer, kapanınca
 * odağı açan öğeye geri verir. */
function Overlay({ className, label, onClose, onClick, children }: {
  className: string;
  label: string;
  onClose: () => void;
  onClick?: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, []);
  return (
    <div ref={ref} className={className} role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} onClick={onClick}>
      {children}
    </div>
  );
}

export default function App() {
  const [mode, setModeState] = useState<Mode>("image");
  const [rt, setRt] = useState<RuntimeState | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [params, setParamsState] = useState<ImageParams>(DEFAULT_PARAMS);
  const [musicParams, setMusicParamsState] = useState<MusicParams>(DEFAULT_MUSIC_PARAMS);
  const [videoParams, setVideoParamsState] = useState<VideoParams>(DEFAULT_VIDEO_PARAMS);
  const [speechParams, setSpeechParamsState] = useState<SpeechParams>(DEFAULT_SPEECH_PARAMS);
  const [transcribeParams, setTranscribeParamsState] = useState<TranscribeParams>({ language: "auto" });
  const [model3dParams, setModel3DParamsState] = useState<Model3DParams>(DEFAULT_MODEL3D_PARAMS);
  const [enhancing, setEnhancing] = useState(false);
  // Yükleniyor göstergesi sayaç: iki yükleme partisi paralel sürerken düğme erken açılmaz.
  const [uploads, setUploads] = useState(0);
  const [showSettings, setShowSettings] = useState(window.innerWidth > 1200);
  const [showMenu, setShowMenu] = useState(false);
  const [logLines, setLogLines] = useState<string[] | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  const [viewer3d, setViewer3d] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const feedEnd = useRef<HTMLDivElement>(null);
  // Yüklenmekte olan ekler de ek sınırında yer kaplar (art arda bırakılan dosyalar için).
  const pendingUploads = useRef(0);
  // Yükleme bitince dosyanın gerçekten hâlâ aynı sohbete/moda ait olup olmadığı buradan okunur.
  const activeIdRef = useRef(activeId);
  const modeRef = useRef(mode);
  // İstem geliştirme yoklaması buradan durdurulur (unmount ya da yeni istek).
  const enhanceRun = useRef<{ stopped: boolean } | null>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const save = (key: string, value: unknown) => {
    clearTimeout(pendingSaves[key]?.timer);
    pendingSaves[key] = {
      value,
      timer: setTimeout(() => {
        delete pendingSaves[key];
        api.saveSetting(key, value).catch(fail);
      }, 400),
    };
  };

  useEffect(() => {
    activeIdRef.current = activeId;
    modeRef.current = mode;
  }, [activeId, mode]);

  // Sekme kapanırken/gizlenirken bekleyen ayar kayıtları hemen gider; modül ömrü boyunca
  // zamanlayıcı sızmaz.
  useEffect(() => {
    const flush = () => flushSaves();
    const onHidden = () => {
      if (document.visibilityState === "hidden") flush();
    };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onHidden);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onHidden);
      flush();
    };
  }, []);

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        if (s["mode"]) setModeState(s["mode"] as Mode);
        if (s["image.params"]) setParamsState({ ...DEFAULT_PARAMS, ...(s["image.params"] as ImageParams) });
        if (s["video.params"]) setVideoParamsState({ ...DEFAULT_VIDEO_PARAMS, ...(s["video.params"] as VideoParams) });
        if (s["music.params"]) setMusicParamsState({ ...DEFAULT_MUSIC_PARAMS, ...(s["music.params"] as MusicParams) });
        if (s["speech.params"]) setSpeechParamsState({ ...DEFAULT_SPEECH_PARAMS, ...(s["speech.params"] as SpeechParams) });
        if (s["transcribe.params"]) setTranscribeParamsState(s["transcribe.params"] as TranscribeParams);
        if (s["model3d.params"]) setModel3DParamsState({ ...DEFAULT_MODEL3D_PARAMS, ...(s["model3d.params"] as Model3DParams) });
      })
      .catch(fail);
  }, []);

  const setParams = (p: ImageParams) => {
    setParamsState(p);
    save("image.params", p);
  };
  const setVideoParams = (p: VideoParams) => {
    setVideoParamsState(p);
    save("video.params", p);
  };
  const setMusicParams = (p: MusicParams) => {
    setMusicParamsState(p);
    save("music.params", p);
  };
  const setSpeechParams = (p: SpeechParams) => {
    setSpeechParamsState(p);
    save("speech.params", p);
  };
  const setTranscribeParams = (p: TranscribeParams) => {
    setTranscribeParamsState(p);
    save("transcribe.params", p);
  };
  const setModel3DParams = (p: Model3DParams) => {
    setModel3DParamsState(p);
    save("model3d.params", p);
  };
  // Her modun güncel ayarları ve bir sonuçtan ayarları geri yükleme
  const currentParams: Record<Mode, object> = {
    image: params, video: videoParams, model3d: model3dParams, music: musicParams, speech: speechParams, transcribe: transcribeParams,
  };
  const restoreParams: Record<Mode, (p: object) => void> = {
    // Turbo'dan önceki işlerde bu alan yok; onlar turbo'suz üretildi.
    image: (p) => setParams({ ...params, turbo: false, ...p }),
    video: (p) => setVideoParams({ ...videoParams, ...p }),
    model3d: (p) => setModel3DParams({ ...model3dParams, ...p }),
    music: (p) => setMusicParams({ ...musicParams, ...p }),
    speech: (p) => setSpeechParams({ ...speechParams, ...p }),
    transcribe: (p) => setTranscribeParams({ ...transcribeParams, ...p }),
  };
  // Sohbet değişince ekler temizlenir; başka moda taşınan dosya (görseli 3D'ye gönder) bunun yerine eklenir.
  const carry = useRef<string[]>([]);
  const setMode = (m: Mode, files: string[] = []) => {
    setModeState(m);
    save("mode", m);
    carry.current = files;
    setActiveId(null);
    setAttachments(files);
  };

  const refreshThreads = useCallback(() => api.threads(mode).then(setThreads).catch(fail), [mode]);
  const refreshPresets = useCallback(() => api.presets("image").then(setPresets).catch(fail), []);

  useEffect(() => {
    refreshThreads();
    refreshPresets();
  }, [refreshThreads, refreshPresets]);

  // GPU durumu. Tek bir başarısız yoklama son bilinen durumu silmez (üst şerit, kenar çubuğu ve
  // GPU hata bandı yerinde kalır); aynı anda tek istek gider ve eski yanıt yenisini ezmez.
  useEffect(() => {
    let stop = false;
    let busy = false;
    let generation = 0;
    const tick = async () => {
      if (busy) return;
      busy = true;
      const mine = ++generation;
      try {
        const s = await api.runtime();
        if (stop || mine !== generation) return;
        setRt(s);
      } catch {
        // Servis yeniden başlarken görülen geçici hata: son durum korunur, sonraki tick dener.
      } finally {
        busy = false;
      }
    };
    tick();
    const timer = setInterval(tick, 3000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, []);

  // Açık sohbet; bekleyen iş varsa sık yenilenir
  useEffect(() => {
    if (!activeId) {
      setThread(null);
      return;
    }
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const detail = await api.thread(activeId);
        if (stop) return;
        setThread(detail);
        timer = setTimeout(tick, detail.jobs.some(isPending) ? 1500 : 10000);
      } catch (e) {
        if (stop) return;
        // Silinmiş sohbet kapanır; geçici hatada (servis yeniden başlarken) yenileme sürer.
        if (e instanceof ApiError && e.status === 404) setActiveId(null);
        else timer = setTimeout(tick, 5000);
      }
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [activeId]);

  useEffect(() => {
    feedEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread?.jobs.length]);

  // Konuşma bağlamı: görsel sohbetinde son biten görsel, sonraki mesaja referans olarak eklenir
  // ("kaplanı kaldır" gibi bir devam mesajı onu düzenler). Kullanıcının kendi eklediği dosyalara
  // dokunulmaz; kaldırılırsa aynı sonuç için bir daha eklenmez.
  const context = useRef<{ job: string | null; name: string | null }>({ job: null, name: null });
  const lastImage = [...(thread?.jobs ?? [])].reverse().find((j) => j.kind === "image" && j.status === "done" && j.outputs.length > 0);
  useEffect(() => {
    context.current = { job: null, name: null };
    setAttachments(carry.current);
    carry.current = [];
  }, [activeId]);
  useEffect(() => {
    if (mode !== "image" || !lastImage || context.current.job === lastImage.id) return;
    const ownFiles = attachments.length > 0 && !(attachments.length === 1 && attachments[0] === context.current.name);
    context.current = { job: lastImage.id, name: lastImage.outputs[0] };
    if (!ownFiles) setAttachments([lastImage.outputs[0]]);
  }, [mode, lastImage?.id]);

  // Kayıt penceresi açıkken canlı kayıt
  useEffect(() => {
    if (logLines === null) return;
    const timer = setInterval(() => api.runtimeLog().then((r) => setLogLines(r.lines)).catch(() => {}), 3000);
    return () => clearInterval(timer);
  }, [logLines === null]);

  /** İşi gönderir; sunucu reddettiyse ya da ulaşılamadıysa false döner. */
  const submit = async (kind: Mode, text: string, p: object, inputs: string[]) => {
    let job;
    try {
      job = await api.createJob({ thread_id: activeId, kind, prompt: text, params: p as ImageParams, inputs });
    } catch (e) {
      fail(e);
      return false;
    }
    if (!activeId && job.thread_id) {
      setActiveId(job.thread_id);
      refreshThreads();
    } else if (activeId) {
      api.thread(activeId).then(setThread).catch(fail);
    }
    return true;
  };

  const onSubmit = async () => {
    const text = prompt.trim();
    const files = attachments;
    // Bekleyen istem geliştirme varsa durur: gönderilen istem sonradan değiştirilmez.
    if (enhanceRun.current) enhanceRun.current.stopped = true;
    setPrompt("");
    setAttachments([]);
    if (!(await submit(mode, text, currentParams[mode], mode === "music" ? [] : files))) {
      // Gönderilemeyen istem ve ekler kaybolmaz (bu arada yenisi yazılmadıysa geri gelir).
      setPrompt((cur) => cur || text);
      setAttachments((cur) => (cur.length ? cur : files));
    }
  };

  // İstem geliştirme sunucuda sürer: iş bitene (done/error) kadar yoklanır. Üst sınır 3 dk, iş
  // sunucuda yoksa (404) hemen bırakılır, sekme kapanınca yoklama durur ve kullanıcı bu arada
  // istemini değiştirdiyse sonuç onu ezmez.
  const onEnhance = async () => {
    if (enhanceRun.current) enhanceRun.current.stopped = true;
    const run = { stopped: false };
    enhanceRun.current = run;
    const typed = prompt.trim();
    const deadline = Date.now() + 180_000;
    setEnhancing(true);
    try {
      let job = await api.createJob({ thread_id: null, kind: "enhance", prompt: typed, inputs: attachments });
      // Tek bir yoklamanın düşmesi (servis yeniden başlarken) sonucu kaybettirmesin.
      while (isPending(job)) {
        if (run.stopped) return;
        if (Date.now() > deadline) throw new Error(t("İstem geliştirme 3 dk içinde bitmedi; tekrar dene."));
        await new Promise((r) => setTimeout(r, 1500));
        try {
          job = await api.job(job.id);
        } catch (e) {
          // 404 kalıcıdır: iş sunucuda yok, yoklamayı sürdürmenin anlamı yok.
          if (e instanceof ApiError && e.status === 404) throw new Error(t("İstem geliştirme işi sunucuda bulunamadı; tekrar dene."));
        }
      }
      if (job.status === "error") throw new Error(job.error ? tServer(job.error) : t("İstem geliştirilemedi"));
      const result = job.text;
      // Kullanıcı bu arada istemi değiştirdiyse sonucu yazıp onu ezme.
      if (result) setPrompt((cur) => (cur.trim() === typed || !cur.trim() ? result : cur));
    } catch (e) {
      if (!run.stopped) fail(e);
    } finally {
      if (enhanceRun.current === run) enhanceRun.current = null;
      setEnhancing(false);
    }
  };

  // Sekme kapanınca yoklama durur (boşuna istek gitmez).
  useEffect(
    () => () => {
      if (enhanceRun.current) enhanceRun.current.stopped = true;
    },
    [],
  );

  const onUpload = async (files: File[]) => {
    const limit = { image: 10, video: 2, model3d: 1, music: 0, speech: 1, transcribe: 1 }[mode];
    const room = Math.max(0, limit - attachments.length - pendingUploads.current);
    const batch = files.slice(0, room);
    if (batch.length < files.length) setError(t("En fazla {limit} ek eklenebilir; {n} dosya eklenmedi.", { limit, n: files.length - batch.length }));
    if (batch.length === 0) return;
    // Yükleme sürerken sohbet ya da mod değişebilir: dosyalar yalnızca başladıkları yere eklenir.
    const chat = activeId;
    const target = mode;
    pendingUploads.current += batch.length;
    setUploads((n) => n + 1);
    try {
      const names: string[] = [];
      for (const f of batch) names.push((await api.upload(f)).name);
      setAttachments((cur) => (activeIdRef.current === chat && modeRef.current === target ? [...cur, ...names] : cur));
    } catch (e) {
      fail(e);
    } finally {
      pendingUploads.current -= batch.length;
      setUploads((n) => n - 1);
    }
  };

  const runtimeAction = (a: "start" | "stop" | "toggle-auto") =>
    api.runtimeAction(a).then(setRt).catch(fail);

  return (
    <div className={`app ${showSettings ? "with-settings" : ""}`}>
      <Sidebar
        open={showMenu}
        rt={rt}
        mode={mode}
        onMode={(m) => {
          setMode(m);
          setShowMenu(false);
        }}
        threads={threads}
        activeId={activeId}
        onNew={() => {
          setActiveId(null);
          setShowMenu(false);
        }}
        onSelect={(id) => {
          setActiveId(id);
          setShowMenu(false);
        }}
        onDelete={async (id) => {
          await api.deleteThread(id).catch(fail);
          if (id === activeId) setActiveId(null);
          refreshThreads();
        }}
      />
      <main className="main">
        <TopBar
          rt={rt}
          onStart={() => runtimeAction("start")}
          onStop={() => confirm(t("GPU kapatılsın mı? Süren işler yarıda kalır.")) && runtimeAction("stop")}
          onToggleAuto={() => runtimeAction("toggle-auto")}
          onToggleLog={() => (logLines === null ? api.runtimeLog().then((r) => setLogLines(r.lines)).catch(fail) : setLogLines(null))}
          onToggleSettings={() => setShowSettings(!showSettings)}
          onToggleMenu={() => setShowMenu(!showMenu)}
        />
        {error && (
          <div className="banner err" role="alert">
            <p className="error-text">{error}</p>
            <button className="icon-btn" onClick={() => setError(null)} aria-label={t("Kapat")}><X size={16} /></button>
          </div>
        )}
        {rt?.phase === "error" && (
          <div className="banner err" role="alert">
            <p className="error-text">{t("GPU hatası: {detail}", { detail: tServer(rt.detail) })}</p>
          </div>
        )}
        <div className="feed">
          <div className="feed-inner">
            <Feed
              mode={mode}
              jobs={thread?.jobs ?? []}
              rt={rt}
              onSuggestion={setPrompt}
              onRetry={(job) => submit(job.kind as Mode, job.prompt, { ...job.params, seed: null }, job.inputs)}
              onEdit={(name) => {
                setAttachments([name]);
                setPrompt("");
              }}
              onReusePrompt={(job) => {
                setPrompt(job.prompt);
                if (job.kind === "music") setMusicParams({ ...musicParams, lyrics: job.params.lyrics ?? "" });
                setAttachments(job.inputs);
              }}
              onUseSettings={(job) => {
                const { width: _w, height: _h, ...rest } = job.params;
                restoreParams[job.kind as Mode](rest);
              }}
              onCancel={async (job) => {
                if (!confirm(t("Bu iş iptal edilsin mi?"))) return;
                let cancelled: Job;
                try {
                  cancelled = await api.cancelJob(job.id);
                } catch (e) {
                  fail(e);
                  return;
                }
                // Uç iptal edilen işin son hâlini döndürür: satır hemen tutarlı kalır.
                setThread((cur) => (cur ? { ...cur, jobs: cur.jobs.map((j) => (j.id === cancelled.id ? cancelled : j)) } : cur));
                if (!activeId) return;
                try {
                  setThread(await api.thread(activeId));
                } catch (e) {
                  // Tam liste alınamadı: yerelde işaretlenen satır duruyor, sonraki yoklama düzeltir.
                  fail(e);
                }
              }}
              onZoom={setZoom}
              onOpen3D={setViewer3d}
              onMake3D={(name) => setMode("model3d", [name])}
            />
            <div ref={feedEnd} />
          </div>
        </div>
        <Composer
          prompt={prompt}
          setPrompt={setPrompt}
          attachments={attachments}
          setAttachments={setAttachments}
          chips={
            mode === "video" ? (
              <VideoChips params={videoParams} setParams={setVideoParams} />
            ) : mode === "model3d" ? (
              <Model3DChips params={model3dParams} setParams={setModel3DParams} />
            ) : mode === "music" ? (
              <MusicChips params={musicParams} setParams={setMusicParams} />
            ) : mode === "speech" ? (
              <SpeechChips params={speechParams} setParams={setSpeechParams} cloning={attachments.length > 0} />
            ) : mode === "transcribe" ? (
              <TranscribeChips params={transcribeParams} setParams={setTranscribeParams} />
            ) : (
              <ImageChips params={params} setParams={setParams} presets={presets} editing={attachments.length > 0} />
            )
          }
          extra={
            mode === "music" && (
              <textarea
                className="lyrics"
                value={musicParams.lyrics}
                placeholder={t("Şarkı sözleri (boş bırakılırsa enstrümantal)") + "\n[Verse]\n...\n[Chorus]\n..."}
                rows={5}
                onChange={(e) => setMusicParams({ ...musicParams, lyrics: e.target.value })}
              />
            )
          }
          canAttach={mode !== "music"}
          accept={mode === "speech" || mode === "transcribe" ? "audio/*" : "image/png,image/jpeg,image/webp"}
          blocked={tooSlowReason(mode, videoParams, params, attachments.length > 0)}
          canSubmit={mode === "transcribe" || mode === "model3d" ? attachments.length === 1 : prompt.trim().length > 0}
          placeholder={
            mode === "model3d"
              ? t("3D'ye çevrilecek görseli ekle (ataç, sürükle-bırak ya da Ctrl+V). İstersen buraya bir ad yaz.")
              : mode === "transcribe"
              ? t("Ses dosyasını ekle (ataç düğmesi ya da sürükle-bırak). İstersen buraya bir başlık yaz.")
              : mode === "speech"
                ? t("Seslendirilecek metni yaz. Bir ses kaydı eklersen o ses klonlanır.")
                : mode === "music"
              ? t("Tarz: tür, vokal, dil, tempo, enstrümanlar (ör. Türkçe, sıcak kadın vokal, akustik pop, 96 BPM, gitar ve piyano)")
              : mode === "video"
              ? t("Sahneyi, kamera hareketini ve sesi anlat (diyalog, efekt, müzik)")
              : attachments.length > 0
                ? t("Görselde neyi değiştirmek istiyorsun? (ör. arka planı gün batımı yap)")
                : t("Ne üretmek istiyorsun?")
          }
          submitLabel={
            mode === "image" && attachments.length > 0 ? t("Düzenle")
              : mode === "transcribe" ? t("Yazıya dök")
                : mode === "speech" ? t("Seslendir")
                  : mode === "model3d" ? t("3D'ye çevir")
                    : t("Üret")
          }
          attachTitle={
            mode === "video" ? t("Görsel ekle: 1. ilk kare, 2. son kare")
              : mode === "model3d" ? t("3D'ye çevrilecek görseli ekle")
              : mode === "speech" ? t("Klonlanacak ses kaydını ekle (5-15 sn)")
                : mode === "transcribe" ? t("Yazıya dökülecek ses dosyasını ekle")
                  : t("Görsel ekle (düzenleme)")
          }
          enhancing={enhancing}
          uploading={uploads > 0}
          onUpload={onUpload}
          onReject={setError}
          onEnhance={mode === "image" ? onEnhance : null}
          onSubmit={onSubmit}
        />
      </main>
      {showSettings && (
        <Settings
          mode={mode}
          params={params}
          setParams={setParams}
          videoParams={videoParams}
          setVideoParams={setVideoParams}
          musicParams={musicParams}
          setMusicParams={setMusicParams}
          speechParams={speechParams}
          setSpeechParams={setSpeechParams}
          model3dParams={model3dParams}
          setModel3DParams={setModel3DParams}
          presets={presets}
          editing={mode === "image" && attachments.length > 0}
          onSavePreset={(name) => api.savePreset("image", name, params).then(refreshPresets).catch(fail)}
          onDeletePreset={(id) => api.deletePreset(id).then(refreshPresets).catch(fail)}
          onClose={() => setShowSettings(false)}
        />
      )}
      {zoom && (
        <Overlay className="lightbox" label={t("Görsel önizleme")} onClose={() => setZoom(null)} onClick={() => setZoom(null)}>
          <img src={zoom} alt="" />
        </Overlay>
      )}
      {viewer3d && <Viewer3D src={viewer3d} onClose={() => setViewer3d(null)} />}
      {logLines !== null && (
        <Overlay className="log" label={t("Kayıtlar")} onClose={() => setLogLines(null)}>
          <header>
            <strong>{t("Kayıtlar")}</strong>
            <button className="icon-btn" onClick={() => setLogLines(null)} aria-label={t("Kapat")}><X size={16} /></button>
          </header>
          <pre>{logLines.join("\n") || t("Kayıt yok.")}</pre>
        </Overlay>
      )}
    </div>
  );
}
