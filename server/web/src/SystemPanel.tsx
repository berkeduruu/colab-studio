import { X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, type Credits, type Metric, type PackStatus, type RuntimeState } from "./api";
import { downloadText, formatDuration, pct } from "./format";
import { t, tServer } from "./i18n";

const GB = 1024 ** 3;

/** Model paketleri: inenler çubukla, hazır olanlar tik ile. */
function Packs({ packs }: { packs: Record<string, PackStatus> }) {
  const list = Object.entries(packs);
  if (list.length === 0) return null;
  return (
    <div className="packs">
      {list.map(([name, p]) => (
        <div key={name} className="pack">
          <div className="gauge-head">
            <span>{tServer(p.label ?? name)}</span>
            <span>{p.fraction >= 1 ? t("hazır ✓") : pct(p.fraction * 100, Math.floor)}</span>
          </div>
          {p.fraction < 1 && (
            <>
              <div className="progress"><div style={{ width: `${Math.floor(p.fraction * 100)}%` }} /></div>
              <div className="muted small">{downloadText(p)}</div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

function Spark({ values }: { values: number[] }) {
  const w = 100;
  const h = 24;
  const pts = values.map((v, i) => `${values.length > 1 ? (i / (values.length - 1)) * w : w},${h - Math.min(Math.max(v, 0), 1) * h}`);
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      {pts.length > 0 && <polyline points={[`0,${h}`, ...pts, `${w},${h}`].join(" ")} className="area" />}
      {pts.length > 0 && <polyline points={pts.join(" ")} className="line" />}
    </svg>
  );
}

function Gauge({ label, value, history }: { label: string; value: string; history: number[] }) {
  return (
    <div className="gauge">
      <div className="gauge-head">
        <span>{label}</span>
        <span>{value}</span>
      </div>
      <Spark values={history} />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="kv">
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}

/** Kenar çubuğundaki tıklanabilir özet kutusu; ayrıntı için pencere açar. */
function Tile({ label, value, sub, bar, wide, className = "", title, onClick }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  bar?: number;
  wide?: boolean;
  className?: string;
  title: string;
  onClick: (anchor: DOMRect) => void;
}) {
  return (
    <button className={`tile-btn ${wide ? "wide" : ""} ${className}`} onClick={(e) => onClick(e.currentTarget.getBoundingClientRect())} aria-label={title} title={title}>
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {bar !== undefined && <span className="progress"><div style={{ width: `${Math.round(Math.min(Math.max(bar, 0), 1) * 100)}%` }} /></span>}
      {sub && <span className="tile-sub">{sub}</span>}
    </button>
  );
}

/** Tıklanan kutucuğun yanında açılan yuvarlak balon (geniş ekranda kenar çubuğunun sağında, telefonda
 * kutucuğun üstünde). Esc, dışarı tıklama, kapat düğmesi, aynı kutucuğa yeniden tıklama ya da pencere
 * boyutunu değiştirme kapatır (konum açılışta hesaplanır; boyut değişince geçersiz olur). */
function Detail({ title, anchor, onClose, children }: { title: string; anchor: DOMRect; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const onPointer = (e: PointerEvent) => {
      if (!(e.target as Element).closest(".detail, .tile-btn")) onClose();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("resize", onClose);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);
  const side = window.innerWidth > 900;
  const bottom = side ? Math.max(8, window.innerHeight - anchor.bottom) : window.innerHeight - anchor.top + 8;
  const style = side ? { left: anchor.right + 10, bottom, maxHeight: window.innerHeight - bottom - 8 } : { left: 8, right: 8, bottom, maxHeight: window.innerHeight - bottom - 8 };
  return (
    <div className={`detail ${side ? "side" : ""}`} role="dialog" aria-label={title} style={style}>
      <header>
        <strong>{title}</strong>
        <button className="icon-btn" onClick={onClose} aria-label={t("Ayrıntıyı kapat")}><X size={16} /></button>
      </header>
      <div className="detail-body">{children}</div>
    </div>
  );
}

function CreditRows({ rt, c, balance }: { rt: RuntimeState; c: Credits; balance: number }) {
  const cs = rt.credit_stats;
  return (
    <div className="credits">
      <div className="credits-balance">
        <strong>{balance.toFixed(1)}</strong> CU
      </div>
      {c.rate !== undefined && <Row label={t("Saatlik harcama")} value={t("{n} CU/sa", { n: c.rate.toFixed(2) })} />}
      {c.rate ? <Row label={t("Bu hızla yeter")} value={formatDuration(Math.round((balance / c.rate) * 3600))} /> : null}
      {rt.session_spent !== null && (
        <Row
          label={t("Bu oturum")}
          value={`${rt.session_spent.toFixed(2)} CU${rt.session_seconds !== null ? ` · ${formatDuration(rt.session_seconds)}` : ""}`}
        />
      )}
      {cs && cs.week > 0 && <Row label={t("Bugün")} value={`${cs.today.toFixed(2)} CU`} />}
      {cs && cs.week > 0 && <Row label={t("Günlük ort. ({d})", { d: t("{d} gün", { d: Math.min(cs.tracked_days, 7) }) })} value={`${cs.per_day.toFixed(2)} CU`} />}
      {cs && cs.per_day > 0 && <Row label={t("Ortalamayla yeter")} value={`~${t("{d} gün", { d: Math.floor(balance / cs.per_day) })}`} />}
    </div>
  );
}

type Section = "gpu" | "models" | "credits";

export function SystemPanel({ rt }: { rt: RuntimeState | null }) {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [open, setOpen] = useState<{ section: Section; rect: DOMRect } | null>(null);
  const close = () => setOpen(null);
  const toggle = (section: Section) => (rect: DOMRect) => setOpen((o) => (o?.section === section ? null : { section, rect }));
  const ready = rt?.phase === "ready";

  useEffect(() => {
    if (!ready) {
      setMetrics([]);
      return;
    }
    const tick = () => api.metrics().then(setMetrics).catch(() => {});
    tick();
    const timer = setInterval(tick, 5000);
    return () => clearInterval(timer);
  }, [ready]);

  if (!rt) return null;
  const s = rt.startup;
  const last = metrics[metrics.length - 1];
  const c = rt.credits;
  const balance = c?.balance;
  const packs = Object.values(rt.packs ?? {});
  const showPacks = (rt.phase === "starting" || ready) && packs.length > 0;
  const readyPacks = packs.filter((p) => p.fraction >= 1).length;
  const loading = packs.find((p) => p.fraction < 1);
  const totalBytes = packs.reduce((a, p) => a + p.total, 0);
  const doneBytes = packs.reduce((a, p) => a + p.done, 0);

  const gpuTile = ready && last && (
    <Tile
      label="GPU"
      value={pct(last.gpu)}
      bar={last.gpu / 100}
      sub={`VRAM ${(last.vram_used / GB).toFixed(0)}/${(last.vram_total / GB).toFixed(0)} GB`}
      title={t("GPU ve sistem ayrıntıları")}
      onClick={toggle("gpu")}
    />
  );
  const modelsTile = showPacks && (
    <Tile
      label={t("Modeller")}
      value={loading ? t("{n}/{total} hazır", { n: readyPacks, total: packs.length }) : t("Hepsi hazır ✓")}
      bar={totalBytes ? doneBytes / totalBytes : 1}
      sub={loading ? `${tServer(loading.label) || t("Paket")} ${pct(loading.fraction * 100, Math.floor)}${loading.rate ? ` · ${Math.round(loading.rate / 2 ** 20)} MB/s` : ""}` : undefined}
      title={t("Model ayrıntıları")}
      onClick={toggle("models")}
    />
  );
  const top = [gpuTile, modelsTile].filter(Boolean);
  const pack = s ? rt.packs?.[s.pack ?? "image"] : undefined;

  return (
    <div className="system">
      {rt.phase === "starting" && s && (
        <Tile
          className="startup"
          wide
          label={tServer(s.steps[s.step])}
          value={pct(s.progress * 100)}
          bar={s.progress}
          sub={t("~{left} kaldı · {elapsed} geçti", { left: formatDuration(s.remaining_seconds), elapsed: formatDuration(s.elapsed_seconds) })}
          title={t("Açılış ayrıntıları")}
          onClick={toggle("models")}
        />
      )}
      {top.length > 0 && (
        <div className="tiles">
          {gpuTile && <div className={top.length === 1 ? "wide" : ""}>{gpuTile}</div>}
          {modelsTile && <div className={top.length === 1 ? "wide" : ""}>{modelsTile}</div>}
        </div>
      )}
      {c?.error && <div className="muted small" title={tServer(c.error)}>{t("Kredi okunamadı")}</div>}
      {balance !== undefined && (
        <Tile
          wide
          label={t("Kredi")}
          value={`${balance.toFixed(1)} CU`}
          sub={[c?.rate !== undefined ? t("{n} CU/sa", { n: c.rate.toFixed(2) }) : null, c?.rate ? t("{d} yeter", { d: formatDuration(Math.round((balance / c.rate) * 3600)) }) : null].filter(Boolean).join(" · ")}
          title={t("Kredi ayrıntıları")}
          onClick={toggle("credits")}
        />
      )}

      {open?.section === "gpu" && last && (
        <Detail title={t("GPU ve sistem")} anchor={open.rect} onClose={close}>
            <div className="gauges">
              <Gauge label={`GPU · ${last.temp}°C`} value={pct(last.gpu)} history={metrics.map((m) => m.gpu / 100)} />
              <Gauge
                label="VRAM"
                value={`${(last.vram_used / GB).toFixed(1)}/${(last.vram_total / GB).toFixed(0)} GB`}
                history={metrics.map((m) => m.vram_used / m.vram_total)}
              />
              <Gauge
                label="RAM"
                value={`${(last.ram_used / GB).toFixed(1)}/${(last.ram_total / GB).toFixed(0)} GB`}
                history={metrics.map((m) => m.ram_used / m.ram_total)}
              />
              <Gauge
                label={t("Disk")}
                value={`${(last.disk_used / GB).toFixed(0)}/${(last.disk_total / GB).toFixed(0)} GB`}
                history={metrics.map((m) => m.disk_used / m.disk_total)}
              />
            </div>
        </Detail>
      )}
      {open?.section === "models" && (
        <Detail title={rt.phase === "starting" ? t("Açılış ve modeller") : t("Modeller")} anchor={open.rect} onClose={close}>
          {rt.phase === "starting" && s && (
            <div className="startup-detail">
              <div className="gauge-head">
                <span>{tServer(s.steps[s.step])}</span>
                <span>{pct(s.progress * 100)}</span>
              </div>
              <div className="progress"><div style={{ width: `${Math.round(s.progress * 100)}%` }} /></div>
              <div className="muted small">{t("~{left} kaldı · {elapsed} geçti", { left: formatDuration(s.remaining_seconds), elapsed: formatDuration(s.elapsed_seconds) })}</div>
              {s.setup && <div className="muted small">{t("Kurulum {step}", { step: tServer(s.setup) })}</div>}
              {s.download && <div className="muted small">{t("{label} modelleri: {progress}", { label: tServer(pack?.label ?? "Görsel"), progress: downloadText(s.download) })}</div>}
            </div>
          )}
          {showPacks ? <Packs packs={rt.packs!} /> : <div className="muted">{t("Henüz model paketi yok.")}</div>}
        </Detail>
      )}
      {open?.section === "credits" && balance !== undefined && c && (
        <Detail title={t("Kredi")} anchor={open.rect} onClose={close}>
          <CreditRows rt={rt} c={c} balance={balance} />
        </Detail>
      )}
    </div>
  );
}
