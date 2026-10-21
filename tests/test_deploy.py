import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from dotenv import dotenv_values

from cycling_mcp.config import Settings
from cycling_mcp.deploy import DeploymentError, configure, validate_domain
from cycling_mcp.security import normalize_public_url, verify_password


@pytest.fixture
def setup_environment(monkeypatch):
    values = {"MCP_DOMAIN": "cycling.example.test", "IGPSPORT_TOKEN": "fixture-token",
              "MCP_ADMIN_PASSWORD": "fixture-owner-password-2026"}
    runtime = {name: value for name, value in os.environ.items()
               if not name.startswith(("MCP_", "IGPSPORT_", "CYCLING_", "SYNC_"))}
    monkeypatch.setattr(os, "environ", {**runtime, **values})
    return values


def test_first_configuration_and_repeat_are_safe(tmp_path, setup_environment, monkeypatch):
    initial = configure(tmp_path, interactive=False)
    raw = (tmp_path / ".env").read_text(encoding="utf-8")
    assert setup_environment["MCP_ADMIN_PASSWORD"] not in raw
    assert verify_password(setup_environment["MCP_ADMIN_PASSWORD"], initial["MCP_ADMIN_PASSWORD_HASH"])
    monkeypatch.delenv("MCP_ADMIN_PASSWORD")
    repeated = configure(tmp_path, interactive=False)
    assert repeated["MCP_OAUTH_STORAGE_KEY"] == initial["MCP_OAUTH_STORAGE_KEY"]
    assert repeated["MCP_ADMIN_PASSWORD_HASH"] == initial["MCP_ADMIN_PASSWORD_HASH"]
    assert repeated["IGPSPORT_TOKEN"] == "fixture-token"
    assert repeated["MCP_PUBLIC_URL"] == "https://cycling.example.test"
    if os.name != "nt":
        assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600


def test_credentials_remain_literal_in_dotenv(tmp_path, setup_environment, monkeypatch):
    monkeypatch.delenv("IGPSPORT_TOKEN")
    monkeypatch.setenv("IGPSPORT_USERNAME", "fixture-user")
    password = "literal${UNSET}'$password#value"
    monkeypatch.setenv("IGPSPORT_PASSWORD", password)
    configure(tmp_path, interactive=False)
    monkeypatch.delenv("IGPSPORT_PASSWORD")
    monkeypatch.chdir(tmp_path)
    assert Settings.from_env().password == password


def test_lost_storage_key_is_not_replaced(tmp_path, setup_environment):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "oauth.db").touch()
    with pytest.raises(DeploymentError, match="restore MCP_OAUTH_STORAGE_KEY"):
        configure(tmp_path, interactive=False)
    assert not (tmp_path / ".env").exists()


def test_password_reset_preserves_encryption_key(tmp_path, setup_environment, monkeypatch):
    initial = configure(tmp_path, interactive=False)
    monkeypatch.delenv("MCP_ADMIN_PASSWORD")
    replacement = "replacement-owner-password-2026"
    monkeypatch.setattr("cycling_mcp.deploy.getpass.getpass", lambda prompt: replacement)
    changed = configure(tmp_path, reset_password=True)
    assert changed["MCP_OAUTH_STORAGE_KEY"] == initial["MCP_OAUTH_STORAGE_KEY"]
    assert changed["MCP_ADMIN_PASSWORD_HASH"] != initial["MCP_ADMIN_PASSWORD_HASH"]
    assert verify_password(replacement, changed["MCP_ADMIN_PASSWORD_HASH"])
    assert not verify_password(setup_environment["MCP_ADMIN_PASSWORD"], changed["MCP_ADMIN_PASSWORD_HASH"])


@pytest.mark.parametrize("value", [None, "", "cycling.example.com", "https://my.example", "127.0.0.1", "::1", "my.example\n{respond evil}"])
def test_invalid_domain_is_rejected(value):
    with pytest.raises(DeploymentError):
        validate_domain(value)


@pytest.mark.parametrize("value", ["http://my.example", "https://user:password@my.example", "https://my.example/path", "https://my.example?q=1"])
def test_invalid_public_origin_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_public_url(value)


def test_noninteractive_setup_reports_missing_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"MCP_DOMAIN": "cycling.example.test"})
    with pytest.raises(DeploymentError, match="IGPSPORT_TOKEN"):
        configure(tmp_path, interactive=False)


def test_compose_retains_literal_password_hash_and_private_service(tmp_path, setup_environment, monkeypatch):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is not installed")
    password = "literal${UNSET}'$password#value"
    monkeypatch.delenv("IGPSPORT_TOKEN")
    monkeypatch.setenv("IGPSPORT_USERNAME", "fixture-user")
    monkeypatch.setenv("IGPSPORT_PASSWORD", password)
    configure(tmp_path, interactive=False)
    monkeypatch.delenv("IGPSPORT_PASSWORD")
    root = Path(__file__).resolve().parents[1]
    command = ["docker", "compose", "--project-directory", str(root), "--env-file", str(tmp_path / ".env"),
               "-f", str(root / "compose.yaml"), "config"]
    result = subprocess.run([*command, "--format", "json"], env=os.environ, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert "ports" not in services["cycling-server"]
    expected = dotenv_values(tmp_path / ".env", interpolate=False)["MCP_ADMIN_PASSWORD_HASH"]
    # Compose 的配置序列化可能将美元符转义为双美元符，插值环境仍应保留原值。
    assert services["cycling-server"]["environment"]["MCP_ADMIN_PASSWORD_HASH"] in {expected, expected.replace("$", "$$")}
    assert services["cycling-server"]["environment"]["IGPSPORT_PASSWORD"] in {password, password.replace("$", "$$")}
    environment = subprocess.run([*command, "--environment"], env=os.environ, capture_output=True, text=True, timeout=30)
    assert environment.returncode == 0, environment.stderr
    assert "MCP_ADMIN_PASSWORD_HASH=" + expected in environment.stdout.splitlines()
    assert "IGPSPORT_PASSWORD=" + password in environment.stdout.splitlines()
    assert services["cycling-server"]["environment"]["MCP_PUBLIC_URL"] == "https://cycling.example.test"
    assert services["caddy"]["depends_on"]["cycling-server"]["condition"] == "service_healthy"
