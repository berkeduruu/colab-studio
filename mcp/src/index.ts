import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import http from "node:http";
import { timingSafeEqual } from "node:crypto";
import { registerRuntimeTools } from "./tools/runtime.js";
import { registerGenerationTools } from "./tools/generation.js";
import { registerMediaTools } from "./tools/media.js";
import { registerStudioTools } from "./tools/studio.js";
import { registerResources } from "./resources.js";
import { registerPrompts } from "./prompts.js";

const SERVER_NAME = "colab-studio";
const SERVER_VERSION = "1.1.0";

/**
 * İstemcinin sistem istemine eklenen kısa kullanım kılavuzu: gereksiz durum sorgularını, `get_job`
 * döngülerini ve dosyaları base64 olarak taşımayı önler.
 */
const SERVER_INSTRUCTIONS = `Media generation on a shared Colab L4 GPU behind a single job queue.
- Call generate_* / enhance_prompt / transcribe_audio directly. A job starts the GPU by itself (cold start ~4-5 min) and downloads that job's models first. Do not call colab_runtime_status or colab_runtime_start beforehand; colab_runtime_start only pre-warms and always prepares the image models first.
- Submit with wait:false, then call wait_for_job once: it blocks without spending tokens and returns the outputs. Do not poll get_job in a loop. When timeout_seconds runs out the job keeps running (unless cancel_on_timeout:true); call wait_for_job again. If your client has a short tool timeout, pass a timeout_seconds below it.
- Outputs are media names (e.g. abc_0.png) with a public URL. Output names can be passed straight back as inputs (reference_images, image_file). To save a file locally, download its URL; use get_media_base64 only to look at a result (small JPEG preview; videos come back as a 2x2 frame sheet).
- Inputs: upload_media with source_url (the server downloads it) instead of base64 for anything but tiny files.
- Other clients may share the queue: a job can wait behind theirs. Look at colab_runtime_status only if a job seems stuck.`;

/**
 * Her bağlantı kendi sunucusunu alır: tek bir `McpServer`e ikinci kez `connect` çağrısı
 * "Already connected" fırlatır ve süreci öldürürdü.
 */
type RegisterFn = (server: McpServer) => void;

function createServerFactory(): () => McpServer {
  const registrars: RegisterFn[] = [
    registerRuntimeTools,
    registerGenerationTools,
    registerMediaTools,
    registerStudioTools,
    registerResources,
    registerPrompts,
  ];

  return function createServer(): McpServer {
    const server = new McpServer(
      { name: SERVER_NAME, version: SERVER_VERSION },
      {
        instructions: SERVER_INSTRUCTIONS,
        capabilities: {
          tools: {},
          resources: {},
          prompts: {},
          logging: {},
        },
      }
    );

    for (const register of registrars) {
      register(server);
    }
    return server;
  };
}

async function startStdio(): Promise<void> {
  process.stdout.setMaxListeners(100);
  process.stdin.setMaxListeners(100);
  const server = createServerFactory()();
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("[colab-studio-mcp] Server running on stdio");
}

interface SseSession {
  server: McpServer;
  transport: SSEServerTransport;
}

function safeEqual(given: string, expected: string): boolean {
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  if (a.length !== b.length) {
    return false;
  }
  return timingSafeEqual(a, b);
}

function bearerToken(req: http.IncomingMessage): string | null {
  const header = req.headers.authorization;
  if (typeof header !== "string") {
    return null;
  }
  const match = /^Bearer\s+(.+)$/i.exec(header.trim());
  return match ? match[1].trim() : null;
}

async function startSse(
  port: number,
  host: string = process.env.MCP_HOST?.trim() || "127.0.0.1"
): Promise<void> {
  const token = process.env.MCP_HTTP_TOKEN?.trim() || "";
  const allowedOrigins = (process.env.MCP_ALLOWED_ORIGINS || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);

  const createServer = createServerFactory();
  const sessions = new Map<string, SseSession>();

  function applyCors(req: http.IncomingMessage, res: http.ServerResponse): boolean {
    const origin = typeof req.headers.origin === "string" ? req.headers.origin : "";
    if (!origin) {
      return true; // tarayıcı dışı istemci
    }
    if (!allowedOrigins.includes(origin)) {
      return false;
    }
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
    return true;
  }

  function unauthorized(res: http.ServerResponse): void {
    res.writeHead(401, { "Content-Type": "application/json", "WWW-Authenticate": "Bearer" });
    res.end(JSON.stringify({ error: "Unauthorized" }));
  }

  const httpServer = http.createServer(async (req, res) => {
    try {
      const corsAllowed = applyCors(req, res);

      if (req.method === "OPTIONS") {
        res.writeHead(corsAllowed ? 204 : 403);
        res.end();
        return;
      }

      const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);

      if (req.method === "GET" && url.pathname === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok", name: SERVER_NAME, auth: token ? "bearer" : "none" }));
        return;
      }

      if (!corsAllowed) {
        res.writeHead(403, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Origin not allowed" }));
        return;
      }

      if (token && !safeEqual(bearerToken(req) ?? "", token)) {
        unauthorized(res);
        return;
      }

      if (req.method === "GET" && url.pathname === "/sse") {
        const server = createServer();
        const transport = new SSEServerTransport("/messages", res);
        const sessionId = transport.sessionId;
        sessions.set(sessionId, { server, transport });

        const cleanup = (): void => {
          sessions.delete(sessionId);
          void server.close().catch(() => undefined);
        };
        transport.onclose = cleanup;
        res.on("close", () => sessions.delete(sessionId));

        await server.connect(transport);
        console.error(`[colab-studio-mcp] SSE session ${sessionId} connected (${sessions.size} active)`);
        return;
      }

      if (req.method === "POST" && url.pathname === "/messages") {
        const sessionId = url.searchParams.get("sessionId");
        const session = sessionId ? sessions.get(sessionId) : undefined;

        if (!session) {
          res.writeHead(404, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: sessionId ? "Unknown SSE session" : "Missing sessionId" }));
          return;
        }

        await session.transport.handlePostMessage(req, res);
        return;
      }

      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "Not found" }));
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`[colab-studio-mcp] HTTP handler error: ${message}`);
      if (!res.headersSent) {
        res.writeHead(500, { "Content-Type": "application/json" });
      }
      if (!res.writableEnded) {
        res.end(JSON.stringify({ error: message }));
      }
    }
  });

  httpServer.on("clientError", (error: Error, socket) => {
    console.error(`[colab-studio-mcp] client error: ${error.message}`);
    socket.destroy();
  });

  const { promise, resolve } = Promise.withResolvers<void>();
  httpServer.listen(port, host, () => {
    const authNote = token ? "bearer token required" : "NO AUTH (set MCP_HTTP_TOKEN to protect)";
    console.error(`[colab-studio-mcp] SSE server listening on http://${host}:${port}/sse — ${authNote}`);
    resolve();
  });
  httpServer.on("error", (error: Error) => {
    console.error(`[colab-studio-mcp] SSE server error: ${error.message}`);
    process.exitCode = 1;
  });
  await promise;
}

async function main(): Promise<void> {
  const isSse =
    process.argv.includes("--sse") ||
    process.argv.includes("--http") ||
    process.env.MCP_TRANSPORT === "sse";
  const portArgIdx = process.argv.findIndex((arg) => arg === "--port" || arg === "-p");
  const port = portArgIdx !== -1 && process.argv[portArgIdx + 1]
    ? parseInt(process.argv[portArgIdx + 1], 10)
    : parseInt(process.env.MCP_PORT || "8195", 10);
  const hostArgIdx = process.argv.findIndex((arg) => arg === "--host");
  const host = hostArgIdx !== -1 && process.argv[hostArgIdx + 1]
    ? process.argv[hostArgIdx + 1]
    : process.env.MCP_HOST?.trim() || "127.0.0.1";

  if (isSse) {
    await startSse(port, host);
  } else {
    await startStdio();
  }
}

main().catch((err) => {
  console.error("[colab-studio-mcp] Fatal error:", err);
  process.exit(1);
});
