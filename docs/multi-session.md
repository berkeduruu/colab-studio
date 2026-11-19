# Multiple Colab sessions

The studio normally runs **one** Colab L4 runtime. This feature lets it run up to
`STUDIO_SESSIONS_MAX` (5) at the same time, so two videos — or a video plus an image plus a speech
job — can render concurrently. Each session is a separate GPU runtime with its own ports, model
packs and queue slot; the job router decides where a job lands.

## Limits

| | Value |
|---|---|
| Hard ceiling | `STUDIO_SESSIONS_MAX` (default **5**, the default session included: at most 4 extra slots) |
| Default plan | `STUDIO_SESSIONS_DEFAULT` (default **3**) |
| Jobs per session | 1 (one ComfyUI processes one graph efficiently) |
| Opening a session | never implicit — it costs compute units |
| Rollback | `STUDIO_SESSIONS_MAX=1` |

The plan is chosen by the user once (`POST /api/sessions/plan`) and stored in the settings table;
the UI asks the first time it runs (`asked: false`).

## Slots, names, ports

| Slot | Session name | Local port | SSH config | Data |
|---|---|---|---|---|
| 0 | `STUDIO_SESSION` (`studio`) | 8188 | `DATA/ssh/config` | `DATA/` |
| 1 | `<session>-2` | 8200 | `DATA/ssh/config.1` | `DATA/sessions/1/` |
| 2 | `<session>-3` | 8210 | `DATA/ssh/config.2` | `DATA/sessions/2/` |
| n | `<session>-<n+1>` | `8200+10·(n-1)` | `DATA/ssh/config.n` | `DATA/sessions/n/` |

Slot 0 is frozen: existing installs, docs and `test_*` assume port 8188 and `ssh/config`.

Each slot's SSH config declares its own `Host` (`colab`, `colab-2`, …) and its own
`ControlPath …/%C`. `%C` is a hash of the config contents, so two sessions can never share a master
connection — a shared one silently runs commands on the wrong VM.

Assignments live in `DATA/sessions.json`, guarded by `flock`; ports are checked by binding them
before an assignment is written.

## Routing

`server/router.py`, evaluated when the dispatcher takes the job, before it waits for its session; the
choice is written to `jobs.session` right away, so jobs waiting for a session count as its load:

1. `jobs.session` — the user pinned a session, or a previous routing decision.
2. Session with the job's model packs already downloaded.
3. Least waiting jobs.
4. Session whose model is already in VRAM (`warm`).
5. Lowest slot.

No open session → the default session. A new session is never opened automatically.

## HTTP API

`GET /api/sessions` → `{"sessions": [...], "limits": {...}}`

```json
{"sessions": [{"name": "studio", "slot": 0, "phase": "ready", "detail": null,
               "port": 8188, "auto_shutdown": true, "idle_seconds": 30, "idle_minutes": 15,
               "session_seconds": 120, "session_spent": 0.4, "packs": {}, "startup": null}],
 "limits": {"max": 5, "default": 3, "plan": 3, "active": 1}}
```

- `POST /api/sessions` `{"count": 1, "profile": "image"|null, "packs": ["image", ...]?}` → starts
  `count` sessions (respecting the plan) and returns `{"sessions": ..., "limits": ..., "started": ["studio-2"]}`.
  409 when the plan is full, 400 for an unknown pack.
- `POST /api/sessions/{name}/stop` → 404 when unknown, cancels that session's waiting jobs and stops it in
  the background. A session that cannot be released stays listed (phase `error`) so it can be closed again.
- `POST /api/sessions/stop-all` → stops every extra session, then the default one.
- `GET /api/sessions/plan` → `{"max", "default", "plan", "active", "asked", "keep_warm"}`
- `PUT /api/sessions/plan` `{"plan": 2, "keep_warm": false}` → clamped to 1..max, stamps `asked_at`.

`GET /api/runtime` keeps its exact old shape and **adds** `sessions` + `limits`. `POST /api/runtime/start`
answers 409 when the extra sessions already fill the plan.

Jobs: `POST /api/jobs` accepts `"session": "studio-2" | "auto" | null` and `"parallel": N`.
`parallel > 1` fans the job out over N sessions, returns `{"jobs": [...], "group_id": "..."}`; a
single job keeps the old response shape (one job object). `jobs` rows carry `session` and `group_id`.

## Shutdown and leftovers

- Idle shutdown (`STUDIO_IDLE_MINUTES`) applies to every session; an extra session that idles out is
  stopped and its slot released.
- Stopping the service stops every extra session too (in-app, so Docker and `run.sh` are covered),
  then the default one. systemd additionally runs `deploy/colab-stop-all.sh` as `ExecStopPost`; it reads
  `STUDIO_DATA`, `COLAB_BIN` and `STUDIO_SESSION` through `server/config.py`, i.e. from `.env`.
- At startup the studio releases any extra session still listed in `sessions.json` from a previous
  run (crash, `docker compose down`, cut-short shutdown); one that cannot be released stays listed
  and is retried at the next start.
- A job whose session was closed fails with "Oturum kapatıldı" instead of reopening it or moving to the
  default session.

## SessionRuntime (server/runtime.py)

`SessionRuntime(slot, name, host_alias, data_dir, comfy_port, ssh_conf)` exposes the per-session
state that used to be module-level, so the rest of the code reads `rt.state["phase"]`,
`rt.ready`, `rt.warm`, `rt.local_modes`, `rt.packs`, `rt.comfy_url`, `rt.ssh` / `rt.remote`,
`rt.data_dir` and calls `rt.start(pack, packs=None)`, `rt.stop(reason)`, `rt.ensure_models(pack,
on_wait)`, `rt.snapshot()`, `rt.startup_progress()`, `rt.touch()`.

Slot 0 delegates every field to the module globals (`state`, `ready`, `warm`, …, `COMFY_URL`), so
existing callers, tests and `monkeypatch.setattr(runtime, "…")` keep working. `runtime.DEFAULT` is
the slot 0 instance; the module-level functions take `session=None` and resolve it with `_sel()`.

## MCP tools

`colab_sessions`, `colab_sessions_start`, `colab_sessions_stop`, `colab_sessions_plan`;
`generate_image` and `generate_video` accept `session` and `parallel`.

## Tests

```bash
cd server && python -m pytest -q          # backend
cd server/web && npm ci && npm run build && npx playwright test
cd mcp && npm run build && bash test-mcp.sh
./lint.sh
```
