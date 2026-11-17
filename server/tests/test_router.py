"""Routing: which session runs a job?

Order: user choice → packs ready → warm model → least loaded → lowest slot. No session is opened
implicitly (each one burns compute units).
"""
import pytest

import config
import router
import runtime
import sessions


def _manager(tmp_path):
    """Registry in a temp dir (no port checks) with the default plus two open sessions."""
    manager = sessions.Manager(sessions.Registry(tmp_path / "sessions.json", check_ports=False))
    runtime.DEFAULT.state["phase"] = "ready"
    for slot in (1, 2):
        manager.ensure(slot).state["phase"] = "ready"
    return manager


@pytest.fixture
def manager(monkeypatch, tmp_path):
    mgr = _manager(tmp_path)
    monkeypatch.setattr(sessions, "_manager", mgr)
    yield mgr
    for rt in mgr.live.values():
        rt.state["phase"] = "off"
        # Isınmışlık modül globalinde yaşadığı için (slot 0) sonraki testlere sızmasın.
        rt.local_modes.clear()
        rt.warm.clear()


def test_assigned_session_always_wins(manager):
    assert router.choose(manager, ("music",), "music", assigned="studio-2") == "studio-2"


def test_no_open_session_falls_back_to_the_default(manager):
    for rt in manager.live.values():
        rt.state["phase"] = "off"
    assert router.choose(manager, ("image",), "image") == runtime.SESSION


def test_a_session_with_the_pack_already_downloaded_wins(manager):
    manager.get(f"{config.SESSION}-2").local_modes.add("video")
    assert router.choose(manager, ("video",), "video") == f"{config.SESSION}-2"


def test_a_warm_session_wins(manager):
    manager.get(f"{config.SESSION}-2").local_modes.add("image")
    manager.get(f"{config.SESSION}-2").warm.add("image")
    runtime.DEFAULT.local_modes.add("image")
    assert router.choose(manager, ("image",), "image") == f"{config.SESSION}-2"


def test_the_least_loaded_session_wins(manager):
    loads = {config.SESSION: 3, f"{config.SESSION}-2": 1, f"{config.SESSION}-3": 2}
    assert router.choose(manager, ("image",), "image", load=loads.get) == f"{config.SESSION}-2"


def test_ties_go_to_the_lowest_slot(manager):
    """Equal load: the default session fills first (single-session behaviour is preserved)."""
    assert router.choose(manager) == runtime.SESSION


def test_starting_sessions_are_candidates(manager):
    for rt in manager.live.values():
        rt.state["phase"] = "off"
    manager.get(f"{config.SESSION}-3").state["phase"] = "starting"
    manager.get(f"{config.SESSION}-3").local_modes.add("video")
    assert router.choose(manager, ("video",), "video") == f"{config.SESSION}-3"


def test_explain_names_the_reason(manager):
    rt = manager.get(f"{config.SESSION}-2")
    rt.local_modes.add("video")
    rt.warm.add("video")
    info = router.explain(manager, ("video",), "video", load=lambda _: 0)
    assert info["session"] == f"{config.SESSION}-2"
    assert "model packs ready" in info["reason"] and "VRAM" in info["reason"]
