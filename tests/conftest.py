import time

import pytest


@pytest.fixture(autouse=True)
def no_real_agent_logs(tmp_path_factory, monkeypatch):
    """`whyl log`/`whyl blame` sync agent logs from this machine; tests must never see the real ones."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path_factory.mktemp("codex-home")))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-home")))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("xdg-data")))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-config")))


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    """`whyl` prints local times; pin the zone so expected output doesn't depend on the machine."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
