from cycling_mcp.storage import ActivityStore

def test_store(tmp_path):
    store = ActivityStore(tmp_path)
    assert store.status()["count"] == 0
    (tmp_path / "fit" / "123.fit").write_bytes(b"test")
    store.save("123", {"title": "Ride", "startTime": "2026.10.19"}, "fit/123.fit", "abc")
    assert store.has_fit("123")
    assert store.get_activity("123")["title"] == "Ride"
    assert len(store.list_activities()) == 1
