import sys
from unittest.mock import Mock

import pytest

from cycling_mcp import cli
from cycling_mcp.config import Settings


@pytest.mark.parametrize("flag, configured, expected", [
    ("--download-fit", False, True), ("--no-download-fit", True, False)])
def test_cli_can_override_optional_fit(tmp_path, monkeypatch, flag, configured, expected, capsys):
    settings = Settings(tmp_path, "test-token", "", "", "127.0.0.1", 8000, 0, 0, configured)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    run = Mock(return_value={"status": "ok"})
    monkeypatch.setattr(cli, "sync_activities", run)
    monkeypatch.setattr(sys, "argv", ["cycling-sync", "sync", "--max-pages", "1", flag])
    cli.main()
    assert run.call_args.args[0].download_fit is expected
    assert run.call_args.args[2] == 1
    assert '"status": "ok"' in capsys.readouterr().out
