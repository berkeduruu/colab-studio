"""Session routing: which session runs a job?

Order: the user's choice → a session whose model packs are already on disk → a warm session (the
model is already in VRAM) → the least loaded → the lowest slot (the default session fills first).

A new session is never opened implicitly: every running session burns compute units, so opening one
is the user's decision.
"""
import runtime


def usable(manager):
    """Sessions that can take a job: GPU ready or preparing."""
    return [rt for rt in manager.live.values() if rt.state["phase"] in ("ready", "starting")]


def score(rt, packs=(), kind=None, load=None):
    """Lower is better: (packs ready, model warm, waiting jobs, slot)."""
    packs_ready = all(p in rt.local_modes for p in packs)
    warm = bool(kind) and kind in rt.warm
    waiting = load(rt.name) if load else 0
    return (0 if packs_ready else 1, 0 if warm else 1, waiting, rt.slot)


def choose(manager, packs=(), kind=None, load=None, assigned=None):
    """Name of the session a job runs on. `assigned` (the job's own column) always wins."""
    if assigned:
        return assigned
    live = usable(manager)
    if not live:
        return runtime.SESSION
    return min(live, key=lambda rt: score(rt, packs, kind, load)).name


def explain(manager, packs=(), kind=None, load=None):
    """Why that session? Shown in the UI/MCP next to "automatic"."""
    live = usable(manager)
    if not live:
        return {"session": runtime.SESSION, "reason": "no open session, using the default session"}
    best = min(live, key=lambda rt: score(rt, packs, kind, load))
    reasons = []
    if packs and all(p in best.local_modes for p in packs):
        reasons.append("model packs ready")
    if kind and kind in best.warm:
        reasons.append("model already in VRAM")
    reasons.append(f"{load(best.name) if load else 0} waiting jobs")
    return {"session": best.name, "reason": ", ".join(reasons)}
