import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import * as api from "./api.js";

export function registerResources(server: McpServer): void {
  server.resource(
    "runtime_status",
    "colab://runtime/status",
    {
      description: "Live state, phase, credits balance, and active queue of the Colab L4 GPU runtime",
      mimeType: "application/json",
    },
    async (uri) => {
      const status = await api.getRuntimeStatus();
      return {
        contents: [
          {
            uri: uri.href,
            mimeType: "application/json",
            text: JSON.stringify(status),
          },
        ],
      };
    }
  );

  server.resource(
    "studio_settings",
    "colab://settings",
    {
      description: "Global configuration settings across all generation modes (image, video, speech, etc.)",
      mimeType: "application/json",
    },
    async (uri) => {
      const settings = await api.getSettings();
      return {
        contents: [
          {
            uri: uri.href,
            mimeType: "application/json",
            text: JSON.stringify(settings),
          },
        ],
      };
    }
  );

  server.resource(
    "system_status",
    "colab://system/status",
    {
      description: "Studio host health, service status, disk and memory utilization, and active Colab CLI sessions",
      mimeType: "application/json",
    },
    async (uri) => {
      const system = await api.getSystemStatus();
      return {
        contents: [
          {
            uri: uri.href,
            mimeType: "application/json",
            text: JSON.stringify(system),
          },
        ],
      };
    }
  );

  server.resource(
    "recent_media",
    "colab://media/recent",
    {
      description: "Inventory of the 20 most recently generated media files (with URLs and timestamps)",
      mimeType: "application/json",
    },
    async (uri) => {
      const media = await api.listMediaFiles(20);
      return {
        contents: [
          {
            uri: uri.href,
            mimeType: "application/json",
            text: JSON.stringify(media),
          },
        ],
      };
    }
  );
}
