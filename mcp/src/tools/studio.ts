import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import * as api from "../api.js";

const MODE = z.enum(api.STUDIO_MODES);
const VERBOSE = z.boolean().default(false).describe("Return raw JSON instead of the summary");

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function dateText(seconds: unknown): string {
  return typeof seconds === "number" && Number.isFinite(seconds)
    ? new Date(seconds * 1000).toISOString().slice(0, 16).replace("T", " ")
    : "?";
}

function short(text: unknown, max: number = 80): string {
  const value = typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

/** Thread içindeki iş: tek satır (kimlik, tür, durum, kısa istem, çıktı adları). */
function jobLine(job: api.JobRecord): string {
  const outputs = job.outputs.length > 0 ? ` -> ${job.outputs.join(", ")}` : "";
  const error = job.status === "error" && job.error ? ` (${short(job.error, 60)})` : "";
  return `- ${job.id} | ${job.kind} | ${job.status}${error} | "${short(job.prompt)}"${outputs}`;
}

function ok(text: string) {
  return { content: [{ type: "text" as const, text }] };
}

function fail(prefix: string, error: unknown) {
  return { isError: true, content: [{ type: "text" as const, text: `${prefix}: ${errorText(error)}` }] };
}

export function registerStudioTools(server: McpServer): void {
  server.tool(
    "list_threads",
    "List generation threads (id, mode, title, date), newest first. Omit 'mode' to list threads of every mode.",
    {
      mode: MODE.optional().describe("Mode to filter threads by"),
      limit: z.number().int().min(1).max(500).default(30).describe("Newest N threads"),
      verbose: VERBOSE,
    },
    { readOnlyHint: true },
    async ({ mode, limit, verbose }) => {
      try {
        const all = await api.listThreads(mode);
        if (all.length === 0) {
          return ok("No threads found.");
        }
        const threads = all.slice(0, limit);
        if (verbose) {
          return ok(JSON.stringify(threads));
        }
        return ok(
          [`${threads.length} of ${all.length} threads, newest first:`, ...threads.map((t) => `- ${t.id} | ${t.mode} | ${dateText(t.created_at)} | ${short(t.title, 60)}`)].join("\n")
        );
      } catch (error: unknown) {
        return fail("Failed to list threads", error);
      }
    }
  );

  server.tool(
    "get_thread",
    "Get a thread and its job history, one line per job (id, kind, status, prompt, outputs). Use get_job for one job's full details.",
    {
      thread_id: z.string().min(1).describe("The unique thread ID"),
      verbose: VERBOSE,
    },
    { readOnlyHint: true },
    async ({ thread_id, verbose }) => {
      try {
        const thread = await api.getThread(thread_id);
        if (verbose) {
          return ok(JSON.stringify(thread));
        }
        const jobs = thread.jobs ?? [];
        return ok(
          [
            `Thread ${thread.id} | ${thread.mode} | ${dateText(thread.created_at)} | ${short(thread.title, 60)}`,
            `${jobs.length} jobs (oldest first):`,
            ...jobs.map(jobLine),
          ].join("\n")
        );
      } catch (error: unknown) {
        return fail("Failed to get thread", error);
      }
    }
  );

  server.tool(
    "list_presets",
    "List saved parameter presets (id, mode, name, params). Omit 'mode' to list presets of every mode.",
    {
      mode: MODE.optional().describe("Mode to filter presets by"),
    },
    { readOnlyHint: true },
    async ({ mode }) => {
      try {
        const presets = await api.listPresets(mode);
        if (presets.length === 0) {
          return ok("No presets found.");
        }
        return ok(
          [`${presets.length} presets:`, ...presets.map((p) => `- ${p.id} | ${p.mode} | ${p.name} | ${JSON.stringify(p.params)}`)].join("\n")
        );
      } catch (error: unknown) {
        return fail("Failed to list presets", error);
      }
    }
  );

  server.tool(
    "save_preset",
    "Save a parameter configuration as a named preset.",
    {
      mode: MODE.describe("Generation mode"),
      name: z.string().min(1).describe("Human-readable name for the preset"),
      params: z.record(z.string(), z.unknown()).describe("Dictionary of parameters to save"),
    },
    { destructiveHint: false },
    async ({ mode, name, params }) => {
      try {
        const result = await api.savePreset(mode, name, params);
        return ok(`Preset '${name}' saved successfully with ID: ${result.id}`);
      } catch (error: unknown) {
        return fail("Failed to save preset", error);
      }
    }
  );

  server.tool(
    "delete_preset",
    "Delete a saved preset by ID.",
    {
      preset_id: z.string().min(1).describe("The preset ID"),
    },
    { destructiveHint: true, idempotentHint: true },
    async ({ preset_id }) => {
      try {
        const result = await api.deletePreset(preset_id);
        return ok(result.ok ? `Preset ${preset_id} was deleted.` : `Delete preset returned: ${JSON.stringify(result)}`);
      } catch (error: unknown) {
        return fail("Failed to delete preset", error);
      }
    }
  );

  server.tool(
    "get_settings",
    "Retrieve current global studio settings (default parameters across all modes).",
    {},
    { readOnlyHint: true },
    async () => {
      try {
        return ok(JSON.stringify(await api.getSettings()));
      } catch (error: unknown) {
        return fail("Failed to get settings", error);
      }
    }
  );

  server.tool(
    "get_system_status",
    "Check the studio host: colab-studio service, data disk usage, RAM, and Colab CLI sessions.",
    {},
    { readOnlyHint: true },
    async () => {
      try {
        const health = await api.getSystemStatus();
        const summary = [
          `Colab Studio service: ${health.service.toUpperCase()}`,
          `Host Uptime: ${health.uptime}`,
          "",
          "Disk Usage:",
          health.diskUsage,
          "",
          "Memory Usage:",
          health.memoryUsage,
          "",
          "Active Colab CLI Sessions:",
          health.colabSessions,
        ].join("\n");
        return ok(summary);
      } catch (error: unknown) {
        return fail("Failed to retrieve system status", error);
      }
    }
  );

  // Yönetici araçları (MCP_ADMIN_TOOLS=1): veriyi siler, ayar yazar ya da servisi yeniden başlatır.
  // Aynı stüdyoyu başka bir ajan da kullanabildiği için varsayılan olarak listelenmezler.
  if (!api.ADMIN_TOOLS) {
    return;
  }

  server.tool(
    "delete_thread",
    "Delete a thread and its message records from Colab Studio.",
    {
      thread_id: z.string().min(1).describe("The thread ID to delete"),
    },
    { destructiveHint: true, idempotentHint: true },
    async ({ thread_id }) => {
      try {
        const result = await api.deleteThread(thread_id);
        return ok(result.ok ? `Thread ${thread_id} deleted successfully.` : `Thread delete returned: ${JSON.stringify(result)}`);
      } catch (error: unknown) {
        return fail("Failed to delete thread", error);
      }
    }
  );

  server.tool(
    "update_setting",
    "Update a studio configuration setting key.",
    {
      key: z.enum([
        "mode",
        "image.params",
        "video.params",
        "music.params",
        "speech.params",
        "transcribe.params",
        "model3d.params",
      ]).describe("Setting key name"),
      value: z.unknown().describe("Setting value (string or object)"),
    },
    { destructiveHint: false, idempotentHint: true },
    async ({ key, value }) => {
      try {
        const result = await api.updateSetting(key, value);
        return ok(result.ok ? `Setting '${key}' updated successfully.` : `Update returned: ${JSON.stringify(result)}`);
      } catch (error: unknown) {
        return fail("Failed to update setting", error);
      }
    }
  );

  server.tool(
    "restart_colab_studio",
    "Restart the colab-studio systemd user service (only when installed with install.sh --service). Interrupts running jobs of every client.",
    {},
    { destructiveHint: true },
    async () => {
      try {
        return ok(await api.restartService());
      } catch (error: unknown) {
        return fail("Failed to restart service", error);
      }
    }
  );
}
