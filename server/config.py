"""Studio settings.

Read from environment variables; a `.env` file in the repository root is loaded first (values that
are already set in the environment win). See `.env.example` for every option.
"""
import os
import shutil
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path):
    """Minimal KEY=VALUE parser: blank lines and # comments are skipped, surrounding quotes removed."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_dotenv(Path(os.environ.get("STUDIO_ENV_FILE", REPO_DIR / ".env")))

# Address the web UI listens on. 127.0.0.1: only this machine. 0.0.0.0: reachable by IP from the
# network (there is no password then; put nginx + basic auth in front, see deploy/setup-nginx.sh).
def _int_setting(name, default, low, high, what):
    """Integer setting; a typo stops the studio with one readable line instead of a traceback."""
    raw = os.environ.get(name, default).strip()
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not low <= value <= high:
        sys.exit(f"Colab Studio: {name}={raw!r} is not valid: {what}. Fix it in .env and start the studio again.")
    return value


HOST = os.environ.get("STUDIO_HOST", "127.0.0.1").strip()
PORT = _int_setting("STUDIO_PORT", "8190", 1, 65535, "it must be a port number from 1 to 65535")
# Host names the UI may be opened by, besides IP addresses and localhost (comma-separated, e.g. the
# nginx domain). Requests for any other name are refused: this blocks DNS rebinding, where a web page
# points its own domain at 127.0.0.1 to reach the studio. "*" turns the check off.
# Quotes around single items ("a.example", b.example) are tolerated as well as around the whole value.
ALLOWED_HOSTS = {
    h.strip().strip("'\"").strip().lower().rstrip(".")
    for h in os.environ.get("STUDIO_ALLOWED_HOSTS", "").split(",")
    if h.strip().strip("'\"").strip()
}
# Database, generated media, SSH key and learned timings.
DATA_DIR = Path(os.environ.get("STUDIO_DATA", "~/.local/share/colab-studio")).expanduser()
# Chats (and their media) not used for this many days are deleted automatically. 0 keeps everything.
RETENTION_DAYS = _int_setting("STUDIO_RETENTION_DAYS", "30", 0, 36500, "it must be a number of days from 1 to 36500, or 0 to keep everything")
# The GPU runtime is released after this many idle minutes (it costs Colab compute units).
IDLE_MINUTES = _int_setting("STUDIO_IDLE_MINUTES", "15", 1, 24 * 60, "it must be a number of minutes from 1 to 1440")
# Only L4 is supported: all workflows, VRAM budgets and time estimates were measured on it.
GPU = "L4"
# Colab session name the studio owns; other sessions on the same account are never touched.
SESSION = os.environ.get("STUDIO_SESSION", "studio")
# How many Colab sessions may run at once. SESSIONS_MAX is the hard ceiling; SESSIONS_DEFAULT is the
# plan the UI offers the first time (the user picks 1..MAX and the answer is stored in the database).
SESSIONS_MAX = _int_setting("STUDIO_SESSIONS_MAX", "5", 1, 5, "it must be a number of sessions from 1 to 5")
SESSIONS_DEFAULT = _int_setting(
    "STUDIO_SESSIONS_DEFAULT", "3", 1, SESSIONS_MAX,
    f"it must be a number of sessions from 1 to {SESSIONS_MAX} (STUDIO_SESSIONS_MAX)",
)
# Local ports for extra sessions: slot 1 uses 8200, slot 2 8210, ... (slot 0 keeps 8188).
SESSIONS_PORT_BASE = _int_setting("STUDIO_SESSIONS_PORT_BASE", "8200", 1024, 65500, "it must be a port number from 1024 to 65500")
SESSIONS_PORT_STRIDE = 10
# Which slots are taken and which ports they own (flock-protected; survives restarts).
SESSIONS_FILE = DATA_DIR / "sessions.json"
# Google Colab CLI (`uv tool install google-colab-cli`).
COLAB = os.environ.get("COLAB_BIN") or shutil.which("colab") or str(Path("~/.local/bin/colab").expanduser())
COLAB_SESSIONS = Path("~/.config/colab-cli/sessions.json").expanduser()
# Optional Hugging Face read token. No model needs one, but anonymous downloads hit rate limits (429).
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
# Model catalog: models.toml in the repository root if present, otherwise the shipped defaults.
MODELS_FILE = Path(
    os.environ.get("STUDIO_MODELS")
    or (REPO_DIR / "models.toml" if (REPO_DIR / "models.toml").is_file() else REPO_DIR / "station" / "models.default.toml")
).expanduser()


def colab_python():
    """Python of the colab CLI's own environment (used to release runtimes by endpoint)."""
    return str(Path(COLAB).resolve().parent / "python")
