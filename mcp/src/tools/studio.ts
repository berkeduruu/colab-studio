import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import * as api from "../api.js";

export function registerStudioTools(server: McpServer): void {
  server.tool(
    "list_threads",
    "List generation threads in Colab Studio. Omit 'mode' to list threads of every mode (backend returns all modes when no mode is given).",
    {
      mode: z.enum(["image", "video", "music", "speech", "transcribe", "model3d"]).optional().describe("Mode to filter threads by"),
    },
    async ({ mode }) => {
      try {
        const threads = await api.listThreads(mode);
        return {
          content: [
            {
              type: "text",
              text: threads.length > 0
                ? JSON.stringify(threads, null, 2)
                : "No threads found.",
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to list threads: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "get_thread",
    "Get full details and job history of a specific conversation thread.",
    {
      thread_id: z.string().min(1).describe("The unique thread ID"),
    },
    async ({ thread_id }) => {
      try {
        const thread = await api.getThread(thread_id);
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(thread, null, 2),
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to get thread: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "delete_thread",
    "Delete a thread and its message records from Colab Studio.",
    {
      thread_id: z.string().min(1).describe("The thread ID to delete"),
    },
    async ({ thread_id }) => {
      try {
        const result = await api.deleteThread(thread_id);
        return {
          content: [
            {
              type: "text",
              text: result.ok ? `Thread ${thread_id} deleted successfully.` : `Thread delete returned: ${JSON.stringify(result)}`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to delete thread: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "list_presets",
    "List saved parameter presets. Omit 'mode' to list presets of every mode (backend returns all modes when no mode is given).",
    {
      mode: z.enum(["image", "video", "music", "speech", "transcribe", "model3d"]).optional().describe("Mode to filter presets by"),
    },
    async ({ mode }) => {
      try {
        const presets = await api.listPresets(mode);
        return {
          content: [
            {
              type: "text",
              text: presets.length > 0 ? JSON.stringify(presets, null, 2) : "No presets found.",
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to list presets: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "save_preset",
    "Save a parameter configuration as a named preset.",
    {
      mode: z.enum(["image", "video", "music", "speech", "transcribe", "model3d"]).describe("Generation mode"),
      name: z.string().min(1).describe("Human-readable name for the preset"),
      params: z.record(z.string(), z.unknown()).describe("Dictionary of parameters to save"),
    },
    async ({ mode, name, params }) => {
      try {
        const result = await api.savePreset(mode, name, params);
        return {
          content: [
            {
              type: "text",
              text: `Preset '${name}' saved successfully with ID: ${result.id}`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to save preset: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "delete_preset",
    "Delete a saved preset by ID.",
    {
      preset_id: z.string().min(1).describe("The preset ID"),
    },
    async ({ preset_id }) => {
      try {
        const result = await api.deletePreset(preset_id);
        return {
          content: [
            {
              type: "text",
              text: result.ok ? `Preset ${preset_id} was deleted.` : `Delete preset returned: ${JSON.stringify(result)}`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to delete preset: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "get_settings",
    "Retrieve current global studio settings (default parameters across all modes).",
    {},
    async () => {
      try {
        const settings = await api.getSettings();
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(settings, null, 2),
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to get settings: ${message}` }],
        };
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
    async ({ key, value }) => {
      try {
        const result = await api.updateSetting(key, value);
        return {
          content: [
            {
              type: "text",
              text: result.ok ? `Setting '${key}' updated successfully.` : `Update returned: ${JSON.stringify(result)}`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to update setting: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "get_system_status",
    "Check the studio host: colab-studio service, data disk usage, RAM, and Colab CLI sessions.",
    {},
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

        return {
          content: [{ type: "text", text: summary }],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to retrieve system status: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "restart_colab_studio",
    "Restart the colab-studio systemd user service (only when installed with install.sh --service).",
    {},
    async () => {
      try {
        const result = await api.restartService();
        return {
          content: [{ type: "text", text: result }],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to restart service: ${message}` }],
        };
      }
    }
  );
}
