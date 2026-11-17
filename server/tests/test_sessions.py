"""Session registry: slot/port scheme, persistent assignment and port conflicts.

No network: the port check binds a real socket, so the tests use short-lived ephemeral ports where
possible and never a fixed one they do not own.
"""
import json
import pathlib
import socket

import pytest

import config
import runtime
import sessions


def test_slot_zero_keeps_the_configured_name_and_port():
    """Slot 0 is frozen: existing installs, docs and tests assume the default session and 8188."""
    assert sessions.session_name(0) == config.SESSION
    assert sessions.slot_port(0) == runtime.COMFY_PORT == 8188
    assert sessions.ssh_conf_path(0) == runtime.SSH_CONF
    assert sessions.host_alias(0) == "colab"


def test_new_slots_get_their_own_ports_and_configs():
    """Extra slots start at 8200, step 10; each has its own SSH config and Host name."""
    assert sessions.slot_port(1) == 8200
    assert sessions.slot_port(2) == 8210
    assert sessions.slot_port(4) == 8230
    assert sessions.session_name(1) == f"{config.SESSION}-2"
    assert sessions.session_name(4) == f"{config.SESSION}-5"
    assert sessions.ssh_conf_path(2).name == "config.2"
    assert sessions.host_alias(2) == "colab-2"


def test_ports_are_unique_across_slots():
    ports = [sessions.slot_port(slot) for slot in range(sessions.MAX_SESSIONS + 1)]
    assert len(ports) == len(set(ports))


def test_session_data_lives_in_its_own_directory(tmp_path):
    assert sessions.data_dir(0, tmp_path) == tmp_path
    assert sessions.data_dir(2, tmp_path) == tmp_path / "sessions" / "2"


def test_port_check_detects_a_listening_socket():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        assert sessions.port_is_free(busy.getsockname()[1]) is False


def test_registry_persists_the_assignment(tmp_path):
    path = tmp_path / "sessions.json"
    entry = sessions.Registry(path, check_ports=False).reserve(1)
    assert entry["name"] == f"{config.SESSION}-2" and entry["port"] == 8200
    assert json.loads(path.read_text())["1"]["port"] == 8200

    again = sessions.Registry(path, check_ports=False)
    assert again.entries()[1]["name"] == f"{config.SESSION}-2"
    assert again.free_slot() == 2
    assert again.active_count() == 1


def test_registry_releases_and_reuses_the_lowest_slot(tmp_path):
    reg = sessions.Registry(tmp_path / "sessions.json", check_ports=False)
    reg.reserve(1)
    reg.reserve(2)
    reg.release(1)
    assert reg.reserve()["slot"] == 1


def test_registry_respects_the_session_limit(tmp_path):
    reg = sessions.Registry(tmp_path / "sessions.json", max_sessions=2, check_ports=False)
    reg.reserve(1)
    reg.reserve(2)
    assert reg.free_slot() is None
    with pytest.raises(sessions.PortBusy, match="limit reached"):
        reg.reserve()


def test_registry_refuses_a_busy_port(tmp_path):
    """A port held by another process must not be tunnelled: the assignment is refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", sessions.PORT_BASE))
        busy.listen(1)
        reg = sessions.Registry(tmp_path / "sessions.json")
        with pytest.raises(sessions.PortBusy, match="in use"):
            reg.reserve(1)
        assert reg.entries() == {}


def test_slot_zero_is_not_written_to_the_registry(tmp_path):
    path = tmp_path / "sessions.json"
    reg = sessions.Registry(path, check_ports=False)
    entry = reg.reserve(0)
    assert entry["name"] == config.SESSION and entry["port"] == 8188
    assert not path.exists()
    assert reg.active_count() == 0


def test_plan_is_clamped_to_the_hard_limit():
    assert sessions.clamp_plan(3) == 3
    assert sessions.clamp_plan(0) == 1
    assert sessions.clamp_plan(99) == sessions.MAX_SESSIONS
    assert sessions.clamp_plan("4") == 4
    assert sessions.clamp_plan(None) == sessions.DEFAULT_SESSIONS
    assert sessions.clamp_plan("abc") == sessions.DEFAULT_SESSIONS


def test_plan_comes_from_the_settings_dict():
    assert sessions.plan_from({}) == sessions.DEFAULT_SESSIONS
    assert sessions.plan_from({sessions.PLAN_KEY: 2}) == 2
    assert sessions.plan_from({sessions.PLAN_KEY: 99}) == sessions.MAX_SESSIONS


def test_limits_summary_shape():
    assert sessions.limits(active=2, plan=3) == {
        "max": sessions.MAX_SESSIONS, "default": sessions.DEFAULT_SESSIONS, "plan": 3, "active": 2,
    }


def test_unaccounted_assignments_point_at_a_leaked_runtime():
    assert sessions.unaccounted(3, ["studio", "studio-2"]) == 1
    assert sessions.unaccounted(2, ["studio", "studio-2"]) == 0
    assert sessions.unaccounted(None, ["studio"]) is None


def test_new_session_data_dir_is_created_before_writing(tmp_path, monkeypatch):
    """A session's own timing/endpoint files live in `sessions/<slot>/`; the directory must exist
    before the first write (a production run crashed with FileNotFoundError)."""
    monkeypatch.setattr(runtime, "DATA_DIR", tmp_path)
    rt = runtime.SessionRuntime(slot=1, name=f"{config.SESSION}-2", host_alias=sessions.host_alias(1),
                                data_dir=sessions.data_dir(1, tmp_path))
    runtime._own("endpoint-1", rt)
    assert (tmp_path / "sessions" / "1" / "owned_endpoints.json").read_text() == '["endpoint-1"]'


def test_setup_script_accepts_a_pack_profile():
    """The GPU side takes an optional pack profile; the default keeps every pack (old behaviour)."""
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    script = (root / "station" / "runtime_setup.sh").read_text()
    assert "PACK_PROFILE=${2:-all}" in script
    assert "DEFAULT_PACKS=" in script
    assert "${PACK_PROFILE}" in script
