"""İki dil: arayüzde t("...") ile çevrilen her metnin ve sunucunun kullanıcıya gösterdiği her
doğrulama mesajının İngilizce karşılığı server/web/src/en.ts'de var (eksik olan Türkçe görünürdü)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "src"
EN_TS = (SRC / "en.ts").read_text()


def _ts_string(quoted):
    q, body = quoted[0], quoted[1:-1]
    if q == "'":
        body = body.replace("\\'", "'").replace('"', '\\"')
    return json.loads(f'"{body}"')


EN_KEYS = {_ts_string(m.group(1)) for m in re.finditer(r'^\s*("(?:[^"\\]|\\.)*"):', EN_TS, re.M)}
PATTERNS = [_ts_string(m.group(1)) for m in re.finditer(r'^\s*\[("(?:[^"\\]|\\.)*"),', EN_TS, re.M)]


def _pattern_regex(template):
    parts = re.split(r"(\{\w+\})", template)
    return re.compile("^" + "".join(r"[\s\S]*?" if re.fullmatch(r"\{\w+\}", p) else re.escape(p) for p in parts) + "$")


PATTERN_RES = [_pattern_regex(p) for p in PATTERNS]


def _translated(text):
    return text in EN_KEYS or any(r.match(text) for r in PATTERN_RES)


def test_every_ui_string_has_an_english_translation():
    missing = set()
    for path in SRC.glob("*.ts*"):
        if path.name in ("en.ts", "i18n.ts"):
            continue
        for m in re.finditer(r'\bt\((("(?:[^"\\]|\\.)*")|(\'(?:[^\'\\]|\\.)*\'))', path.read_text()):
            text = _ts_string(m.group(1))
            if text not in EN_KEYS:
                missing.add(f"{path.name}: {text}")
    assert not missing, "en.ts'de eksik çeviri:\n" + "\n".join(sorted(missing))


def test_every_server_validation_message_is_translated():
    """app.py'deki bad("...") / bad(f"...") mesajları: f-string alanları kalıplardaki {..} ile eşleşir."""
    app = (ROOT / "app.py").read_text()
    missing = []
    for m in re.finditer(r'bad\((f?)"((?:[^"\\]|\\.)*)"\)', app):
        text = m.group(2)
        if m.group(1):
            text = re.sub(r"\{[^}]*\}", "X", text)  # örnek değer; kalıptaki {ad} her metni yakalar
        if not _translated(text):
            missing.append(m.group(2))
    assert not missing, "çevirisi olmayan sunucu mesajları:\n" + "\n".join(missing)


def test_job_stage_labels_are_translated():
    import studio

    missing = [label for label in set(studio.STAGES.values()) | set(studio.MODEL_LABELS.values()) if not _translated(label)]
    assert not missing, missing


def test_runtime_step_labels_and_setup_phases_are_translated():
    import runtime

    labels = [label for _, label, _ in runtime.STEPS]
    setup = (ROOT.parent / "station" / "runtime_setup.sh").read_text()
    labels += [m.group(1).replace("${WANTED}", "image") for m in re.finditer(r'^phase "([^"]+)"', setup, re.M)]
    missing = [label for label in labels if not _translated(label)]
    assert not missing, missing


def test_setup_problems_are_translated(monkeypatch, tmp_path):
    """Kurulum eksikliği mesajları (preflight) yeni kullanıcının ilk gördüğü şey: İngilizcesi olmalı."""
    import runtime

    colab = tmp_path / "colab"
    colab.write_text("#!/bin/sh\n")
    colab.chmod(0o755)
    monkeypatch.setattr(runtime, "COLAB_TOKEN", tmp_path / "token.json")
    messages = []
    for docker in ("", "1"):
        monkeypatch.setenv("STUDIO_IN_DOCKER", docker)
        monkeypatch.setattr(runtime, "COLAB", str(colab))
        messages.append(runtime.preflight())
    monkeypatch.setattr(runtime, "COLAB", str(tmp_path / "missing"))
    messages.append(runtime.preflight())
    assert all(messages) and len(set(messages)) == 3
    assert [m for m in messages if not _translated(m)] == []
