import base64
import hashlib
import hmac
from pathlib import Path

import httpx
import pytest

from cycling_mcp import signing
from cycling_mcp.signing import build_get_path, sign_request, load_unwrapping_key, SigningError


def test_sign_request_matches_independent_hmac():
    key = b"session-key"
    body = b'{"appId":"igpsport-web"}'
    path = "/service/auth/account/login"
    canonical = "\n".join(("POST", path, "1700000000", "fixed-nonce",
                           hashlib.sha256(body).hexdigest()))
    expected = base64.b64encode(hmac.new(key, canonical.encode(),
                                         hashlib.sha256).digest()).decode()
    assert sign_request(key, "post", path, "1700000000", "fixed-nonce", body) == expected


def test_query_canonicalization():
    assert build_get_path("/service/web-gateway/web-analyze/activity/queryMyActivity",
                          {"pageNo": 1, "pageSize": 10, "reqType": 0, "sort": 1,
                           "sortType": 1}).endswith(
                               "?pageNo=1&pageSize=10&reqType=0&sort=1&sortType=1")
    assert build_get_path("/service/x", {"q": " a b "}) == "/service/x?q=a+b"


@pytest.mark.parametrize("path", ["https://example.com/service/x", "/service/../x",
                                   "/private/x", "/service/x?token=abc"])
def test_rejects_unsafe_path(path):
    with pytest.raises(ValueError):
        build_get_path(path)


def test_missing_wasm_is_explicit(tmp_path):
    with pytest.raises(SigningError, match="Missing WASM"):
        load_unwrapping_key(tmp_path / "missing.wasm")


def test_transport_ignores_system_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "unsupported://proxy.invalid")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(signing, "load_unwrapping_key", lambda _: object())

    transport = signing.SignedTransport(Path("unused.wasm"))
    transport.close()


def test_time_request_is_isolated_from_business_headers(monkeypatch):
    requests = []
    business_headers = ("Qiwu-App-Version", "X-Platform", "TimeZone")
    credentials = ("Authorization", "X-Access-Key", "X-Timestamp", "X-Nonce", "X-Signature")

    def respond(request):
        requests.append(request)
        if request.url.path == signing.KEY_PATH:
            assert all(name in request.headers for name in business_headers)
            return httpx.Response(200, json={"code": 0, "data": {
                "access_key": "test-access-key", "secret_key": "wrapped-test-key"}})
        if request.url.path == signing.TIME_PATH:
            if any(name in request.headers for name in business_headers + credentials):
                return httpx.Response(403, json={"code": 1021})
            return httpx.Response(200, json={"code": 0, "data": 1700000000})
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["X-Access-Key"] == "test-access-key"
        assert request.headers["X-Signature"]
        assert all(name in request.headers for name in business_headers)
        return httpx.Response(200, json={"code": 0, "data": {"rows": []}})

    real_client = httpx.Client
    monkeypatch.setattr(signing, "load_unwrapping_key", lambda _: object())
    monkeypatch.setattr(signing, "unwrap_secret", lambda *_: b"session-key")
    monkeypatch.setattr(signing.httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(respond)))
    transport = signing.SignedTransport(Path("unused.wasm"))
    try:
        transport.use_token("test-token")
        assert transport.get("/service/test") == {"rows": []}
        assert transport._exchange("GET", signing.TIME_PATH) == 1700000000
        assert len(requests) == 4
    finally:
        transport.close()
