import base64
import hashlib
import hmac

import pytest

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
