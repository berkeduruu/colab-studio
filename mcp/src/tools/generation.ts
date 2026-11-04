import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
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

/** Zaman aşımında iş iptal edilir: mesaj bu ifadeyi taşır. */
function timeoutMessage(jobId: string, timeoutSeconds: number): string {
  return `Job ${jobId} did not finish within ${timeoutSeconds} seconds — iş arka planda sürüyor, iptal edildi.`;
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
    `Prompt: "${job.prompt}"`,
  ];

  if (typeof job.stage === "string" && job.stage.length > 0) {
    lines.push(`Stage: ${job.stage}`);
  }
  if (job.error) {
    lines.push(cancelled ? "Error: İptal edildi (the job was cancelled)" : `Error: ${job.error}`);
  }
  if (job.text) {
    lines.push(`Result Text:\n${job.text}`);
  }
  if (job.outputs.length > 0) {
    lines.push(`Outputs (${job.outputs.length} files):`);
    for (const output of job.outputs) {
      lines.push(`  - File: ${output} -> ${api.mediaPublicUrl(output)}`);
    }
  }

  return lines.join("\n");
}

/**
 * İşi bekler; süre dolarsa işi iptal eder (`POST /api/jobs/{id}/cancel`) ve durum metni döner.
 * `AbortSignal` ile bekleme turu iptal edilebilir; iş bitince zamanlayıcı temizlenir.
 */
async function waitForJobText(
  jobId: string,
  timeoutSeconds: number,
  pollIntervalSeconds: number,
  finishedText: (job: api.JobRecord) => string
): Promise<string> {
  const controller = new AbortController();
  const hardStopMs = (timeoutSeconds + 30) * 1000;
  const hardStop = setTimeout(() => controller.abort(), hardStopMs);

  try {
    const job = await api.waitForJob(jobId, timeoutSeconds, pollIntervalSeconds, controller.signal);
    return finishedText(job);
  } catch (error: unknown) {
    if (!(error instanceof api.JobTimeoutError)) {
      throw error;
    }

    let finalJob: api.JobRecord | null = error.lastJob;
    let cancelReport: string;
    try {
      finalJob = await api.cancelJob(jobId);
      cancelReport = api.isCancelledJob(finalJob)
        ? "Cancel confirmed: the job is marked as cancelled (İptal edildi)."
        : `Cancel requested; job is currently ${String(finalJob.status).toUpperCase()}${finalJob.error ? ` (${finalJob.error})` : ""}.`;
    } catch (cancelError: unknown) {
      cancelReport = `Cancel request failed: ${errorMessage(cancelError)}`;
    }

    const summary = finalJob ? `\n\n${formatJobSummary(finalJob)}` : "";
    return `${timeoutMessage(jobId, timeoutSeconds)} ${cancelReport}${summary}\nUse get_job with this ID to read the final state.`;
  } finally {
    clearTimeout(hardStop);
    controller.abort();
  }
}

export function registerGenerationTools(server: McpServer): void {
  server.tool(
    "generate_image",
    "Submit an image generation job using Qwen-Image 2.1 / Noct-Q V4. If GPU is off, it will be automatically queued and started.",
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
      wait: z.boolean().default(false).describe("If true, wait until job completes and return output URLs directly"),
      timeout_seconds: z.number().int().min(10).max(3600).default(api.JOB_TIMEOUT_SECONDS).describe("Timeout in seconds if wait is true; the job is cancelled when it expires"),
    },
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
    }) => {
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
          );
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `Image generation job queued successfully!\nJob ID: ${job.id}\nUse 'get_job' or 'wait_for_job' to track progress.\n\n${formatJobSummary(job)}`,
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
      wait: z.boolean().default(true).describe("Whether to wait for prompt enhancement to complete (usually takes 5-15s)"),
      timeout_seconds: z.number().int().min(10).max(3600).default(180),
    },
    async ({ prompt, reference_images, wait, timeout_seconds }) => {
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
          );
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `Prompt enhancement job queued.\nJob ID: ${job.id}\nUse 'get_job' with this ID to read the enhanced text.`,
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
      timeout_seconds: z.number().int().min(10).max(3600).default(api.JOB_TIMEOUT_SECONDS).describe("Timeout in seconds if wait is true; the job is cancelled when it expires"),
    },
    async ({ prompt, quality, ratio, seconds, turbo, seed, reference_images, thread_id, wait, timeout_seconds }) => {
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
          );
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `Video generation job queued!\nJob ID: ${job.id}\n${formatJobSummary(job)}`,
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
      timeout_seconds: z.number().int().min(10).max(3600).default(api.JOB_TIMEOUT_SECONDS).describe("Timeout in seconds if wait is true; the job is cancelled when it expires"),
    },
    async ({ prompt, lyrics, seconds, seed, thread_id, wait, timeout_seconds }) => {
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
          );
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `Music generation job queued!\nJob ID: ${job.id}\n${formatJobSummary(job)}`,
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
    "Text-to-speech synthesis using Chatterbox (24 languages, Turkish included) or Qwen3-TTS (11 languages, no Turkish). Language must be supported by the chosen engine; the default follows the engine (chatterbox: Turkish, qwen3: Auto).",
    {
      text: z.string().min(1).describe("The speech text to synthesize"),
      engine: z.enum(["chatterbox", "qwen3"]).default("chatterbox").describe("TTS engine: 'chatterbox' supports Turkish and 23 more languages, 'qwen3' has no Turkish support"),
      language: z
        .enum(ALL_SPEECH_LANGUAGES)
        .optional()
        .describe("Speech language. Defaults: chatterbox -> Turkish, qwen3 -> Auto. qwen3 supports only: Auto, English, Chinese, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian"),
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
      timeout_seconds: z.number().int().min(10).max(3600).default(api.JOB_TIMEOUT_SECONDS).describe("Timeout in seconds if wait is true; the job is cancelled when it expires"),
    },
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
    }) => {
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
          );
          return { content: [{ type: "text", text: textOut }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `Speech job queued!\nJob ID: ${job.id}\n${formatJobSummary(job)}`,
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
      timeout_seconds: z.number().int().min(10).max(3600).default(300),
    },
    async ({ audio_file, language, wait, timeout_seconds }) => {
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
          );
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `Transcription job queued.\nJob ID: ${job.id}\nUse 'get_job' to check result.`,
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
      timeout_seconds: z.number().int().min(10).max(3600).default(api.JOB_TIMEOUT_SECONDS).describe("Timeout in seconds if wait is true; the job is cancelled when it expires"),
    },
    async ({ image_file, prompt, resolution, texture, faces, seed, thread_id, wait, timeout_seconds }) => {
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
          );
          return { content: [{ type: "text", text }] };
        }

        return {
          content: [
            {
              type: "text",
              text: `3D model generation job queued!\nJob ID: ${job.id}\n${formatJobSummary(job)}`,
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
    "Check the status, real-time progress %, current stage, and outputs of any job (terminal statuses: done, error).",
    {
      job_id: z.string().min(1).describe("The unique ID of the job"),
    },
    async ({ job_id }) => {
      try {
        const job = await api.getJob(job_id);
        return {
          content: [
            {
              type: "text",
              text: `${formatJobSummary(job)}\n\n=== Raw Job JSON ===\n${JSON.stringify(job, null, 2)}`,
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
    "Wait/poll for a job until it reaches a terminal status (done or error). Cancelled jobs end as error with error=\"İptal edildi\". When the timeout expires the job is cancelled.",
    {
      job_id: z.string().min(1).describe("The unique ID of the job"),
      timeout_seconds: z.number().int().min(10).max(7200).default(api.JOB_TIMEOUT_SECONDS).describe("Maximum seconds to wait (the job is cancelled when it expires)"),
      poll_interval_seconds: z.number().int().min(1).max(30).default(3).describe("Polling interval in seconds"),
    },
    async ({ job_id, timeout_seconds, poll_interval_seconds }) => {
      try {
        const text = await waitForJobText(job_id, timeout_seconds, poll_interval_seconds, (job) =>
          `Job reached terminal status:\n\n${formatJobSummary(job)}`
        );
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
    "Cancel a queued or currently executing job. The endpoint returns the full job record; a job is cancelled when it ends as error=\"İptal edildi\".",
    {
      job_id: z.string().min(1).describe("The unique ID of the job to cancel"),
    },
    async ({ job_id }) => {
      try {
        const job = await api.cancelJob(job_id);

        let text: string;
        if (api.isCancelledJob(job)) {
          text = `Job ${job_id} was successfully cancelled (status: ${job.status}, error: "${job.error}").`;
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
