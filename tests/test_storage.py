from cycling_mcp.storage import ActivityStore

def test_store(tmp_path):
    store = ActivityStore(tmp_path)
    assert store.status()["count"] == 0
    (tmp_path / "fit" / "123.fit").write_bytes(b"test")
    store.save("123", {"title": "Ride", "startTime": "2026.10.19"}, "fit/123.fit", "abc")
    assert store.has_fit("123")
    assert store.get_activity("123")["title"] == "Ride"
    assert len(store.list_activities()) == 1


def test_api_only_activity_and_metadata_refresh_preserve_optional_resources(tmp_path):
    store = ActivityStore(tmp_path)
    info = {"title": "Original", "rideDistance": 1000, "avgHrm": 134,
            "pwrNP": 90, "fitUrl": "https://example.invalid/private",
            "token": "private", "position_lat": 123,
            "laps": [{"items": [{"avgHrm": 140, "startPositionLat": 123}]}],
            "zones": {"hr": [10, 20]}}
    store.save("123", info)
    store.save("123", {"title": "Updated", "sync_errors": [{"source": "laps", "type": "SigningError"}]})

    row = store.get_activity("123")
    assert row["title"] == "Updated"
    assert row["summary"]["avgHrm"] == 134
    assert row["summary"]["pwrNP"] == 90
    assert row["laps"] == [{"items": [{"avgHrm": 140}]}]
    assert row["zones"] == {"hr": [10, 20]}
    assert row["fit_file"] is None
    assert not store.has_fit("123")
    assert all(key not in row["summary"] for key in ("fitUrl", "token", "position_lat"))


def test_metadata_refresh_does_not_remove_existing_fit(tmp_path):
    store = ActivityStore(tmp_path)
    (tmp_path / "fit" / "123.fit").write_bytes(b"test")
    store.save("123", {"title": "Original"}, "fit/123.fit", "abc")
    store.save("123", {"title": "Updated"})
    assert store.has_fit("123")
    assert store.get_activity("123")["sha256"] == "abc"


def test_activity_list_supports_offset(tmp_path):
    store = ActivityStore(tmp_path)
    for ride_id in ("1", "2", "3"):
        store.save(ride_id, {"startTime": ride_id})
    assert [row["id"] for row in store.list_activities(limit=1, offset=1)] == ["2"]
