"""Yollar boşluk, tırnak ya da % içerebilir (ör. ~/My Projects/colab-studio): ssh ayarı geçerli kalmalı."""
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

import runtime

pytestmark = pytest.mark.skipif(shutil.which("ssh") is None, reason="ssh yok")


def test_ssh_config_parses_with_awkward_paths(tmp_path, monkeypatch):
    key = tmp_path / "My Data" / "it's 100%" / "id_ed25519"
    monkeypatch.setattr(runtime, "SSH_KEY", key)
    monkeypatch.setattr(runtime, "COLAB", str(tmp_path / "co lab" / "colab"))
    monkeypatch.setattr(runtime, "COLAB_SESSIONS", tmp_path / "my sessions.json")
    conf = tmp_path / "config"
    conf.write_text(runtime.ssh_conf_text())
    out = subprocess.run(["ssh", "-F", str(conf), "-G", "colab"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
    # ssh -G yazdırırken "%" dizilerini açmaz (bağlanırken "%%" tek "%" olur): açılmış hali karşılaştırılır.
    assert lines["identityfile"].replace("%%", "%") == str(key)
    # ProxyCommand kabuğa verilir: bölününce her yol tek parça kalmalı.
    parts = shlex.split(lines["proxycommand"].replace("%%", "%"))
    assert parts[0] == "sh" and Path(parts[1]).name == "colab_proxy.sh"
    assert parts[3] == str(tmp_path / "my sessions.json") and parts[4] == str(tmp_path / "co lab" / "colab")
