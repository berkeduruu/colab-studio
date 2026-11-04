"""Studio settings.

Read from environment variables; a `.env` file in the repository root is loaded first (values that
are already set in the environment win). See `.env.example` for every option.
"""
import os
import shutil
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
HOST = os.environ.get("STUDIO_HOST", "127.0.0.1")
PORT = int(os.environ.get("STUDIO_PORT", "8190"))
# Host names the UI may be opened by, besides IP addresses and localhost (comma-separated, e.g. the
# nginx domain). Requests for any other name are refused: this blocks DNS rebinding, where a web page
# points its own domain at 127.0.0.1 to reach the studio. "*" turns the check off.
ALLOWED_HOSTS = {h.strip().lower().rstrip(".") for h in os.environ.get("STUDIO_ALLOWED_HOSTS", "").split(",") if h.strip()}
# Database, generated media, SSH key and learned timings.
DATA_DIR = Path(os.environ.get("STUDIO_DATA", "~/.local/share/colab-studio")).expanduser()
# The GPU runtime is released after this many idle minutes (it costs Colab compute units).
IDLE_MINUTES = int(os.environ.get("STUDIO_IDLE_MINUTES", "15"))
# Only L4 is supported: all workflows, VRAM budgets and time estimates were measured on it.
GPU = "L4"
# Colab session name the studio owns; other sessions on the same account are never touched.
SESSION = os.environ.get("STUDIO_SESSION", "studio")
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
