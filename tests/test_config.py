import os
import pytest

from cycling_mcp.config import Settings


def test_settings_load_local_env_without_overriding_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {"IGPSPORT_TOKEN": "environment-token"})
    (tmp_path / ".env").write_text(
        "IGPSPORT_TOKEN=file-token\n"
        "IGPSPORT_USERNAME=test-user\n"
        "IGPSPORT_WASM_PATH=D:/local/edge_core.wasm\n"
        "SYNC_INTERVAL_MINUTES=15\n",
        encoding="utf-8",
    )

    settings = Settings.from_env()

    assert settings.token == "environment-token"
    assert settings.username == "test-user"
    assert settings.interval_minutes == 15
    assert os.environ["IGPSPORT_WASM_PATH"] == "D:/local/edge_core.wasm"


def test_settings_without_local_env_use_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {})

    settings = Settings.from_env()

    assert settings.token == ""
    assert settings.interval_minutes == 60
    assert settings.download_fit is False


@pytest.mark.parametrize("value, expected", [("true", True), ("false", False), ("1", True), ("0", False)])
def test_optional_fit_setting(tmp_path, monkeypatch, value, expected):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {"SYNC_DOWNLOAD_FIT": value})
    assert Settings.from_env().download_fit is expected


def test_invalid_optional_fit_setting_is_explicit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {"SYNC_DOWNLOAD_FIT": "maybe"})
    with pytest.raises(ValueError, match="SYNC_DOWNLOAD_FIT"):
        Settings.from_env()
