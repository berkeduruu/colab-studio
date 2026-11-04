import { exec } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";

const execAsync = promisify(exec);

/**
 * Stüdyonun .env dosyası (repo kökü, bu dosyanın iki üst dizini): port ve veri dizini oradan okunur,
 * böylece MCP ek ayar istemez. Ortam değişkeni verilmişse o geçerlidir.
 */
function studioEnv(): Record<string, string> {
  const file = process.env.STUDIO_ENV_FILE || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../.env");
  try {
    return Object.fromEntries(
      readFileSync(file, "utf8")
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith("#") && l.includes("="))
        .map((l) => {
          const i = l.indexOf("=");
          let v = l.slice(i + 1).trim();
          if (v.length >= 2 && (v[0] === '"' || v[0] === "'") && v.endsWith(v[0])) v = v.slice(1, -1);
          return [l.slice(0, i).trim().replace(/^export\s+/, ""), v];
        }),
    );
  } catch {
    return {};
  }
}
const ENV: Record<string, string | undefined> = { ...studioEnv(), ...process.env };

/** Stüdyo API'si (server/app.py). */
export const API_BASE = (ENV.STUDIO_URL || `http://127.0.0.1:${ENV.STUDIO_PORT || "8190"}`).replace(/\/+$/, "");
/** Stüdyonun veri dizini (.env'deki STUDIO_DATA); medya `media/` altında. */
export const DATA_DIR = (ENV.STUDIO_DATA || "~/.local/share/colab-studio").replace(/^~(?=\/|$)/, os.homedir());
export const MEDIA_DIR = path.join(DATA_DIR, "media");
/** Medya dosyalarının istemciye verilen taban adresi (stüdyo `/media` yolundan sunar). */
export const PUBLIC_MEDIA_URL_BASE = (ENV.STUDIO_PUBLIC_MEDIA_URL || `${API_BASE}/media`).replace(/\/+$/, "");
/** colab CLI (PATH'te değilse COLAB_BIN ile verilir). */
const COLAB_BIN = ENV.COLAB_BIN || "colab";

/** `server/studio.py:JOB_TIMEOUT` — bir işin 30 dakikada bitmesi beklenir. */
export const JOB_TIMEOUT_SECONDS = 1800;
/** `server/studio.py:CANCELLED` — iptal edilen iş `status=error, error="İptal edildi"` olarak kapanır. */
export const CANCELLED_JOB_ERROR = "İptal edildi";
/** Terminal durumlar: backend bunların dışına asla yazmaz (`queued|waiting_gpu|running` sürer). */
export const TERMINAL_JOB_STATUSES = ["done", "error"] as const;

/** `/api/uploads` sözleşmesi: multipart alanı `file`, en fazla 50 MB (aiohttp client_max_size). */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
/** API'nin kabul ettiği içerik tipleri (`server/app.py:UPLOAD_TYPES`) ve uzantı karşılıkları. */
export const UPLOAD_MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".flac": "audio/flac",
  ".ogg": "audio/ogg",
  ".webm": "audio/webm",
  ".m4a": "audio/mp4",
};

const MEDIA_MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".flac": "audio/flac",
  ".ogg": "audio/ogg",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".m4a": "audio/mp4",
  ".glb": "model/gltf-binary",
  ".ply": "application/octet-stream",
  ".txt": "text/plain",
  ".json": "application/json",
};

/** `/api/runtime/metrics` kayıtları (bayt cinsinden VRAM/RAM/disk). */
export interface GpuMetricsSnapshot {
  t: number;
  gpu: number;
  temp: number;
  vram_used: number;
  vram_total: number;
  ram_used: number;
  ram_total: number;
  disk_used: number;
  disk_total: number;
}

/** `runtime.snapshot()["gpu_now"]` — metrics'in son kaydının kısa hâli. */
export type GpuNow = Pick<GpuMetricsSnapshot, "t" | "gpu" | "vram_used" | "vram_total">;

export interface CreditsInfo {
  balance: number;
  rate: number;
}

export interface CreditsError {
  error: string;
}

/** Başlangıçta `null`, okunamazsa `{error}` döner: her iki durum da normaldir. */
export type RuntimeCredits = CreditsInfo | CreditsError | null;

export interface StartupProgress {
  progress: number;
  remaining_seconds: number;
  elapsed_seconds: number;
  step: number;
  steps: string[];
  step_elapsed_seconds: number;
  download?: unknown;
  pack?: string;
  setup?: string | null;
}

export interface QueueCurrent {
  id: string;
  kind: string;
  status: string;
  stage?: string | null;
  progress: number;
  prompt: string;
}

export interface CreditStats {
  today: number;
  week: number;
  per_day: number;
  tracked_days: number;
}

export interface RuntimeStatus {
  phase: "off" | "starting" | "setup" | "ready" | "error" | string;
  detail: string | null;
  auto_shutdown: boolean;
  idle_seconds: number | null;
  idle_minutes: number;
  credits: RuntimeCredits;
  session_spent: number | null;
  session_seconds: number | null;
  startup: StartupProgress | null;
  gpu: string;
  packs: Record<string, unknown>;
  gpu_now: GpuNow | null;
  credit_stats: CreditStats | null;
  queue: {
    order: string[];
    current: QueueCurrent | null;
    starts_in: Record<string, number | null>;
  } | null;
}

export type JobStatus = "queued" | "waiting_gpu" | "running" | "done" | "error";

export interface JobLiveState {
  progress: number;
  eta: number | null;
  stage_seconds: number | null;
  stage_expected: number | null;
  loading: boolean;
}

export interface JobRecord {
  id: string;
  thread_id: string | null;
  mode: string;
  kind: string;
  prompt: string;
  params: Record<string, unknown>;
  inputs: string[];
  status: JobStatus;
  stage?: string | null;
  live?: JobLiveState | null;
  progress: number;
  error: string | null;
  outputs: string[];
  text: string | null;
  created_at: number;
  finished_at: number | null;
}

export interface JobCreatePayload {
  kind: "image" | "video" | "music" | "speech" | "transcribe" | "model3d" | "enhance";
  prompt: string;
  inputs?: string[];
  params?: Record<string, unknown>;
  thread_id?: string;
}

export interface ThreadItem {
  id: string;
  mode: string;
  title: string;
  created_at: number;
  jobs?: JobRecord[];
}

export interface PresetItem {
  id: string;
  mode: string;
  name: string;
  params: Record<string, unknown>;
}

export interface MediaFileInfo {
  filename: string;
  sizeBytes: number;
  modifiedAt: string;
  publicUrl: string;
  isOutput: boolean;
}

export interface SystemHealthSummary {
  service: string;
  uptime: string;
  diskUsage: string;
  memoryUsage: string;
  colabSessions: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** İş zamanında bitmediğinde fırlatılır: çağıran taraf işi iptal etmekle yükümlüdür. */
export class JobTimeoutError extends Error {
  readonly jobId: string;
  readonly timeoutSeconds: number;
  readonly lastJob: JobRecord | null;

  constructor(jobId: string, timeoutSeconds: number, lastJob: JobRecord | null) {
    super(`Timeout waiting for job ${jobId} after ${timeoutSeconds} seconds`);
    this.name = "JobTimeoutError";
    this.jobId = jobId;
    this.timeoutSeconds = timeoutSeconds;
    this.lastJob = lastJob;
  }
}

export function isTerminalStatus(status: string): boolean {
  return (TERMINAL_JOB_STATUSES as readonly string[]).includes(status);
}

/** İptal, terminal `error` durumunun içinden yalnızca bu metinle ayrılır. */
export function isCancelledJob(job: Pick<JobRecord, "error"> | null | undefined): boolean {
  return typeof job?.error === "string" && job.error.trim() === CANCELLED_JOB_ERROR;
}

/** Göreli medya adını (`uploads/x.png`) genel URL'ye çevirir. */
export function mediaPublicUrl(relativeName: string): string {
  const clean = relativeName.replace(/^\/+/, "");
  return `${PUBLIC_MEDIA_URL_BASE}/${clean}`;
}

function abortError(signal: AbortSignal): Error {
  const reason: unknown = signal.reason;
  if (reason instanceof Error) {
    return reason;
  }
  const error = new Error("Aborted");
  error.name = "AbortError";
  return error;
}

export function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError(signal));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, Math.max(0, ms));
    function onAbort(): void {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(abortError(signal as AbortSignal));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function fetchJson<T>(apiPath: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${apiPath.startsWith("/") ? "" : "/"}${apiPath}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init?.body !== undefined && init.body !== null) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, {
    ...init,
    headers: {
      ...headers,
      ...((init?.headers as Record<string, string> | undefined) || {}),
    },
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new ApiError(
      `Colab Studio API Error (${response.status} ${response.statusText}): ${errorText}`,
      response.status
    );
  }

  return response.json() as Promise<T>;
}

/** Geçici hata mı: 5xx/429 ya da ağ katmanı hatası (DNS, bağlantı reddi, reset). */
function isTransientError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.status >= 500 || error.status === 429;
  }
  if (error && typeof error === "object" && (error as { name?: string }).name === "AbortError") {
    return false;
  }
  return true;
}

const JOB_POLL_RETRIES = 3;

export async function getRuntimeStatus(): Promise<RuntimeStatus> {
  return fetchJson<RuntimeStatus>("/api/runtime");
}

export async function startRuntime(): Promise<RuntimeStatus> {
  return fetchJson<RuntimeStatus>("/api/runtime/start", { method: "POST" });
}

export async function stopRuntime(): Promise<RuntimeStatus> {
  return fetchJson<RuntimeStatus>("/api/runtime/stop", { method: "POST" });
}

export async function toggleAutoShutdown(): Promise<RuntimeStatus> {
  return fetchJson<RuntimeStatus>("/api/runtime/toggle-auto", { method: "POST" });
}

export async function getRuntimeMetrics(): Promise<GpuMetricsSnapshot[]> {
  return fetchJson<GpuMetricsSnapshot[]>("/api/runtime/metrics");
}

export async function getRuntimeLogs(): Promise<{ lines: string[] }> {
  return fetchJson<{ lines: string[] }>("/api/runtime/log");
}

export async function createJob(payload: JobCreatePayload): Promise<JobRecord> {
  return fetchJson<JobRecord>("/api/jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Yoklama sırasında geçici ağ hatası tek bir turu boşa harcamasın: 3 kez denenir. */
export async function getJob(jobId: string, signal?: AbortSignal): Promise<JobRecord> {
  const apiPath = `/api/jobs/${encodeURIComponent(jobId)}`;
  let lastError: unknown = null;

  for (let attempt = 1; attempt <= JOB_POLL_RETRIES; attempt++) {
    try {
      return await fetchJson<JobRecord>(apiPath, { signal });
    } catch (error: unknown) {
      lastError = error;
      if (!isTransientError(error) || attempt === JOB_POLL_RETRIES) {
        throw error;
      }
      await delay(400 * attempt, signal);
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

/** İptal uç noktası iş kaydının tamamını döner (`{ok}` değil). */
export async function cancelJob(jobId: string): Promise<JobRecord> {
  return fetchJson<JobRecord>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
  });
}

/**
 * İş terminal duruma (`done|error`) gelene kadar yoklar.
 * Zaman aşımında `JobTimeoutError` fırlatır; `signal` iptalinde abort hatası.
 */
export async function waitForJob(
  jobId: string,
  timeoutSeconds: number = JOB_TIMEOUT_SECONDS,
  pollIntervalSeconds: number = 3,
  signal?: AbortSignal
): Promise<JobRecord> {
  const controller = new AbortController();
  let timedOut = false;
  let lastJob: JobRecord | null = null;

  const forwardAbort = (): void => controller.abort(signal?.reason ?? new Error("Aborted"));
  if (signal) {
    if (signal.aborted) {
      forwardAbort();
    } else {
      signal.addEventListener("abort", forwardAbort, { once: true });
    }
  }

  const timeoutMs = Math.max(1, timeoutSeconds) * 1000;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    for (;;) {
      lastJob = await getJob(jobId, controller.signal);
      if (isTerminalStatus(lastJob.status)) {
        return lastJob;
      }
      await delay(pollIntervalSeconds * 1000, controller.signal);
    }
  } catch (error: unknown) {
    if (timedOut) {
      throw new JobTimeoutError(jobId, timeoutSeconds, lastJob);
    }
    throw error;
  } finally {
    clearTimeout(timer);
    if (signal) {
      signal.removeEventListener("abort", forwardAbort);
    }
  }
}

async function collectMediaFiles(
  dir: string,
  relativePrefix: string,
  isOutput: boolean,
  extensionFilter: string | undefined,
  out: Array<MediaFileInfo & { mtimeMs: number }>
): Promise<void> {
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch (error: unknown) {
    const code = (error as { code?: string }).code;
    if (code === "ENOENT" && relativePrefix) {
      return; // uploads dizini henüz oluşmamış olabilir
    }
    throw error;
  }

  const wanted = extensionFilter?.toLowerCase().replace(".", "");

  for (const entry of entries) {
    if (!entry.isFile()) {
      continue;
    }
    const ext = path.extname(entry.name).toLowerCase().replace(".", "");
    if (wanted && ext !== wanted) {
      continue;
    }
    const relativeName = relativePrefix ? `${relativePrefix}/${entry.name}` : entry.name;
    try {
      const stats = await fs.stat(path.join(dir, entry.name));
      out.push({
        filename: relativeName,
        sizeBytes: stats.size,
        mtimeMs: stats.mtimeMs,
        modifiedAt: stats.mtime.toISOString(),
        publicUrl: mediaPublicUrl(relativeName),
        isOutput,
      });
    } catch {
      // kaldırılmış/geçici dosya: atla
    }
  }
}

/**
 * Medya dizinini listeler: kökteki üretim çıktıları ve `uploads/` alt dizinindeki yüklemeler.
 * `isOutput` dizin bilgisinden gelir, dosya adından tahmin edilmez.
 */
export async function listMediaFiles(
  limit: number = 30,
  extensionFilter?: string
): Promise<MediaFileInfo[]> {
  try {
    const files: Array<MediaFileInfo & { mtimeMs: number }> = [];
    await collectMediaFiles(MEDIA_DIR, "", true, extensionFilter, files);
    await collectMediaFiles(path.join(MEDIA_DIR, "uploads"), "uploads", false, extensionFilter, files);

    files.sort((a, b) => b.mtimeMs - a.mtimeMs);
    return files.slice(0, limit).map(({ mtimeMs: _ignored, ...rest }) => rest);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Failed to list media files: ${message}`);
  }
}

/**
 * Dosyayı API üzerinden yükler (`POST /api/uploads`, multipart alanı `file`).
 * Böylece içerik tipi ve 50 MB sınırı backend tarafından doğrulanır; `MEDIA_DIR`'e doğrudan yazılmaz.
 */
export async function uploadMedia(
  buffer: Buffer,
  filename: string
): Promise<{ uploadPath: string; publicUrl: string; sizeBytes: number; serverUrl: string | null }> {
  const extension = path.extname(filename).toLowerCase();
  const mimeType = UPLOAD_MIME_TYPES[extension];
  if (!mimeType) {
    throw new Error(
      `Unsupported file extension: ${extension || "(none)"}. Supported:PNG, JPG, JPEG, WebP, MP3, WAV, FLAC, OGG, WebM, M4A`
    );
  }
  if (buffer.length === 0) {
    throw new Error("Uploaded file is empty");
  }
  if (buffer.length > MAX_UPLOAD_BYTES) {
    throw new Error(
      `File is ${(buffer.length / (1024 * 1024)).toFixed(1)} MB, the API accepts at most ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB`
    );
  }

  const form = new FormData();
  form.append("file", new Blob([new Uint8Array(buffer)], { type: mimeType }), path.basename(filename) || `upload${extension}`);

  const response = await fetch(`${API_BASE}/api/uploads`, {
    method: "POST",
    body: form,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new ApiError(
      `Upload failed (${response.status} ${response.statusText}): ${errorText}`,
      response.status
    );
  }

  const payload = (await response.json()) as { name?: unknown; url?: unknown };
  const name = typeof payload.name === "string" ? payload.name : "";
  if (!name) {
    throw new Error(`Upload response did not contain a file name: ${JSON.stringify(payload)}`);
  }

  return {
    uploadPath: name,
    publicUrl: mediaPublicUrl(name),
    sizeBytes: buffer.length,
    serverUrl: typeof payload.url === "string" ? `${API_BASE}${payload.url}` : null,
  };
}

export async function readMediaBase64(
  filenameOrRelative: string,
  maxSizeKb: number = 4096
): Promise<{ mimeType: string; base64: string; sizeBytes: number; filename: string }> {
  const normalized = path.normalize(filenameOrRelative).replace(/^(\.\.(\/|\\|$))+/, "");
  const fullPath = path.join(MEDIA_DIR, normalized);
  const resolved = path.resolve(fullPath);
  const root = path.resolve(MEDIA_DIR);
  if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error(`Path escapes the media directory: ${filenameOrRelative}`);
  }

  const stats = await fs.stat(resolved);
  if (stats.size > maxSizeKb * 1024) {
    throw new Error(`File size (${Math.round(stats.size / 1024)} KB) exceeds maximum allowed size of ${maxSizeKb} KB`);
  }

  const ext = path.extname(resolved).toLowerCase();
  const mimeType = MEDIA_MIME_TYPES[ext] || "application/octet-stream";

  const data = await fs.readFile(resolved);
  return {
    mimeType,
    base64: data.toString("base64"),
    sizeBytes: stats.size,
    filename: path.relative(root, resolved).split(path.sep).join("/"),
  };
}

/** `mode` verilirse sorguya eklenir; verilmezse istek modsuz gider (backend tüm modları döner). */
function modeQuery(mode?: string | null): string {
  const clean = typeof mode === "string" ? mode.trim() : "";
  return clean ? `?mode=${encodeURIComponent(clean)}` : "";
}

export async function listThreads(mode?: string): Promise<ThreadItem[]> {
  return fetchJson<ThreadItem[]>(`/api/threads${modeQuery(mode)}`);
}

export async function getThread(threadId: string): Promise<ThreadItem> {
  return fetchJson<ThreadItem>(`/api/threads/${encodeURIComponent(threadId)}`);
}

export async function deleteThread(threadId: string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/threads/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
}

export async function listPresets(mode?: string): Promise<PresetItem[]> {
  return fetchJson<PresetItem[]>(`/api/presets${modeQuery(mode)}`);
}

export async function savePreset(mode: string, name: string, params: Record<string, unknown>): Promise<{ id: string }> {
  return fetchJson<{ id: string }>("/api/presets", {
    method: "POST",
    body: JSON.stringify({ mode, name, params }),
  });
}

export async function deletePreset(presetId: string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/presets/${encodeURIComponent(presetId)}`, {
    method: "DELETE",
  });
}

export async function getSettings(): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>("/api/settings");
}

export async function updateSetting(key: string, value: unknown): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/api/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify(value),
  });
}

export async function runColabSessions(): Promise<string> {
  try {
    const { stdout, stderr } = await execAsync(`"${COLAB_BIN}" sessions 2>&1`);
    return stdout || stderr || "No output returned.";
  } catch (error: unknown) {
    if (error && typeof error === "object" && "message" in error) {
      const err = error as { message: string; stdout?: string; stderr?: string };
      return `Error executing colab sessions: ${err.message}\n${err.stdout || ""} ${err.stderr || ""}`;
    }
    return `Error executing colab sessions: ${String(error)}`;
  }
}

export async function getSystemStatus(): Promise<SystemHealthSummary> {
  let serviceStatus = "unknown";
  try {
    const { stdout } = await execAsync("systemctl --user is-active colab-studio || true");
    serviceStatus = stdout.trim();
  } catch {}

  let uptime = "";
  try {
    const { stdout } = await execAsync("uptime -p");
    uptime = stdout.trim();
  } catch {}

  let diskUsage = "";
  try {
    const { stdout } = await execAsync(`df -h "${DATA_DIR}" | tail -n +2`);
    diskUsage = stdout.trim();
  } catch {}

  let memoryUsage = "";
  try {
    const { stdout } = await execAsync("free -h");
    memoryUsage = stdout.trim();
  } catch {}

  const colabSessions = await runColabSessions();

  return {
    service: serviceStatus,
    uptime,
    diskUsage,
    memoryUsage,
    colabSessions,
  };
}

export async function restartService(): Promise<string> {
  try {
    const { stdout, stderr } = await execAsync("systemctl --user restart colab-studio");
    return stdout || stderr || "Service restarted successfully.";
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Failed to restart colab-studio (installed as a systemd user service by install.sh --service): ${message}`);
  }
}
