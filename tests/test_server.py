import asyncio
import importlib
import json
from unittest.mock import Mock

import pytest

from cycling_mcp.storage import ActivityStore


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLING_DATA_DIR", str(tmp_path))
    module = importlib.import_module("cycling_mcp.server")
    monkeypatch.setattr(module, "store", ActivityStore(tmp_path))
    return module


def call_tool(server, name, arguments):
    result = asyncio.run(server.mcp.call_tool(name, arguments))
    content = result[0] if isinstance(result, tuple) else result
    if name == "list_activities":
        return [json.loads(item.text) for item in content]
    payload = json.loads(content[0].text)
    return payload["result"] if isinstance(payload, dict) and set(payload) == {"result"} else payload


def test_mcp_returns_api_metrics_laps_and_zones_without_fit(server, monkeypatch):
    server.store.save("123", {
        "title": "API ride", "avgHrm": 134, "maxHrm": 154,
        "avgPower": 84, "maxPower": 134, "avgSpeed": 4.75,
        "pwrNP": 90, "pwrTSS": 25, "pwrIF": 0.5,
        "laps": [{"items": [{"avgHrm": 134}]}], "zones": {"hr": [10, 20]},
    })
    parse = Mock(side_effect=AssertionError("FIT must not be parsed"))
    monkeypatch.setattr(server, "summarize_fit", parse)

    row = call_tool(server, "get_activity", {"activity_id": "123"})

    assert row["sensors"]["heart_rate"] == {"average": 134, "maximum": 154, "unit": "bpm"}
    assert row["sensors"]["speed"]["unit"] == "m/s"
    assert row["summary"]["pwrTSS"] == 25
    assert row["laps"] == [{"items": [{"avgHrm": 134}]}]
    assert row["zones"] == {"hr": [10, 20]}
    assert row["sensor_sources"]["heart_rate"] == "api"
    parse.assert_not_called()


def test_mcp_paginates_and_reports_missing_activity(server):
    for ride_id in ("1", "2", "3"):
        server.store.save(ride_id, {"startTime": ride_id})
    rows = call_tool(server, "list_activities", {"limit": 1, "offset": 1})
    assert [row["id"] for row in rows] == ["2"]
    assert call_tool(server, "get_activity", {"activity_id": "missing"}) == {"error": "activity_not_found"}


def test_old_fit_can_supply_metrics_missing_from_api(server, monkeypatch):
    server.store.save("123", {"avgSpeed": 4.75}, "fit/123.fit", "test-digest")
    monkeypatch.setattr(server, "summarize_fit", lambda _: {
        "heart_rate": {"average": 130, "maximum": 150, "samples": 100},
        "speed": {"average": 4.7, "maximum": 6.0, "samples": 100}})
    row = server.get_activity("123")
    assert row["sensors"]["heart_rate"]["average"] == 130
    assert row["sensors"]["speed"]["average"] == 4.75
    assert row["sensor_sources"]["heart_rate"] == "fit"


def test_corrupted_optional_fit_does_not_break_api_query(server):
    (server.store.root / "fit" / "123.fit").write_bytes(b"not a fit file")
    server.store.save("123", {"avgPower": 84}, "fit/123.fit", "test-digest")
    row = server.get_activity("123")
    assert row["sensors"]["power"]["average"] == 84
    assert row["sensors_error"]
