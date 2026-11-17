import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import * as api from "../api.js";

/**
 * Paralel Colab oturumları (`docs/multi-session.md`).
 *
 * Her oturum kendi GPU çalışma zamanı, portu, model paketleri ve kuyruk yuvası olan ayrı bir Colab
 * VM'idir: iki video — ya da bir video + bir görsel + bir ses işi — aynı anda üretilebilir. İşler
 * otomatik yönlendirilir (paket hazır → model VRAM'de → en az yüklü); bu araçlar oturum açar/kapatır
 * ve kaç tanesine izin verildiğini ayarlar. Oturum açmak kredi harcar, bu yüzden hiçbir araç
 * kendiliğinden oturum açmaz.
 */

/** Sunucunun bildirdiği paketler (`server/runtime.py:PACKS`); "none" hiçbir paket hazırlamaz. */
const SESSION_PROFILES = ["image", "video", "music", "speech", "enhance", "model3d", "none"] as const;

/** Sunucu `null` profilini "paket hazırlama" diye kabul eder (bilinmeyen adı 400 ile reddeder). */
function profileValue(profile: (typeof SESSION_PROFILES)[number] | undefined): string | null {
  if (profile === undefined) {
    return "image"; // en hızlı açılış
  }
  return profile === "none" ? null : profile;
}

function phaseText(session: api.SessionSnapshot): string {
  const detail = session.detail ? ` — ${session.detail}` : "";
  return `${String(session.phase ?? "unknown").toUpperCase()}${detail}`;
}

function ageText(session: api.SessionSnapshot): string {
  const seconds = session.session_seconds;
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) {
    return "not running";
  }
  const spent =
    typeof session.session_spent === "number" && Number.isFinite(session.session_spent)
      ? `, ~${session.session_spent.toFixed(2)} units`
      : "";
  return `${Math.round(seconds / 60)} min${spent}`;
}

function sessionText(session: api.SessionSnapshot): string {
  const idle =
    typeof session.idle_seconds === "number"
      ? `, idle ${Math.round(session.idle_seconds / 60)}/${session.idle_minutes} min`
      : "";
  const auto = session.auto_shutdown ? "" : ", keep-warm";
  const packs = Object.values(session.packs ?? {})
    .map((pack) => {
      const entry = pack as { fraction?: number; label?: string } | null;
      return `${entry?.label ?? "?"} ${entry?.fraction === undefined ? "?" : Math.round((entry.fraction ?? 0) * 100)}%`;
    })
    .join(", ");
  const lines = [
    `• ${session.name} (slot ${session.slot}) — ${phaseText(session)}`,
    `  port ${session.port} · ${ageText(session)}${idle}${auto}`,
  ];
  if (packs) {
    lines.push(`  packs: ${packs}`);
  }
  return lines.join("\n");
}

function listText(list: api.SessionList): string {
  const header =
    `Sessions: ${list.limits.active}/${list.limits.plan} open ` +
    `(max ${list.limits.max}, default plan ${list.limits.default})`;
  const body = list.sessions.map(sessionText).join("\n");
  return body ? `${header}\n${body}` : `${header}\nNo sessions yet.`;
}

export function registerSessionTools(server: McpServer): void {
  server.tool(
    "colab_sessions",
    "List every Colab session (name, slot, phase, port, uptime, model packs) together with the concurrency limits. Read-only; use it before asking the user how many sessions to run.",
    {},
    { readOnlyHint: true },
    async () => {
      try {
        return { content: [{ type: "text", text: listText(await api.listSessions()) }] };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return { isError: true, content: [{ type: "text", text: `Failed to list sessions: ${message}` }] };
      }
    }
  );

  server.tool(
    "colab_sessions_start",
    "Open one or more additional Colab sessions (each is a separate GPU runtime and burns credits). Refused with 409 when the concurrency plan is full; raise it with colab_sessions_plan first. Jobs are routed to sessions automatically.",
    {
      count: z.number().int().min(1).max(5).default(1).describe("How many extra sessions to open"),
      profile: z
        .enum(SESSION_PROFILES)
        .optional()
        .describe("Model pack to prepare first (image = fastest start); 'none' prepares none"),
    },
    { destructiveHint: false, idempotentHint: false },
    async ({ count, profile }) => {
      try {
        const result = await api.startSessions({ count, profile: profileValue(profile) });
        const started = result.started.length > 0 ? `Started: ${result.started.join(", ")}` : "Nothing started";
        return { content: [{ type: "text", text: `${started}\n${listText(result)}` }] };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        const hint = /409|sınırı dolu/i.test(message)
          ? "\nThe concurrency plan is full: raise it with colab_sessions_plan or stop a session first."
          : "";
        return { isError: true, content: [{ type: "text", text: `Failed to start sessions: ${message}${hint}` }] };
      }
    }
  );

  server.tool(
    "colab_sessions_stop",
    "Stop one session or all of them (the default session stays unless 'all' is set). A running job on that session is interrupted; credits stop burning immediately.",
    {
      name: z.string().optional().describe("Session name to stop (e.g. 'studio-2'); omit to stop the first extra session"),
      all: z.boolean().default(false).describe("Stop every session, including the default one"),
    },
    { destructiveHint: true, idempotentHint: true },
    async ({ name, all }) => {
      try {
        const result = all ? await api.stopAllSessions() : await api.stopSession(name ?? "studio-2");
        return { content: [{ type: "text", text: `Stop requested.\n${listText(result)}` }] };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        const hint = /404|bulunamadı/i.test(message) ? "\nThat session does not exist; see colab_sessions." : "";
        return { isError: true, content: [{ type: "text", text: `Failed to stop session: ${message}${hint}` }] };
      }
    }
  );

  server.tool(
    "colab_sessions_plan",
    "Read or change the concurrency plan: how many Colab sessions may run at once (1…max) and whether sessions stay warm when idle. Ask the user once and store the answer here.",
    {
      plan: z.number().int().min(1).max(5).optional().describe("Allowed concurrent sessions (clamped to the server maximum)"),
      keep_warm: z.boolean().optional().describe("Keep sessions running when idle"),
    },
    { destructiveHint: false, idempotentHint: true },
    async ({ plan, keep_warm }) => {
      try {
        const current = await api.getSessionPlan();
        const wantsChange = plan !== undefined || keep_warm !== undefined;
        const result = wantsChange ? await api.setSessionPlan({ plan, keep_warm }) : current;
        const lines = [
          `Plan: ${result.plan} concurrent sessions (max ${result.max}, active ${result.active})`,
          `Keep-warm: ${result.keep_warm ? "on" : "off"}`,
          `Asked before: ${result.asked ? "yes" : "no"}`,
        ];
        return { content: [{ type: "text", text: lines.join("\n") }] };
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        return { isError: true, content: [{ type: "text", text: `Failed to read/update the session plan: ${message}` }] };
      }
    }
  );
}
