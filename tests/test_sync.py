from contextlib import contextmanager
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cycling_mcp import sync
from cycling_mcp.config import Settings
from cycling_mcp.igpsport import IgpsportError
from cycling_mcp.storage import ActivityStore


@pytest.fixture
def setup_sync(tmp_path, monkeypatch):
    settings = Settings(tmp_path, "test-token", "", "", "127.0.0.1", 8000, 0, 0)
    store = ActivityStore(tmp_path)
    client = SimpleNamespace(
        activity_page=Mock(return_value={"rows": [{"rideId": 123, "title": "Ride"}], "totalPage": 1}),
        activity_detail=Mock(return_value={"avgHrm": 134, "maxHrm": 154, "avgPower": 84, "pwrNP": 90}),
        activity_laps=Mock(return_value=[{"items": [{"avgHrm": 134}]}]),
        activity_zones=Mock(return_value={"hr": [10, 20]}),
        download_fit=Mock(),
    )

    @contextmanager
    def factory(*_):
        yield client

    monkeypatch.setattr(sync, "IgpsportClient", factory)
    monkeypatch.setattr(sync.time, "sleep", lambda _: None)
    return settings, store, client


def test_default_sync_saves_detail_laps_and_zones_without_fit(setup_sync):
    settings, store, client = setup_sync
    result = sync.sync_activities(settings, store)
    assert result["status"] == "ok"
    assert result["synced"] == 1
    assert result["downloaded"] == 0
    row = store.get_activity("123")
    assert row["summary"]["pwrNP"] == 90
    assert row["laps"] == [{"items": [{"avgHrm": 134}]}]
    assert row["zones"] == {"hr": [10, 20]}
    assert row["fit_file"] is None
    client.download_fit.assert_not_called()


def test_refresh_updates_existing_activity_even_when_fit_exists(setup_sync):
    settings, store, client = setup_sync
    (store.root / "fit" / "123.fit").write_bytes(b"test")
    store.save("123", {"title": "Old"}, "fit/123.fit", "abc")
    client.activity_page.return_value["rows"][0]["title"] = "Updated"
    assert sync.sync_activities(settings, store)["synced"] == 1
    assert store.get_activity("123")["title"] == "Updated"
    assert store.has_fit("123")


def test_auxiliary_failure_preserves_metadata_and_previous_laps(setup_sync):
    settings, store, client = setup_sync
    store.save("123", {"laps": [{"items": []}]})
    client.activity_laps.side_effect = IgpsportError("No access")
    result = sync.sync_activities(settings, store)
    assert result["status"] == "partial"
    assert result["synced"] == 1
    row = store.get_activity("123")
    assert row["summary"]["avgHrm"] == 134
    assert row["laps"] == [{"items": []}]
    assert row["sync_errors"] == [{"source": "laps", "type": "IgpsportError"}]


def test_optional_fit_failure_does_not_discard_api_data(setup_sync):
    settings, store, client = setup_sync
    settings = replace(settings, download_fit=True)
    client.activity_detail.return_value["fitUrl"] = "https://igp-zh.oss-cn-hangzhou.aliyuncs.com/test"
    client.download_fit.side_effect = IgpsportError("Download failed")
    result = sync.sync_activities(settings, store)
    assert result["status"] == "partial"
    assert result["synced"] == 1
    assert result["downloaded"] == 0
    assert store.get_activity("123")["summary"]["pwrNP"] == 90


def test_optional_fit_is_saved_with_hash_without_temporary_file(setup_sync):
    settings, store, client = setup_sync
    settings = replace(settings, download_fit=True)
    client.activity_detail.return_value["fitUrl"] = "https://igp-zh.oss-cn-hangzhou.aliyuncs.com/test"
    content = b"\x0e\x10\x00\x00\x00\x00\x00\x00.FIT\x00\x00"
    client.download_fit.return_value = content
    result = sync.sync_activities(settings, store)
    assert result["status"] == "ok"
    assert result["downloaded"] == 1
    assert (store.root / "fit" / "123.fit").read_bytes() == content
    assert not (store.root / "fit" / "123.fit.tmp").exists()
    assert store.get_activity("123")["sha256"] == hashlib.sha256(content).hexdigest()


def test_pagination_uses_server_metadata_instead_of_assumed_page_size(setup_sync):
    settings, store, client = setup_sync
    client.activity_page.side_effect = [
        {"rows": [{"rideId": 1}], "pageSize": 1, "totalPage": 2},
        {"rows": [{"rideId": 2}], "pageSize": 1, "totalPage": 2},
    ]
    result = sync.sync_activities(settings, store)
    assert result["pages"] == 2
    assert result["synced"] == 2
    assert store.status()["count"] == 2


def test_recording_start_failure_releases_sync_lock(setup_sync, monkeypatch):
    settings, store, _ = setup_sync
    real_record = store.record_sync
    monkeypatch.setattr(store, "record_sync", Mock(side_effect=OSError("DB unavailable")))
    with pytest.raises(OSError):
        sync.sync_activities(settings, store)
    monkeypatch.setattr(store, "record_sync", real_record)
    try:
        assert sync.sync_activities(settings, store)["status"] == "ok"
    finally:
        if sync._LOCK.locked():
            sync._LOCK.release()
