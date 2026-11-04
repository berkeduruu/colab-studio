import { ArrowUp, Loader2, Paperclip, Sparkles, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { mediaUrl } from "./api";
import { t } from "./i18n";

interface Props {
  prompt: string;
  setPrompt: (v: string) => void;
  attachments: string[];
  setAttachments: (v: string[]) => void;
  chips: ReactNode;
  extra?: ReactNode;
  canAttach: boolean;
  accept: string;
  canSubmit: boolean;
  /** Seçili ayarlarla iş gönderilemiyorsa nedeni (gönder düğmesi kapanır, ipucu yerine görünür). */
  blocked?: string | null;
  placeholder: string;
  submitLabel: string;
  attachTitle: string;
  enhancing: boolean;
  uploading: boolean;
  onUpload: (files: File[]) => void;
  onReject: (message: string) => void;
  onEnhance: (() => void) | null;
  onSubmit: () => void;
}

export const isAudio = (name: string) => /\.(mp3|wav|flac|ogg|webm|m4a)$/i.test(name);

// <input accept> kuralıyla aynı eşleşme: "image/png,image/jpeg" ya da "audio/*"
const accepts = (accept: string, file: File) =>
  accept.split(",").some((a) => (a.endsWith("/*") ? file.type.startsWith(a.slice(0, -1)) : file.type === a));

// Bazı tarayıcılar (Linux'ta Firefox) panodaki görseli files'ta değil items'ta verir.
const clipboardFiles = (dt: DataTransfer) => {
  const files = Array.from(dt.files);
  if (files.length) return files;
  return Array.from(dt.items)
    .filter((i) => i.kind === "file")
    .map((i) => i.getAsFile())
    .filter((f): f is File => f !== null);
};

export function Composer(props: Props) {
  const { prompt, setPrompt, attachments, setAttachments, enhancing, uploading } = props;
  const fileRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  // Sürüklenen ya da yapıştırılan dosyalar: uygun olanlar yüklenir, olmayanlar için uyarı çıkar.
  const take = (files: File[]) => {
    if (!props.canAttach) return props.onReject(t("Bu modda dosya eklenemez."));
    const ok = files.filter((f) => accepts(props.accept, f));
    const bad = files.filter((f) => !accepts(props.accept, f));
    if (bad.length)
      props.onReject(t("{files} eklenemedi: bu modda yalnızca {kind} eklenebilir.", {
        files: bad.map((f) => f.name || f.type || t("dosya")).join(", "),
        kind: props.accept.startsWith("audio") ? t("ses dosyası") : t("PNG, JPEG ya da WebP görsel"),
      }));
    if (ok.length) props.onUpload(ok);
  };
  const takeRef = useRef(take);
  takeRef.current = take;
  // Pano işleyicisi pencereye bir kez bağlanır; mod ve kabul kuralı en güncel hâliyle buradan okunur.
  const rulesRef = useRef({ canAttach: props.canAttach, accept: props.accept });
  rulesRef.current = { canAttach: props.canAttach, accept: props.accept };

  // Ctrl+V sayfanın neresinde olursa olsun çalışır. Tarayıcının kendi yapıştırması yalnızca
  // gerçekten dosya eklenecekse ve panoda düz metin yoksa engellenir: metin yapıştırma bozulmaz,
  // dosya kabul etmeyen modda da gereksiz müdahale olmaz (yalnızca uyarı çıkar).
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const dt = e.clipboardData;
      if (!dt) return;
      const files = clipboardFiles(dt);
      if (!files.length) return;
      const { canAttach, accept } = rulesRef.current;
      const text = typeof dt.getData === "function" ? dt.getData("text/plain") : "";
      if (canAttach && !text.trim() && files.some((f) => accepts(accept, f))) e.preventDefault();
      takeRef.current(files);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, []);
  // Kutu yazı kadar büyür (en çok ekranın yarısı, CSS max-height); uzun istemin tamamı görünür.
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [prompt]);
  const canSend = props.canSubmit && !props.blocked && !enhancing && !uploading;

  return (
    <div className="composer-wrap">
      <div
        className="composer"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files.length) take(Array.from(e.dataTransfer.files));
        }}
      >
        {attachments.length > 0 && (
          <div className="attachments">
            {attachments.map((name) => (
              <div key={name} className={`attachment ${isAudio(name) ? "audio" : ""}`}>
                {isAudio(name) ? <audio src={mediaUrl(name)} controls preload="metadata" /> : <img src={mediaUrl(name)} alt="" />}
                <button aria-label={t("Kaldır")} onClick={() => setAttachments(attachments.filter((a) => a !== name))}><X size={12} /></button>
              </div>
            ))}
          </div>
        )}
        {props.extra}
        <textarea
          ref={textRef}
          className="prompt"
          aria-label={t("İstem")}
          value={prompt}
          placeholder={props.placeholder}
          rows={2}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canSend) props.onSubmit();
            }
          }}
        />
        <div className="composer-bar">
          <input
            ref={fileRef}
            type="file"
            accept={props.accept}
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length) props.onUpload(Array.from(e.target.files));
              e.target.value = "";
            }}
          />
          {props.canAttach && <button className="icon-btn" title={props.attachTitle} aria-label={props.attachTitle} onClick={() => fileRef.current?.click()}>
            {uploading ? <Loader2 size={18} className="spin" /> : <Paperclip size={18} />}
          </button>}
          {props.chips}
          <span className="spacer" />
          {props.onEnhance && (
            <button className="btn ghost" disabled={!prompt.trim() || enhancing} onClick={props.onEnhance} title={t("Kısa isteği ayrıntılı bir isteme çevirir")}>
              {enhancing ? <Loader2 size={15} className="spin" /> : <Sparkles size={15} />} {t("İstemi geliştir")}
            </button>
          )}
          <button className="btn primary" disabled={!canSend} onClick={props.onSubmit}>
            <ArrowUp size={15} /> {props.submitLabel}
          </button>
        </div>
      </div>
      {props.blocked && <div className="hint warn" role="alert">{props.blocked}</div>}
      <div className="hint">{t("Enter ile gönder, Shift+Enter yeni satır. GPU kapalıysa ilk istek GPU'yu açar.")}</div>
    </div>
  );
}
