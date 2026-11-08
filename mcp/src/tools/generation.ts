import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ToolAnnotations } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import * as api from "../api.js";

/** TTS motorlarının desteklediği diller (`server/app.py:SPEECH_LANGUAGES`). */
const SPEECH_LANGUAGES: Record<string, string[]> = {
  chatterbox: [
    "Turkish", "English", "German", "French", "Spanish", "Italian", "Russian", "Arabic", "Portuguese",
    "Dutch", "Polish", "Swedish", "Danish", "Norwegian", "Finnish", "Greek", "Hebrew", "Hindi",
    "Japanese", "Korean", "Chinese", "Malay", "Swahili",
  ],
  qwen3: [
    "Auto", "English", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese",
    "Spanish", "Italian",
  ],
};

const ALL_SPEECH_LANGUAGES = Array.from(
  new Set([...SPEECH_LANGUAGES.chatterbox, ...SPEECH_LANGUAGES.qwen3])
) as [string, ...string[]];

/** Motora göre varsayılan dil: qwen3 Türkçe desteklemez, varsayılanı "Auto" olmalı. */
const DEFAULT_SPEECH_LANGUAGE: Record<string, string> = { chatterbox: "Turkish", qwen3: "Auto" };

/** Bekleme süresi dolduğunda iş varsayılan olarak sürer; yalnızca `cancel_on_timeout` iptal eder. */
const CANCEL_ON_TIMEOUT = z
  .boolean()
  .default(false)
  .describe("Cancel the job if the wait times out");

const WAIT_TIMEOUT = z
  .number()
  .int()
  .min(10)
  .max(3600)
  .default(api.JOB_TIMEOUT_SECONDS)
  .describe("Max seconds to wait if wait is true");

/** Üretim araçları kuyruğa iş ekler: yıkıcı değil, ama tekrar çağrı yeni iş (ve kredi) demektir. */
const SUBMIT_ANNOTATIONS: ToolAnnotations = { destructiveHint: false };

/** Özetteki istem kısaltılır: uzun (geliştirilmiş) istemler her yanıtta yeniden token harcamasın. */
const PROMPT_PREVIEW_CHARS = 160;

/** MCP ilerleme bildirimi için gereken kısım (`RequestHandlerExtra`'nın alt kümesi). */
interface ProgressExtra {
  _meta?: { progressToken?: string | number };
  sendNotification: (notification: {
    method: "notifications/progress";
    params: { progressToken: string | number; progress: number; total?: number; message?: string };
  }) => Promise<void>;
}

/**
 * İstemci `progressToken` verdiyse her yoklamada ilerleme bildirimi gönderir (yalnızca değişince):
 * bekleme sırasında kullanıcı ilerlemeyi görür, bazı istemciler zaman aşımını da sıfırlar.
 */
function progressReporter(extra: unknown): ((job: api.JobRecord) => void) | undefined {
  const e = extra as Partial<ProgressExtra> | undefined;
  const token = e?._meta?.progressToken;
  if (token === undefined || typeof e?.sendNotification !== "function") {
    return undefined;
  }
  let last = "";
  let step = 0;
  return (job) => {
    const pct = Math.round((Number.isFinite(job.progress) ? job.progress : 0) * 100);
    const message = `${job.status}${job.stage ? ` · ${job.stage}` : ""} · ${pct}%`;
    if (message === last) {
      return;
    }
    last = message;
    step += 1;
    // `progress` her bildirimde artmalı: yüzde düşebileceği için (aşama değişimi) sayaç kullanılır.
    void e.sendNotification!({
      method: "notifications/progress",
      params: { progressToken: token, progress: step, message },
    }).catch(() => undefined);
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatJobSummary(job: api.JobRecord): string {
  const cancelled = api.isCancelledJob(job);
  const lines = [
    `Job ID: ${job.id}`,
    `Kind: ${job.kind} (${job.mode})`,
    `Status: ${String(job.status).toUpperCase()}${cancelled ? " (cancelled)" : ""}`,
    `Progress: ${Math.round((Number.isFinite(job.progress) ? job.progress : 0) * 100)}%`,
    `Prompt: "${job.prompt.length > PROMPT_PREVIEW_CHARS ? `${job.prompt.slice(0, PROMPT_PREVIEW_CHARS)}…` : job.prompt}"`,
  ];
  const eta = job.live?.eta;
  if (!api.isTerminalStatus(job.status) && typeof eta === "number" && eta > 0) {
    lines.push(`ETA: ~${eta >= 90 ? `${Math.round(eta / 60)} min` : `${Math.round(eta)} s`}`);
  }

  if (typeof job.stage === "string" && job.stage.length > 0) {
    lines.push(`Stage: ${job.stage}`);
  }
  if (job.error) {
    lines.push(cancelled ? "Error: the job was cancelled" : `Error: ${job.error}`);
  }
  if (job.text) {
    lines.push(`Result Text:\n${job.text}`);
  }
  if (job.outputs.length > 0) {
    lines.push(`Outputs (${job.outputs.length}):`);
    for (const output of job.outputs) {
      lines.push(`  - ${output} -> ${api.mediaPublicUrl(output)}`);
    }
  }

  return lines.join("\n");
}

interface WaitOptions {
  cancelOnTimeout: boolean;
  onPoll?: (job: api.JobRecord) => void;
}

/**
 * İşi bekler. Süre dolarsa iş varsayılan olarak sürer ve durum metni döner; `cancelOnTimeout` verilirse
 * iptal edilir (`POST /api/jobs/{id}/cancel`). Kısa bir bekleme, GPU açılırken işi (ve açılışı)
 * öldürmesin diye iptal artık varsayılan değildir.
 */
async function waitForJobText(
  jobId: string,
  timeoutSeconds: number,
  pollIntervalSeconds: number,
  finishedText: (job: api.JobRecord) => string,
  options: WaitOptions = { cancelOnTimeout: false }
): Promise<string> {
  const controller = new AbortController();
  const hardStopMs = (timeoutSeconds + 30) * 1000;
  const hardStop = setTimeout(() => controller.abort(), hardStopMs);

  try {
    const job = await api.waitForJob(jobId, timeoutSeconds, pollIntervalSeconds, controller.signal, options.onPoll);
    return finishedText(job);
  } catch (error: unknown) {
    if (!(error instanceof api.JobTimeoutError)) {
      throw error;
    }

    if (!options.cancelOnTimeout) {
      const summary = error.lastJob ? `\n\n${formatJobSummary(error.lastJob)}` : "";
      return `Job ${jobId} is still in progress after ${timeoutSeconds} s (not cancelled).${summary}\nCall wait_for_job with this ID to keep waiting.`;
    }

    let finalJob: api.JobRecord | null = error.lastJob;
    let cancelReport: string;
    try {
      finalJob = await api.cancelJob(jobId);
      cancelReport = api.isCancelledJob(finalJob)
        ? "Cancel confirmed: the job is marked as cancelled."
        : `Cancel requested; job is currently ${String(finalJob.status).toUpperCase()}${finalJob.error ? ` (${finalJob.error})` : ""}.`;
    } catch (cancelError: unknown) {
      cancelReport = `Cancel request failed: ${errorMessage(cancelError)}`;
    }

    const summary = finalJob ? `\n\n${formatJobSummary(finalJob)}` : "";
    return `Job ${jobId} did not finish within ${timeoutSeconds} s and cancel_on_timeout was set. ${cancelReport}${summary}`;
  } finally {
    clearTimeout(hardStop);
    controller.abort();
  }
}

/** Kuyruğa alınan işin yanıtı: kimlik özetin ilk satırında, tekrar edilmez. */
function queuedText(label: string, job: api.JobRecord): string {
  return `${label} queued. Call wait_for_job with the Job ID (one call, waits until done).\n\n${formatJobSummary(job)}`;
}

export function registerGenerationTools(server: McpServer): void {
  server.tool(
    "generate_image",
    "Submit an image generation job using Qwen-Image 2.1 / Noct-Q V4. If the GPU is off it starts automatically (no need to call colab_runtime_start).",
    {
      prompt: z.string().min(1).describe("Detailed visual description of the desired image"),
      negative: z.string().optional().describe("Negative prompt to exclude unwanted concepts"),
      ratio: z.enum(["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"]).default("2:3").describe("Aspect ratio"),
      size: z.enum(["1K", "1.5K", "2K"]).default("1.5K").describe("Resolution budget (1K=1024x1024 equivalent, 1.5K, 2K)"),
      count: z.number().int().min(1).max(4).default(1).describe("Number of variations to produce (1-4)"),
      steps: z.number().int().min(1).max(60).default(25).describe("Sampling steps (default 25)"),
      cfg: z.number().min(1.0).max(10.0).default(3.0).describe("CFG scale (default 3.0)"),
      turbo: z.boolean().default(false).describe("Turbo lightning mode for faster inference"),
      seed: z.number().int().min(0).max(4294967295).optional().describe("Random seed for reproducible outputs"),
      edit_resolution: z.enum(["720p", "1080p", "1440p", "2160p"]).default("1080p").describe("Image editing resolution budget if reference images provided"),
      sampler: z.enum(["euler", "euler_ancestral", "res_multistep", "dpmpp_2m", "dpmpp_2m_sde", "er_sde", "uni_pc"]).default("euler"),
      scheduler: z.enum(["simple", "beta", "normal", "karras", "sgm_uniform"]).default("simple"),
      reference_images: z.array(z.string()).max(10).optional().describe("Array of uploaded image paths (e.g. ['uploads/abc.png']) for image-to-image editing"),
      thread_id: z.string().optional().describe("Optional thread ID to group generations"),
      wait: z.boolean().default(false),
      timeout_seconds: WAIT_TIMEOUT,
      cancel_on_timeout: CANCEL_ON_TIMEOUT,
    },
    SUBMIT_ANNOTATIONS,
    async ({
      prompt,
      negative,
      ratio,
      size,
      count,
      steps,
      cfg,
      turbo,
      seed,
      edit_resolution,
      sampler,
      scheduler,
      reference_images,
      thread_id,
      wait,
      timeout_seconds,
      cancel_on_timeout,
    }, extra) => {
      try {
        const params: Record<string, unknown> = {
          ratio,
          size,
          count,
          steps,
          cfg,
          turbo,
          edit_resolution,
          sampler,
          scheduler,
          negative: negative || "",
          seed: seed ?? null,
        };

        const job = await api.createJob({
          kind: "image",
          prompt,
          inputs: reference_images || [],
          params,
          thread_id,
        });

        if (wait) {
          const text = await waitForJobText(job.id, timeout_seconds, 3, (finished) =>
            `Image generation finished!\n\n${formatJobSummary(finished)}`
          , { cancelOnTimeout: cancel_on_timeout, onPoll: progressReporter(extra) });
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: queuedText("Image generation", job),
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to create image generation job: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "enhance_prompt",
    "Use Qwen3.5 9B on Colab to expand and enhance a concise prompt into a detailed, visually rich prompt for image generation.",
    {
      prompt: z.string().min(1).describe("The concise prompt or concept to expand"),
      reference_images: z.array(z.string()).max(10).optional().describe("Optional reference image names"),
      wait: z.boolean().default(true).describe("Wait for the text (5-15 s once the GPU is up)"),
      timeout_seconds: z.number().int().min(10).max(3600).default(600).describe("Max seconds to wait (covers a cold GPU start)"),
      cancel_on_timeout: CANCEL_ON_TIMEOUT,
    },
    SUBMIT_ANNOTATIONS,
    async ({ prompt, reference_images, wait, timeout_seconds, cancel_on_timeout }, extra) => {
      try {
        const job = await api.createJob({
          kind: "enhance",
          prompt,
          inputs: reference_images || [],
          params: {},
        });

        if (wait) {
          const text = await waitForJobText(job.id, timeout_seconds, 2, (finished) =>
            finished.text
              ? `Enhanced Prompt:\n\n${finished.text}`
              : `Prompt enhancement finished:\n${formatJobSummary(finished)}`
          , { cancelOnTimeout: cancel_on_timeout, onPoll: progressReporter(extra) });
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `Prompt enhancement queued. Job ID: ${job.id}\nCall wait_for_job with this ID to read the enhanced text.`,
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to enhance prompt: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "generate_video",
    "Generate video using MiniMax H3. Can generate text-to-video or image-to-video (using up to 2 reference images for start and end frames).",
    {
      prompt: z.string().min(1).describe("Video motion and scene description"),
      quality: z.enum(["480p", "768p"]).default("480p").describe("Video resolution: 480p is ~2.5x faster on L4"),
      ratio: z.enum(["16:9", "9:16", "1:1"]).default("16:9").describe("Aspect ratio"),
      seconds: z.number().min(2).max(10).default(5).describe("Video duration in seconds (2 to 10)"),
      turbo: z.boolean().default(true).describe("Turbo sampling mode"),
      seed: z.number().int().min(0).max(4294967295).optional(),
      reference_images: z.array(z.string()).max(2).optional().describe("Up to 2 reference image paths (first frame, optional second frame)"),
      thread_id: z.string().optional(),
      wait: z.boolean().default(false),
      timeout_seconds: WAIT_TIMEOUT,
      cancel_on_timeout: CANCEL_ON_TIMEOUT,
    },
    SUBMIT_ANNOTATIONS,
    async ({ prompt, quality, ratio, seconds, turbo, seed, reference_images, thread_id, wait, timeout_seconds, cancel_on_timeout }, extra) => {
      try {
        if (quality === "768p" && seconds >= 10 && !turbo) {
          throw new Error("768p 10s video without turbo exceeds the 30-minute limit. Choose turbo: true or 480p.");
        }

        const params: Record<string, unknown> = {
          quality,
          ratio,
          seconds,
          turbo,
          seed: seed ?? null,
        };

        const job = await api.createJob({
          kind: "video",
          prompt,
          inputs: reference_images || [],
          params,
          thread_id,
        });

        if (wait) {
          const text = await waitForJobText(job.id, timeout_seconds, 5, (finished) =>
            `Video generation finished!\n\n${formatJobSummary(finished)}`
          , { cancelOnTimeout: cancel_on_timeout, onPoll: progressReporter(extra) });
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: queuedText("Video generation", job),
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to create video job: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "generate_music",
    "Generate full AI music using YuE2.",
    {
      prompt: z.string().min(1).describe("Music genre, mood, instruments (e.g. 'acoustic pop ballad, acoustic guitar, emotional')"),
      lyrics: z.string().optional().describe("Song lyrics with structure tags like [verse], [chorus]"),
      seconds: z.number().min(10).max(360).default(120).describe("Song duration in seconds (10 to 360)"),
      seed: z.number().int().min(0).max(4294967295).optional(),
      thread_id: z.string().optional(),
      wait: z.boolean().default(false),
      timeout_seconds: WAIT_TIMEOUT,
      cancel_on_timeout: CANCEL_ON_TIMEOUT,
    },
    SUBMIT_ANNOTATIONS,
    async ({ prompt, lyrics, seconds, seed, thread_id, wait, timeout_seconds, cancel_on_timeout }, extra) => {
      try {
        const params: Record<string, unknown> = {
          lyrics: lyrics || "",
          seconds,
          seed: seed ?? null,
        };

        const job = await api.createJob({
          kind: "music",
          prompt,
          inputs: [],
          params,
          thread_id,
        });

        if (wait) {
          const text = await waitForJobText(job.id, timeout_seconds, 5, (finished) =>
            `Music generation finished!\n\n${formatJobSummary(finished)}`
          , { cancelOnTimeout: cancel_on_timeout, onPoll: progressReporter(extra) });
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: queuedText("Music generation", job),
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to create music job: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "generate_speech",
    "Text-to-speech with Chatterbox (24 languages incl. Turkish) or Qwen3-TTS (11 languages, no Turkish).",
    {
      text: z.string().min(1).describe("The speech text to synthesize"),
      engine: z.enum(["chatterbox", "qwen3"]).default("chatterbox"),
      language: z
        .enum(ALL_SPEECH_LANGUAGES)
        .optional()
        .describe("Default: Turkish (chatterbox) / Auto (qwen3). qwen3 only: Auto, English, Chinese, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian"),
      voice_preset: z.enum(["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"]).default("Vivian").describe("Voice preset for Qwen3-TTS"),
      instruct: z.string().optional().describe("Style/emotion instruction (e.g. 'whispering', 'cheerful')"),
      reference_text: z.string().optional().describe("Reference transcript if cloning voice"),
      exaggeration: z.number().min(0.25).max(2.0).default(0.5).describe("Emotion intensity (0.25 to 2.0)"),
      cfg_weight: z.number().min(0.0).max(1.0).default(0.5).describe("Pacing / CFG weight"),
      temperature: z.number().min(0.05).max(2.0).default(0.8),
      seed: z.number().int().min(0).max(4294967295).optional(),
      reference_audio: z.string().optional().describe("Optional path of uploaded audio file (e.g. 'uploads/sample.wav') for voice cloning"),
      thread_id: z.string().optional(),
      wait: z.boolean().default(false),
      timeout_seconds: WAIT_TIMEOUT,
      cancel_on_timeout: CANCEL_ON_TIMEOUT,
    },
    SUBMIT_ANNOTATIONS,
    async ({
      text,
      engine,
      language,
      voice_preset,
      instruct,
      reference_text,
      exaggeration,
      cfg_weight,
      temperature,
      seed,
      reference_audio,
      thread_id,
      wait,
      timeout_seconds,
      cancel_on_timeout,
    }, extra) => {
      try {
        const supported = SPEECH_LANGUAGES[engine];
        const resolvedLanguage = (language ?? DEFAULT_SPEECH_LANGUAGE[engine]).trim();
        if (!supported.includes(resolvedLanguage)) {
          throw new Error(
            `${engine} does not support language "${resolvedLanguage}". Supported: ${supported.join(", ")}.` +
              (engine === "qwen3" ? ' Use engine: "chatterbox" for Turkish.' : "")
          );
        }

        const params: Record<string, unknown> = {
          engine,
          language: resolvedLanguage,
          voice_preset,
          instruct: instruct || "",
          reference_text: reference_text || "",
          exaggeration,
          cfg_weight,
          temperature,
          seed: seed ?? null,
        };

        const inputs = reference_audio ? [reference_audio] : [];

        const job = await api.createJob({
          kind: "speech",
          prompt: text,
          inputs,
          params,
          thread_id,
        });

        if (wait) {
          const textOut = await waitForJobText(job.id, timeout_seconds, 3, (finished) =>
            `Speech synthesis finished!\n\n${formatJobSummary(finished)}`
          , { cancelOnTimeout: cancel_on_timeout, onPoll: progressReporter(extra) });
          return { content: [{ type: "text", text: textOut }] };
        }

        return {
          content: [
            {
              type: "text",
              text: queuedText("Speech synthesis", job),
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to create speech job: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "transcribe_audio",
    "Transcribe an uploaded audio file into text using Whisper on Colab GPU.",
    {
      audio_file: z.string().min(1).describe("Uploaded audio filename (e.g. 'uploads/record.mp3')"),
      language: z.enum(["auto", "Turkish", "English", "German", "French", "Spanish", "Italian", "Russian", "Arabic", "Japanese"]).default("auto"),
      wait: z.boolean().default(true).describe("Whether to wait for transcription to complete"),
      timeout_seconds: z.number().int().min(10).max(3600).default(600).describe("Max seconds to wait (covers a cold GPU start)"),
      cancel_on_timeout: CANCEL_ON_TIMEOUT,
    },
    SUBMIT_ANNOTATIONS,
    async ({ audio_file, language, wait, timeout_seconds, cancel_on_timeout }, extra) => {
      try {
        const job = await api.createJob({
          kind: "transcribe",
          prompt: "Ses kaydı",
          inputs: [audio_file],
          params: { language },
        });

        if (wait) {
          const text = await waitForJobText(job.id, timeout_seconds, 3, (finished) =>
            finished.text
              ? `Transcription Result:\n\n${finished.text}`
              : `Transcription finished:\n${formatJobSummary(finished)}`
          , { cancelOnTimeout: cancel_on_timeout, onPoll: progressReporter(extra) });
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `Transcription queued. Job ID: ${job.id}\nCall wait_for_job with this ID to read the text.`,
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to start transcription: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "generate_3d",
    "Generate a 3D mesh model (.glb / .ply) from an input image using TRELLIS.2 on Colab GPU.",
    {
      image_file: z.string().min(1).describe("Uploaded image filename in media (e.g. 'uploads/object.png')"),
      prompt: z.string().optional().describe("Optional prompt/label for the 3D model"),
      resolution: z.enum(["1024", "1536", "2048"]).default("1024").describe("Grid resolution (1024 is recommended for speed)"),
      texture: z.enum(["pbr", "vertex"]).default("pbr").describe("Texture mode: 'pbr' for full materials or 'vertex' for vertex colors"),
      faces: z.number().int().min(20000).max(1000000).default(200000).describe("Polygon face count target"),
      seed: z.number().int().min(0).max(4294967295).optional(),
      thread_id: z.string().optional(),
      wait: z.boolean().default(false),
      timeout_seconds: WAIT_TIMEOUT,
      cancel_on_timeout: CANCEL_ON_TIMEOUT,
    },
    SUBMIT_ANNOTATIONS,
    async ({ image_file, prompt, resolution, texture, faces, seed, thread_id, wait, timeout_seconds, cancel_on_timeout }, extra) => {
      try {
        const params: Record<string, unknown> = {
          resolution: parseInt(resolution, 10),
          texture,
          faces,
          seed: seed ?? null,
        };

        const job = await api.createJob({
          kind: "model3d",
          prompt: prompt || "3D model",
          inputs: [image_file],
          params,
          thread_id,
        });

        if (wait) {
          const text = await waitForJobText(job.id, timeout_seconds, 5, (finished) =>
            `3D generation finished!\n\n${formatJobSummary(finished)}`
          , { cancelOnTimeout: cancel_on_timeout, onPoll: progressReporter(extra) });
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: queuedText("3D model generation", job),
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to create 3D job: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "get_job",
    "One-off check of a job's status, progress, stage, ETA and outputs. To wait for a result use wait_for_job, not repeated get_job calls.",
    {
      job_id: z.string().min(1).describe("The unique ID of the job"),
      verbose: z.boolean().default(false).describe("Also return the raw job record"),
    },
    { readOnlyHint: true },
    async ({ job_id, verbose }) => {
      try {
        const job = await api.getJob(job_id);
        return {
          content: [
            {
              type: "text",
              text: verbose ? `${formatJobSummary(job)}\n\nRaw: ${JSON.stringify(job)}` : formatJobSummary(job),
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to get job: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "wait_for_job",
    "Block until a job is done or error and return its outputs; no tokens are spent while waiting. On timeout the job keeps running (unless cancel_on_timeout): call again. Cancelled jobs end as error and are reported as cancelled.",
    {
      job_id: z.string().min(1).describe("The unique ID of the job"),
      timeout_seconds: z.number().int().min(10).max(7200).default(api.JOB_TIMEOUT_SECONDS).describe("Max seconds to wait"),
      poll_interval_seconds: z.number().int().min(1).max(30).default(3).describe("Polling interval in seconds"),
      cancel_on_timeout: CANCEL_ON_TIMEOUT,
    },
    { destructiveHint: false, idempotentHint: true },
    async ({ job_id, timeout_seconds, poll_interval_seconds, cancel_on_timeout }, extra) => {
      try {
        const text = await waitForJobText(job_id, timeout_seconds, poll_interval_seconds, (job) =>
          `Job reached terminal status:\n\n${formatJobSummary(job)}`
        , { cancelOnTimeout: cancel_on_timeout, onPoll: progressReporter(extra) });
        return { content: [{ type: "text", text }] };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Error waiting for job: ${errorMessage(error)}` }],
        };
      }
    }
  );

  server.tool(
    "cancel_job",
    "Cancel a queued or currently executing job. The endpoint returns the full job record; a cancelled job ends with status error and is reported as cancelled.",
    {
      job_id: z.string().min(1).describe("The unique ID of the job to cancel"),
    },
    { destructiveHint: true, idempotentHint: true },
    async ({ job_id }) => {
      try {
        const job = await api.cancelJob(job_id);

        let text: string;
        if (api.isCancelledJob(job)) {
          text = `Job ${job_id} was successfully cancelled (status: ${job.status}).`;
        } else if (job.status === "running") {
          text = `Job ${job_id} cancellation was requested; it is still running and will stop when ComfyUI is interrupted.`;
        } else if (job.status === "done") {
          text = `Job ${job_id} already finished (status: done); nothing to cancel.`;
        } else if (job.status === "error") {
          text = `Job ${job_id} is already in error state: ${job.error ?? "unknown error"}.`;
        } else {
          text = `Job ${job_id} is now ${job.status}; cancellation is pending.`;
        }

        return {
          content: [{ type: "text", text: `${text}\n\n${formatJobSummary(job)}` }],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to cancel job: ${errorMessage(error)}` }],
        };
      }
    }
  );
}
