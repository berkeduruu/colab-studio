import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import * as api from "../api.js";

function numberText(value: unknown, digits: number = 2, suffix: string = ""): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : "n/a";
}

function bytesToMb(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value / (1024 * 1024))} MB`
    : "n/a";
}

function percent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value)}%` : "n/a";
}

function creditsLine(credits: api.RuntimeCredits): string {
  if (!credits) {
    return "Credits Balance: unknown (runtime has not read the Colab usage yet)";
  }
  if ("error" in credits && typeof credits.error === "string" && credits.error.length > 0) {
    return `Credits Balance: unavailable — ${credits.error}`;
  }
  const info = credits as api.CreditsInfo;
  return `Credits Balance: ${numberText(info.balance)} units (Burn Rate: ${numberText(info.rate)}/hr)`;
}

function gpuNowLine(gpuNow: api.GpuNow | null | undefined): string {
  if (!gpuNow) {
    return "GPU Metrics: off / not streaming";
  }
  return [
    `GPU Load: ${percent(gpuNow.gpu)}`,
    `VRAM: ${bytesToMb(gpuNow.vram_used)} / ${bytesToMb(gpuNow.vram_total)}`,
  ].join(" | ");
}

function queueLine(queue: api.RuntimeStatus["queue"]): string {
  if (!queue) {
    return "Queue: unavailable";
  }
  const order = Array.isArray(queue.order) ? queue.order.length : 0;
  const current = queue.current;
  if (!current) {
    return `Queue: ${order} jobs pending, Current: idle`;
  }
  const stage = typeof current.stage === "string" && current.stage.length > 0 ? current.stage : current.status;
  const prompt = typeof current.prompt === "string" && current.prompt.length > 0
    ? ` — "${current.prompt.slice(0, 60)}${current.prompt.length > 60 ? "…" : ""}"`
    : "";
  return `Queue: ${order} jobs pending, Current: ${current.kind} (${stage}, ${percent((current.progress ?? 0) * 100)})${prompt}`;
}

function sessionLine(status: api.RuntimeStatus): string {
  const seconds = status.session_seconds;
  const spent = status.session_spent;
  const duration = typeof seconds === "number" && Number.isFinite(seconds) ? `${Math.round(seconds / 60)} min` : "inactive";
  const cost = typeof spent === "number" && Number.isFinite(spent) ? `${spent.toFixed(2)} units` : "n/a";
  return `Session: ${duration} (Cost: ${cost})`;
}

function startupLine(status: api.RuntimeStatus): string | null {
  const startup = status.startup;
  if (!startup) {
    return null;
  }
  const remaining = typeof startup.remaining_seconds === "number"
    ? `${Math.max(1, Math.round(startup.remaining_seconds / 60))} min left`
    : "unknown";
  const step = Array.isArray(startup.steps) && typeof startup.step === "number"
    ? startup.steps[startup.step] ?? "?"
    : "?";
  const progress = typeof startup.progress === "number" ? ` ${percent(startup.progress * 100)}` : "";
  return `Startup: ${step}${progress}, ${remaining}`;
}

/** Model paketlerinin indirme durumu tek satırda: "image 100%, video 42% (~3 min)". */
function packsLine(packs: api.RuntimeStatus["packs"]): string | null {
  if (!packs || typeof packs !== "object") {
    return null;
  }
  const parts = Object.entries(packs).map(([name, raw]) => {
    const pack = (raw || {}) as { fraction?: unknown; eta?: unknown };
    const fraction = typeof pack.fraction === "number" ? pack.fraction : null;
    const eta = typeof pack.eta === "number" && pack.eta > 0 ? ` (~${Math.max(1, Math.round(pack.eta / 60))} min)` : "";
    return `${name} ${fraction === null ? "?" : percent(fraction * 100)}${fraction !== null && fraction < 1 ? eta : ""}`;
  });
  return parts.length > 0 ? `Model packs on GPU disk: ${parts.join(", ")}` : null;
}

const VERBOSE = z.boolean().default(false).describe("Also return the raw JSON");

export function registerRuntimeTools(server: McpServer): void {
  server.tool(
    "colab_runtime_status",
    "Inspect the Colab L4 GPU runtime: phase, credits, queue, model packs and idle countdown. Not needed before generate_*: jobs start the GPU themselves.",
    { verbose: VERBOSE },
    { readOnlyHint: true },
    async ({ verbose }) => {
      try {
        const status = await api.getRuntimeStatus();
        const lines = [
          `Phase: ${String(status.phase ?? "unknown").toUpperCase()}`,
          `Detail: ${status.detail || "None"}`,
          `Auto-Shutdown: ${status.auto_shutdown ? `Enabled (${status.idle_minutes} min idle threshold)` : "Disabled"}`,
          creditsLine(status.credits ?? null),
          sessionLine(status),
          `GPU: ${status.gpu ?? "unknown"}`,
          gpuNowLine(status.gpu_now),
          queueLine(status.queue),
        ];
        const startup = startupLine(status);
        if (startup) {
          lines.push(startup);
        }
        const packs = packsLine(status.packs);
        if (packs) {
          lines.push(packs);
        }
        if (typeof status.idle_seconds === "number" && status.auto_shutdown) {
          lines.push(`Idle: ${Math.round(status.idle_seconds / 60)} min of ${status.idle_minutes}`);
        }

        const raw = verbose ? `\n\nRaw: ${JSON.stringify(status)}` : "";
        return {
          content: [{ type: "text", text: `${lines.join("\n")}${raw}` }],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to retrieve runtime status: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "colab_runtime_start",
    "Pre-warm the Colab L4 GPU (cold boot ~4-5 min, uses credits). Usually unnecessary: generate_* starts the GPU itself with that job's models first; this tool always prepares image models first.",
    {},
    { destructiveHint: false, idempotentHint: true },
    async () => {
      try {
        const result = await api.startRuntime();
        return {
          content: [
            {
              type: "text",
              text: [
                "Colab GPU runtime start requested.",
                `Current Phase: ${result.phase}`,
                `Detail: ${result.detail || "Initiating startup"}`,
                startupLine(result),
              ].filter(Boolean).join("\n"),
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to start runtime: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "colab_runtime_stop",
    "Immediately shut down the Colab GPU runtime and close SSH tunnel to save Google Colab compute units. A job running on the GPU (from any client of this studio) is interrupted.",
    {},
    { destructiveHint: true, idempotentHint: true },
    async () => {
      try {
        const result = await api.stopRuntime();
        return {
          content: [
            {
              type: "text",
              text: `Colab GPU runtime stopped successfully.\nCurrent Phase: ${result.phase}\nDetail: ${result.detail || "Stopped manually"}`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to stop runtime: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "colab_runtime_toggle_auto",
    "Toggle automatic idle shutdown (default is enabled: shuts down GPU after 15 minutes of idle time to prevent credit waste).",
    {},
    { destructiveHint: false },
    async () => {
      try {
        const result = await api.toggleAutoShutdown();
        return {
          content: [
            {
              type: "text",
              text: `Auto-shutdown is now: ${result.auto_shutdown ? "ENABLED (15m idle shutdown)" : "DISABLED (GPU will remain on until stopped manually)"}`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to toggle auto shutdown: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "colab_runtime_metrics",
    "Get real-time GPU hardware metrics (VRAM used/total, GPU utilization %, temperature, RAM and disk usage).",
    {
      limit: z.number().int().min(1).max(200).default(10).describe("How many of the newest metric samples to show"),
      verbose: VERBOSE,
    },
    { readOnlyHint: true },
    async ({ limit, verbose }) => {
      try {
        const metrics = await api.getRuntimeMetrics();
        if (metrics.length === 0) {
          return {
            content: [{ type: "text", text: "No metrics currently recorded (GPU is either off or starting up)." }],
          };
        }

        const slice = metrics.slice(-limit);
        const lines = slice.map((m: api.GpuMetricsSnapshot) => {
          const time = typeof m.t === "number" ? new Date(m.t * 1000).toISOString() : "n/a";
          return [
            `${time} | GPU ${percent(m.gpu)} | Temp ${numberText(m.temp, 0, "°C")}`,
            `VRAM ${bytesToMb(m.vram_used)}/${bytesToMb(m.vram_total)}`,
            `RAM ${bytesToMb(m.ram_used)}/${bytesToMb(m.ram_total)}`,
            `Disk ${bytesToMb(m.disk_used)}/${bytesToMb(m.disk_total)}`,
          ].join(" | ");
        });

        return {
          content: [
            {
              type: "text",
              text: `${slice.length} of ${metrics.length} samples (newest last):\n${lines.join("\n")}${verbose ? `\n\nRaw: ${JSON.stringify(slice)}` : ""}`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to retrieve metrics: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "colab_runtime_logs",
    "Read the recent watchdog, startup, and error logs from the Colab Studio runtime.",
    {
      limit: z.number().int().min(1).max(200).default(30).describe("Maximum number of recent log lines to retrieve"),
    },
    { readOnlyHint: true },
    async ({ limit }) => {
      try {
        const { lines } = await api.getRuntimeLogs();
        const slice = lines.slice(-limit);
        return {
          content: [
            {
              type: "text",
              text: slice.length > 0 ? slice.join("\n") : "No runtime logs available.",
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to retrieve logs: ${message}` }],
        };
      }
    }
  );

  // get_system_status aynı çıktıyı zaten içerir: yalnızca yönetici modunda.
  if (api.ADMIN_TOOLS) {
    server.tool(
      "colab_sessions_raw",
      "Directly execute `colab sessions` to inspect active Google Colab CLI sessions.",
      {},
      { readOnlyHint: true },
      async () => {
        try {
          const output = await api.runColabSessions();
          return {
            content: [{ type: "text", text: output }],
          };
        } catch (error: unknown) {
          const message = error instanceof Error ? error.message : String(error);
          return {
            isError: true,
            content: [{ type: "text", text: `Failed to run colab sessions: ${message}` }],
          };
        }
      }
    );
  }
}
