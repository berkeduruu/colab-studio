"""Colab'ın tabanı bizim değil (Python, torch, CUDA, ön kurulu paketler): ComfyUI ve ses paketleri
kurulum betiğinin kendi venv'ine, kilit dosyasındaki sürümlerle kurulur. Bu testler o düzenin
kendi içinde tutarlı kaldığını denetler (gerçek GPU gerekmez)."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATION = ROOT / "station"
SETUP = (STATION / "runtime_setup.sh").read_text()
LOCK = (STATION / "requirements.lock").read_text()


def _var(name):
    return re.search(rf"^{name}=(\S+)$", SETUP, re.M).group(1)


def test_lock_pins_the_torch_family_to_the_version_the_setup_script_expects():
    torch = _var("TORCH_VERSION")
    assert re.search(rf"^torch=={re.escape(torch)}\+cu130$", LOCK, re.M)
    for name in ("torchvision", "torchaudio"):
        assert re.search(rf"^{name}==\S+\+cu130$", LOCK, re.M)
    # PyTorch'un cu130 deposu önce gelir, PyPI yedek olarak: ilk satırlar bunu söyler.
    assert "--index-url https://download.pytorch.org/whl/cu130" in LOCK
    assert "--extra-index-url https://pypi.org/simple" in LOCK


def test_lock_has_every_package_pinned_exactly():
    lines = [l for l in LOCK.splitlines() if l and not l.startswith(("#", "--"))]
    assert len(lines) > 100
    for line in lines:
        assert re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][^\s;]*", line), f"sabitlenmemiş satır: {line}"


def test_extra_requirements_pin_the_same_torch_as_the_setup_script():
    extra = (STATION / "requirements.extra.in").read_text()
    assert re.search(rf"^torch=={re.escape(_var('TORCH_VERSION'))}$", extra, re.M)


def test_lock_script_reads_its_versions_from_the_setup_script():
    lock_sh = (STATION / "lock.sh").read_text()
    for name in ("COMFY_COMMIT", "PYTHON_VERSION"):
        assert f"{name}=$(sed -n 's/^{name}=//p' runtime_setup.sh)" in lock_sh
    assert "--no-build" in lock_sh  # yalnızca wheel: çözümleme sırasında paket kodu çalışmaz


def test_audio_packages_are_installed_into_the_venv_not_the_system_python():
    assert "--system" not in SETUP
    assert "python3 -c" not in SETUP
    assert "python main.py" not in SETUP.replace('"${VENV}/bin/python" main.py', "")
    assert "/usr/local/lib/python" not in SETUP
    assert '-p "${VENV}" --no-deps --index-strategy unsafe-best-match -r ${STATION}/requirements.lock' in SETUP
    assert "--python-preference only-managed" in SETUP  # Colab'ın Python'una bağlı kalma
    assert re.search(r"^PYTHON_VERSION=3\.\d+$", SETUP, re.M)


def test_environment_is_verified_before_comfyui_starts():
    check = SETUP.index("envcheck.py")
    assert check < SETUP.index('main.py --listen')
    # eklenti klasörleri import yolunu gölgelemesin: kontrol /content'te, ComfyUI dizininde değil, çalışır
    assert '(cd /content && "${VENV}/bin/python" "${STATION}/envcheck.py"' in SETUP


def test_envcheck_reports_every_problem_and_writes_the_versions(tmp_path):
    """Bu test ortamında torch yok: kontrol çökmeden hepsini toplayıp 1 döner ve json yazar."""
    out = tmp_path / "info.json"
    proc = subprocess.run([sys.executable, str(STATION / "envcheck.py"), "2.11.0", "9.9", str(out)],
                          capture_output=True, text=True, cwd=tmp_path)
    assert proc.returncode == 1
    assert "ORTAM SORUNLARI" in proc.stderr
    assert "Python" in proc.stderr and "beklenen 9.9.x" in proc.stderr
    info = json.loads(out.read_text())
    assert info["problems"] and "packages" in info and "python" in info


def test_comfyui_process_pattern_matches_the_venv_python_only():
    import runtime

    rx = re.compile(runtime.COMFY_PGREP)
    assert rx.search("/content/venv/bin/python main.py --listen 127.0.0.1 --port 8188")
    assert rx.search("python3 main.py --listen 127.0.0.1")
    assert not rx.search("tail -n 40 /content/comfy.log && pgrep -af main.py")
    assert not rx.search(f"bash -c pgrep -f '{runtime.COMFY_PGREP}'")  # pgrep'i çalıştıran kabuk kendine uymaz
    assert f"'{runtime.COMFY_PGREP}'" in runtime.COMFY_PROCESS


def test_ssh_control_socket_stays_short_whatever_the_data_directory(monkeypatch, tmp_path):
    """Unix soket yolu ~104 baytı aşınca ssh açılmıyordu (uzun STUDIO_DATA'da GPU hiç açılmıyordu)."""
    import runtime

    long_dir = tmp_path / ("x" * 120)
    monkeypatch.setattr(runtime, "SSH_DIR", long_dir / "ssh")
    path = re.search(r"ControlPath (\S+)", runtime.ssh_conf_text()).group(1)
    assert str(long_dir) not in path
    # %C ssh'ta 40 karakterlik özettir; ssh geçici soket için ~17 bayt daha ekler
    assert len(path.replace("%C", "x" * 40)) + 17 < 104


@pytest.mark.parametrize("path", ["install.sh", "Dockerfile"])
def test_colab_cli_version_is_pinned_identically(path):
    versions = set(re.findall(r"google-colab-cli==([0-9.]+)", (ROOT / "Dockerfile").read_text()))
    versions |= set(re.findall(r"^COLAB_CLI_VERSION=([0-9.]+)$", (ROOT / "install.sh").read_text(), re.M))
    assert len(versions) == 1, f"install.sh ve Dockerfile aynı colab CLI sürümünü kurmalı: {versions}"
    assert "google-colab-cli" in (ROOT / path).read_text()
