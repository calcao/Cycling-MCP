"""使用合成 HTTP 响应验证签名、解密、同步、存储和 MCP 查询链路。"""

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from cycling_mcp import signing, sync
from cycling_mcp.config import Settings
from cycling_mcp.storage import ActivityStore


@pytest.mark.parametrize("authentication", ["token", "password"])
def test_signed_api_sync_to_registered_mcp_tool(tmp_path, monkeypatch, authentication):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    session_key = b"synthetic-session-key"
    wrapped = private_key.public_key().encrypt(session_key, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    laps = [{"items": [{"distance": 1000, "avgHrm": 134}]}]
    zones = {}
    seen = []

    def respond(request):
        path = request.url.path
        seen.append(path)
        if path == signing.KEY_PATH:
            return httpx.Response(200, headers={"Set-Cookie": "session=fixture; Path=/"}, json={
                "code": 0, "data": {"access_key": "fixture-key", "secret_key": base64.b64encode(wrapped).decode()}})
        assert request.headers["Cookie"] == "session=fixture"
        if path == signing.TIME_PATH:
            assert all(name not in request.headers for name in (
                "Qiwu-App-Version", "X-Platform", "TimeZone", "Authorization", "X-Signature"))
            return httpx.Response(200, json={"code": 0, "data": 1700000000})
        canonical = "\n".join((request.method, request.url.raw_path.decode(),
                               request.headers["X-Timestamp"], request.headers["X-Nonce"],
                               hashlib.sha256(request.content).hexdigest()))
        expected = base64.b64encode(hmac.new(session_key, canonical.encode(), hashlib.sha256).digest()).decode()
        assert request.headers["X-Signature"] == expected
        assert request.headers["X-Access-Key"] == "fixture-key"
        if path == signing.LOGIN_PATH:
            assert json.loads(request.content) == {
                "appId": "igpsport-web", "username": "test-user", "password": "test-password"}
            assert "Authorization" not in request.headers
            data = {"access_token": "test-token"}
        else:
            assert request.headers["Authorization"] == "Bearer test-token"
            if path.endswith("/activity/queryMyActivity"):
                assert dict(request.url.params) == {
                    "pageNo": "1", "pageSize": "20", "reqType": "0", "sort": "1", "sortType": "1"}
                data = {"rows": [{"rideId": 123, "title": "API ride", "rideDistance": 1000}], "totalPage": 1}
            elif path == "/service/web-gateway/web-analyze/activity/queryActivityDetail/123":
                data = {"avgHrm": 134, "maxHrm": 154, "avgPower": 84, "maxPower": 134,
                        "avgSpeed": 4.75, "maxSpeed": 6, "avgCad": 76, "maxCad": 89,
                        "pwrNP": 90, "pwrTSS": 25, "pwrIF": 0.5,
                        "hrZoom": [{"grade": 1, "startNum": 127, "endNum": 138, "percentage": 27.1, "time": 976}],
                        "fitUrl": "https://igp-zh.oss-cn-hangzhou.aliyuncs.com/unused"}
            elif path == "/service/sportg/sporth/activity/queryActivityLapV2":
                assert request.url.params["activityId"] == "123"
                data = laps
            elif path == "/service/web-gateway/web-analyze/webActivityAnalyze/queryActivityDetail/123":
                data = zones
            else:
                raise AssertionError("Unexpected API endpoint")
        return httpx.Response(200, json={"code": 0, "data": data})

    real_client = httpx.Client
    monkeypatch.setattr(signing, "load_unwrapping_key", lambda _: private_key)
    monkeypatch.setattr(signing.httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(sync.time, "sleep", lambda _: None)
    monkeypatch.setenv("CYCLING_DATA_DIR", str(tmp_path))
    settings = Settings(Path(tmp_path), "test-token" if authentication == "token" else "",
                        "test-user", "test-password", "127.0.0.1", 8000, 0, 0)
    store = ActivityStore(tmp_path)

    result = sync.sync_activities(settings, store)

    assert result["status"] == "ok"
    assert result["synced"] == 1 and result["downloaded"] == 0
    assert len(seen) == (6 if authentication == "token" else 7)
    server = importlib.import_module("cycling_mcp.server")
    monkeypatch.setattr(server, "store", store)
    output = asyncio.run(server.mcp.call_tool("get_activity", {"activity_id": "123"}))
    content = output[0] if isinstance(output, tuple) else output
    activity = json.loads(content[0].text)
    assert activity["sensors"]["heart_rate"]["average"] == 134
    assert activity["summary"]["pwrNP"] == 90
    assert activity["laps"] == laps
    assert activity["zones"]["hrIntervals"] == [
        {"zoom": 1, "valueStart": 127, "valueEnd": 138, "percentage": 27.1, "time": 976}]
    assert activity["fit_file"] is None
