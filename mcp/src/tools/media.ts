import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import path from "node:path";
import * as api from "../api.js";

const SUPPORTED_UPLOAD_EXTENSIONS = Object.keys(api.UPLOAD_MIME_TYPES).join(", ");

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function registerMediaTools(server: McpServer): void {
  server.tool(
    "list_media",
    "List recent media files (generated outputs and uploads) in the Colab Studio media library with file sizes, public URLs and their origin (output vs upload).",
    {
      limit: z.number().int().min(1).max(100).default(30).describe("Maximum number of files to return"),
      extension: z.string().optional().describe("Filter by extension (e.g. 'png', 'mp4', 'glb', 'mp3')"),
    },
    { readOnlyHint: true },
    async ({ limit, extension }) => {
      try {
        const files = await api.listMediaFiles(limit, extension);
        if (files.length === 0) {
          return {
            content: [{ type: "text", text: "No media files found matching the criteria." }],
          };
        }

        const lines = [
          `${files.length} media files, newest first. URL = ${api.mediaPublicUrl("")}<name>`,
          ...files.map((f) => {
            const sizeKb = (f.sizeBytes / 1024).toFixed(0);
            const kind = f.isOutput ? "output" : "upload";
            return `${f.filename} | ${kind} | ${sizeKb} KB | ${f.modifiedAt.slice(0, 16).replace("T", " ")}`;
          }),
        ];

        return {
          content: [{ type: "text", text: lines.join("\n") }],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to list media: ${errorText(error)}` }],
        };
      }
    }
  );

  server.tool(
    "get_media_base64",
    "Look at a media file: images return as a downscaled JPEG preview, videos as a 2x2 frame sheet. To save a file, download its URL instead.",
    {
      filename: z.string().min(1).describe("Filename or relative path in media directory (e.g. '0d1012faa87f4fec9fc9831bbb5200d5_0.png' or 'uploads/foo.jpg')"),
      max_dim: z.number().int().min(0).max(4096).default(1024).describe("Preview longest edge in px; 0 = original image"),
      max_size_kb: z.number().int().min(64).max(16384).default(4096).describe("Max returned size in KB"),
    },
    { readOnlyHint: true },
    async ({ filename, max_dim, max_size_kb }) => {
      try {
        if (max_dim > 0) {
          const preview = await api.readMediaPreview(filename, max_dim);
          if (preview) {
            if (preview.sizeBytes > max_size_kb * 1024) {
              throw new Error(`Preview is ${Math.round(preview.sizeBytes / 1024)} KB, above max_size_kb=${max_size_kb}; use a smaller max_dim`);
            }
            const what = preview.kind === "video" ? "Video frames (2x2, start → end)" : "Image preview";
            return {
              content: [
                { type: "image", data: preview.base64, mimeType: preview.mimeType },
                {
                  type: "text",
                  text: `${what}: ${preview.filename} (original ${Math.round(preview.originalBytes / 1024)} KB, preview ${Math.round(preview.sizeBytes / 1024)} KB)\nURL: ${api.mediaPublicUrl(preview.filename)}`,
                },
              ],
            };
          }
        }

        const fileData = await api.readMediaBase64(filename, max_size_kb);
        const publicUrl = api.mediaPublicUrl(fileData.filename);
        if (fileData.mimeType.startsWith("image/")) {
          return {
            content: [
              { type: "image", data: fileData.base64, mimeType: fileData.mimeType },
              {
                type: "text",
                text: `Image file: ${fileData.filename} (${Math.round(fileData.sizeBytes / 1024)} KB, ${fileData.mimeType})\nURL: ${publicUrl}`,
              },
            ],
          };
        }

        // Görsel olmayan dosyanın base64'ü modele bir şey göstermez: yalnızca künye ve adres döner.
        return {
          content: [
            {
              type: "text",
              text: `File: ${fileData.filename} (${Math.round(fileData.sizeBytes / 1024)} KB, ${fileData.mimeType}) cannot be shown inline.\nURL: ${publicUrl} (download it to use the file)`,
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to read media file: ${errorText(error)}` }],
        };
      }
    }
  );

  server.tool(
    "upload_media",
    "Upload an image or audio file to the media library. Prefer source_url (the server downloads it); base64_data only for small files. Returns an 'uploads/...' name for reference_images, image_file, reference_audio or audio_file.",
    {
      source_url: z.string().url().optional().describe("http(s) URL the server downloads the file from (max 50 MB)"),
      base64_data: z.string().min(1).optional().describe("Base64 file content (small files only)"),
      filename: z.string().min(1).optional().describe("Filename with extension; required with base64_data"),
    },
    { destructiveHint: false },
    async ({ source_url, base64_data, filename }) => {
      try {
        if (!!source_url === !!base64_data) {
          throw new Error("Give exactly one of source_url or base64_data");
        }

        let buffer: Buffer;
        let name: string;
        if (source_url) {
          const downloaded = await api.downloadForUpload(source_url, filename);
          buffer = downloaded.buffer;
          name = downloaded.filename;
        } else {
          if (!filename) {
            throw new Error("filename is required with base64_data");
          }
          const cleanBase64 = (base64_data as string).replace(/^data:[^;]+;base64,/, "").replace(/\s+/g, "");
          if (!cleanBase64) {
            throw new Error("base64_data is empty after removing the data URL prefix");
          }
          buffer = Buffer.from(cleanBase64, "base64");
          name = filename;
        }
        if (buffer.length === 0) {
          throw new Error("Decoded file is empty; check the input");
        }

        const ext = path.extname(name).toLowerCase();
        if (!(ext in api.UPLOAD_MIME_TYPES)) {
          throw new Error(`Unsupported file extension: ${ext || "(none)"}. Supported: ${SUPPORTED_UPLOAD_EXTENSIONS}`);
        }

        const saved = await api.uploadMedia(buffer, name);

        return {
          content: [
            {
              type: "text",
              text: `Uploaded: "${saved.uploadPath}" (${Math.round(saved.sizeBytes / 1024)} KB)\nURL: ${saved.publicUrl}\nPass "${saved.uploadPath}" to reference_images, image_file, reference_audio or audio_file.`,
            },
          ],
        };
      } catch (error: unknown) {
        return {
          isError: true,
          content: [{ type: "text", text: `Failed to upload media: ${errorText(error)}` }],
        };
      }
    }
  );
}
