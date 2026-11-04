import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import path from "node:path";
import * as api from "../api.js";

const SUPPORTED_UPLOAD_EXTENSIONS = Object.keys(api.UPLOAD_MIME_TYPES).join(", ");

export function registerMediaTools(server: McpServer): void {
  server.tool(
    "list_media",
    "List recent media files (generated outputs and uploads) in the Colab Studio media library with file sizes, public URLs and their origin (output vs upload).",
    {
      limit: z.number().int().min(1).max(100).default(30).describe("Maximum number of files to return"),
      extension: z.string().optional().describe("Filter by extension (e.g. 'png', 'mp4', 'glb', 'mp3')"),
    },
    async ({ limit, extension }) => {
      try {
        const files = await api.listMediaFiles(limit, extension);
        if (files.length === 0) {
          return {
            content: [{ type: "text", text: "No media files found matching the criteria." }],
          };
        }

        const lines = [
          `Found ${files.length} media files (showing newest first):`,
          ...files.map((f) => {
            const sizeKb = (f.sizeBytes / 1024).toFixed(1);
            const kind = f.isOutput ? "output" : "upload";
            return `• ${f.filename} (${kind}, ${sizeKb} KB, ${f.modifiedAt})\n  URL: ${f.publicUrl}`;
          }),
        ];

        return {
          content: [{ type: "text", text: lines.join("\n") }],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to list media: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "get_media_base64",
    "Fetch a media file from the server. If it is an image, it is returned as an embedded visual image block and base64 so Claude can inspect the output directly.",
    {
      filename: z.string().min(1).describe("Filename or relative path in media directory (e.g. '0d1012faa87f4fec9fc9831bbb5200d5_0.png' or 'uploads/foo.jpg')"),
      max_size_kb: z.number().int().min(64).max(16384).default(4096).describe("Maximum allowed file size in KB (default 4096 KB)"),
    },
    async ({ filename, max_size_kb }) => {
      try {
        const fileData = await api.readMediaBase64(filename, max_size_kb);
        const isImage = fileData.mimeType.startsWith("image/");
        const publicUrl = api.mediaPublicUrl(fileData.filename);

        if (isImage) {
          return {
            content: [
              {
                type: "image",
                data: fileData.base64,
                mimeType: fileData.mimeType,
              },
              {
                type: "text",
                text: `Image file: ${fileData.filename} (${Math.round(fileData.sizeBytes / 1024)} KB, ${fileData.mimeType})\nPublic URL: ${publicUrl}`,
              },
            ],
          };
        }

        return {
          content: [
            {
              type: "text",
              text: `File: ${fileData.filename} (${Math.round(fileData.sizeBytes / 1024)} KB, ${fileData.mimeType})\nPublic URL: ${publicUrl}\n\nBase64 Payload (first 1000 chars):\n${fileData.base64.slice(0, 1000)}...`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to read media file: ${message}` }],
        };
      }
    }
  );

  server.tool(
    "upload_media",
    "Upload an image or audio file into the Colab Studio media library from base64 data (POST /api/uploads). Returns the upload name (e.g. 'uploads/xyz.png') which can be passed to generation tools (3D, video, speech, image edit).",
    {
      base64_data: z.string().min(1).describe("Base64 encoded file content"),
      filename: z.string().min(1).describe("Original filename including extension (e.g. 'portrait.png' or 'sample.wav')"),
    },
    async ({ base64_data, filename }) => {
      try {
        const cleanBase64 = base64_data.replace(/^data:[^;]+;base64,/, "").replace(/\s+/g, "");
        if (!cleanBase64) {
          throw new Error("base64_data is empty after removing the data URL prefix");
        }
        const buffer = Buffer.from(cleanBase64, "base64");
        if (buffer.length === 0) {
          throw new Error("Decoded file is empty; check that base64_data is valid");
        }

        const ext = path.extname(filename).toLowerCase();
        if (!(ext in api.UPLOAD_MIME_TYPES)) {
          throw new Error(`Unsupported file extension: ${ext || "(none)"}. Supported: ${SUPPORTED_UPLOAD_EXTENSIONS}`);
        }

        const saved = await api.uploadMedia(buffer, filename);

        return {
          content: [
            {
              type: "text",
              text: `File uploaded successfully!\nUpload Path: "${saved.uploadPath}"\nPublic URL: ${saved.publicUrl}\nBytes: ${saved.sizeBytes}\n\nYou can now pass "${saved.uploadPath}" into reference_images, image_file, reference_audio, or audio_file parameters!`,
            },
          ],
        };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to upload media: ${message}` }],
        };
      }
    }
  );
}
