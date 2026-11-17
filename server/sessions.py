"""Multiple Colab sessions: slot allocation, ports, SSH configs and the live registry.

Slot 0 is the studio's default session (`STUDIO_SESSION`, port 8188, `ssh/config`) and never appears
in the registry: it exists whether or not extra sessions are asked for. Extra slots are named
`<session>-2` … `<session>-5`, get ports from `STUDIO_SESSIONS_PORT_BASE` (8200, 8210, …) and their
own SSH config (`ssh/config.<slot>`) plus data directory (`sessions/<slot>/`).

The assignment lives in `STUDIO_DATA/sessions.json`, guarded by `flock`: ports stay stable across
restarts and two concurrent starts cannot overwrite each other's slot (a lost update would leave an
unowned runtime burning compute units).

Every session's SSH config has its own `Host` name (`colab`, `colab-2`, …) and its own
`ControlPath …/%C`: the `%C` hash is computed from the config contents, so two sessions can never
share one master connection (a shared one silently runs commands on the wrong VM).
"""
import asyncio
import errno
import fcntl
import json
import os
import socket
import time
from contextlib import contextmanager
from pathlib import Path

import config
import runtime

MAX_SESSIONS = config.SESSIONS_MAX
DEFAULT_SESSIONS = config.SESSIONS_DEFAULT
PORT_BASE = config.SESSIONS_PORT_BASE
PORT_STRIDE = config.SESSIONS_PORT_STRIDE


class PortBusy(RuntimeError):
    """The slot's local port is already taken by another process."""


def session_name(slot):
    """Slot → Colab session name. Slot 0 keeps the configured name (`studio`)."""
    return config.SESSION if slot == 0 else f"{config.SESSION}-{slot + 1}"


def slot_port(slot):
    """Slot → local ComfyUI port. Slot 0 keeps 8188 (everything else assumes it)."""
    return runtime.COMFY_PORT if slot == 0 else PORT_BASE + PORT_STRIDE * (slot - 1)


def ssh_conf_path(slot):
    """Slot → SSH config file. Slot 0 keeps `ssh/config`."""
    return runtime.SSH_CONF if slot == 0 else runtime.SSH_DIR / f"config.{slot}"


def host_alias(slot):
    return "colab" if slot == 0 else f"colab-{slot}"


def data_dir(slot, root=None):
    """Slot → the session's own data directory (learned timings, owned endpoints)."""
    root = Path(runtime.DATA_DIR if root is None else root)
    return root if slot == 0 else root / "sessions" / str(slot)


def port_is_free(port, host="127.0.0.1"):
    """True when nothing is listening on the port (checked by binding it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as e:
            if e.errno in (errno.EADDRINUSE, errno.EACCES):
                return False
            raise
    return True


class Registry:
    """Which slots are in use, persisted in `sessions.json` under `flock`."""

    def __init__(self, path=None, max_sessions=None, check_ports=True):
        self.path = Path(path) if path is not None else Path(config.SESSIONS_FILE)
        self.lock_path = self.path.with_suffix(".lock")
        self.max_sessions = MAX_SESSIONS if max_sessions is None else max_sessions
        self.check_ports = check_ports

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self):
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {int(k): v for k, v in data.items() if str(k).isdigit()}

    def _write(self, entries):
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({str(k): v for k, v in sorted(entries.items())}, indent=1))
        os.replace(tmp, self.path)

    def entries(self):
        with self._locked():
            return self._read()

    def slots(self):
        with self._locked():
            return [0, *sorted(self._read())]

    def active_count(self):
        with self._locked():
            return len(self._read())

    def _free_slot(self, entries):
        limit = max(1, min(self.max_sessions, MAX_SESSIONS))
        for slot in range(1, limit + 1):
            if slot not in entries:
                return slot
        return None

    def free_slot(self):
        with self._locked():
            return self._free_slot(self._read())

    def reserve(self, slot=None):
        """Claim a slot (writing it first) and make sure its port is really free."""
        with self._locked():
            entries = self._read()
            if slot is None:
                slot = self._free_slot(entries)
            if slot is None:
                raise PortBusy(f"concurrent session limit reached ({self.max_sessions})")
            if slot == 0:
                return {"slot": 0, "name": session_name(0), "port": slot_port(0),
                        "ssh_conf": str(ssh_conf_path(0)), "host_alias": host_alias(0),
                        "created_at": time.time()}
            port = slot_port(slot)
            if self.check_ports and not port_is_free(port):
                raise PortBusy(f"slot {slot}: port {port} is in use")
            entry = entries.get(slot) or {
                "name": session_name(slot), "port": port, "ssh_conf": str(ssh_conf_path(slot)),
                "host_alias": host_alias(slot), "created_at": time.time(),
            }
            entries[slot] = entry
            self._write(entries)
            return {"slot": slot, **entry}

    def release(self, slot):
        with self._locked():
            entries = self._read()
            if slot in entries:
                del entries[slot]
                self._write(entries)

    def entry(self, slot):
        if slot == 0:
            return {"slot": 0, "name": session_name(0), "port": slot_port(0),
                    "ssh_conf": str(ssh_conf_path(0)), "host_alias": host_alias(0)}
        with self._locked():
            found = self._read().get(slot)
        return {"slot": slot, **found} if found else None


PLAN_KEY = "sessions.plan"  # studio.settings key
ASKED_KEY = "sessions.asked_at"
KEEP_WARM_KEY = "sessions.keep_warm"


def clamp_plan(value, default=None, maximum=None):
    """Clamp a plan to 1..hard limit (the UI may send "3", 0 or 99)."""
    default = DEFAULT_SESSIONS if default is None else default
    maximum = MAX_SESSIONS if maximum is None else maximum
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, maximum))


def plan_from(settings, default=None, maximum=None):
    raw = (settings or {}).get(PLAN_KEY)
    return clamp_plan(raw if raw is not None else (DEFAULT_SESSIONS if default is None else default),
                      default=default, maximum=maximum)


def limits(active, plan=None):
    """Summary for the UI/MCP: ceiling, default plan, chosen plan, open sessions."""
    return {"max": MAX_SESSIONS, "default": DEFAULT_SESSIONS,
            "plan": clamp_plan(plan if plan is not None else DEFAULT_SESSIONS), "active": int(active)}


def unaccounted(active_assignments, open_names):
    """Assignments on the Colab account that the studio does not know about (leaked runtimes)."""
    if active_assignments is None:
        return None
    return max(0, int(active_assignments) - len(set(open_names)))


class Manager:
    """Open sessions: the on-disk registry plus the live `runtime.SessionRuntime` objects.

    Slot 0 is always present and *is* `runtime.DEFAULT`, so single-session behaviour, ports and file
    names stay exactly as they were."""

    def __init__(self, registry=None, max_sessions=None):
        self.registry = registry if registry is not None else Registry(max_sessions=max_sessions)
        self.live = {runtime.SESSION: runtime.DEFAULT}
        self.tasks = {}

    def get(self, name):
        return self.live.get(name)

    def names(self):
        return list(self.live)

    def open_names(self):
        return [name for name, rt in self.live.items() if rt.state["phase"] != "off"]

    def active_count(self):
        return len(self.open_names())

    def limits(self, plan=None):
        return limits(self.active_count(), plan)

    def snapshot(self):
        out = []
        for name, rt in self.live.items():
            snap = rt.snapshot()
            snap.update({"name": name, "slot": rt.slot, "port": rt.comfy_port})
            out.append(snap)
        return sorted(out, key=lambda s: s["slot"])

    def ensure(self, slot=None):
        entry = self.registry.reserve(slot)
        name = entry["name"]
        if name in self.live:
            return self.live[name]
        rt = runtime.SessionRuntime(slot=entry["slot"], name=name, host_alias=entry["host_alias"],
                                    data_dir=data_dir(entry["slot"]), comfy_port=entry["port"],
                                    ssh_conf=entry["ssh_conf"])
        self.live[name] = rt
        return rt

    def start(self, slot=None, pack="image", keep_warm=None, packs=None):
        rt = self.ensure(slot)
        if rt.slot != 0:
            rt.stop_hook = lambda reason, name=rt.name: self.stop(name, reason)
        if keep_warm is not None:
            rt.state["auto_shutdown"] = not keep_warm
        rt.start(pack, packs=packs)
        return rt

    def watch(self, rt, has_pending, on_credits):
        """Start the session's own watchdog and metrics loops (idle shutdown, credits, GPU stats)."""
        self.unwatch(rt)
        self.tasks[rt.name] = [
            asyncio.create_task(runtime.watchdog(has_pending, on_credits, rt)),
            asyncio.create_task(runtime.metrics_loop(rt)),
        ]

    def unwatch(self, rt):
        for task in self.tasks.pop(rt.name, []):
            task.cancel()

    async def stop(self, name, reason="closed by hand"):
        rt = self.live.get(name)
        if rt is None:
            return None
        self.unwatch(rt)
        await rt.stop(reason)
        rt.stop_hook = None
        if rt.slot != 0:
            self.registry.release(rt.slot)
            self.live.pop(name, None)
        return rt

    async def stop_all(self, reason="all closed"):
        stopped, errors = [], []
        for name in sorted(self.live, key=lambda n: self.live[n].slot, reverse=True):
            try:
                await self.stop(name, reason)
                stopped.append(name)
            except RuntimeError as e:
                errors.append(f"{name}: {e}")
        return {"stopped": stopped, "errors": errors}


_manager = None


def manager():
    """Process-wide manager: the API, the job dispatcher and the watchdogs share one registry."""
    global _manager
    if _manager is None:
        _manager = Manager()
    return _manager


def reset():
    global _manager
    _manager = None
