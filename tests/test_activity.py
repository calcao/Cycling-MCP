from cycling_mcp.activity import normalize_zones, summarize_api


def test_empty_analysis_uses_main_detail_intervals_without_inventing_duration():
    result = normalize_zones({
        "hrZoom": [{"grade": 1, "startNum": 127, "endNum": 138, "percentage": 27.1, "time": 976}],
        "powerZoom": [{"grade": 1, "startNum": 0, "endNum": 100, "percentage": 60}],
        "cadZoom": [{"grade": 1, "startNum": 0, "endNum": 60, "percentage": 20}],
        "spZoom": [{"grade": 1, "startNum": 0, "endNum": 5, "percentage": 10, "time": 360}],
    }, {})
    assert result["hrIntervals"] == [
        {"zoom": 1, "valueStart": 127, "valueEnd": 138, "percentage": 27.1, "time": 976}]
    assert "time" not in result["ftpIntervals"][0]
    assert result["cadenceIntervals"][0]["valueEnd"] == 60
    assert result["speedIntervals"][0]["time"] == 360


def test_custom_analysis_intervals_are_preserved():
    intervals = [{"zoom": 1, "valueStart": 120, "valueEnd": 140}]
    result = normalize_zones({"hrZoom": [{"grade": 9}], "powerZoom": [{"grade": 9}]}, {
        "mhrIntervals": intervals, "ftpIntervals": intervals, "isFtpIntervalDefault": 1})
    assert "hrIntervals" not in result
    assert result["mhrIntervals"] == intervals
    assert result["ftpIntervals"] == intervals
    assert result["isFtpIntervalDefault"] == 1


def test_missing_zones_are_distinct_from_successful_empty_analysis():
    assert normalize_zones({}, None) is None
    assert normalize_zones({}, {}) == {}


def test_api_metrics_accept_zero_and_reject_missing_or_nonfinite_values():
    assert summarize_api({"avgPower": 0, "maxHrm": None, "avgCad": False, "avgSpeed": float("nan")}) == {
        "power": {"average": 0, "unit": "W"}}
