"""Signed iGPSPORT CN web API transport.

Derived from the user's verified reverse-engineered client. The matching WASM
asset is supplied locally at runtime and must never be committed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE_URL = "https://prod.zh.igpsport.com"
KEY_PATH = "/service/edge-core/api/public/key"
TIME_PATH = "/service/edge-core/api/public/timestamp"
LOGIN_PATH = "/service/auth/account/login"
WASM_SHA256 = "7b9d7936b75afc21d0cca11a3eddddea09ecdd782c6064dd1f65d6e674313bfb"
JS_SPACE = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


class SigningError(RuntimeError):
    """Protocol error; never includes raw responses or secrets."""


def _leb(raw: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while pos < len(raw):
        byte = raw[pos]
        pos += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, pos
        shift += 7
        if shift > 35:
            break
    raise SigningError("Invalid WASM LEB encoding")


def load_unwrapping_key(path: Path):
    """Extract the RSA private key from a pinned WASM version, without executing it."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SigningError("Missing WASM asset; configure IGPSPORT_WASM_PATH") from exc
    if hashlib.sha256(raw).hexdigest() != WASM_SHA256:
        raise SigningError("WASM SHA256 mismatch; inspect the new asset before using it")
    pos = 8
    while pos < len(raw):
        section = raw[pos]
        size, start = _leb(raw, pos + 1)
        end = start + size
        if end > len(raw):
            raise SigningError("Truncated WASM section")
        pos = end
        if section != 11:
            continue
        count, cursor = _leb(raw, start)
        for _ in range(count):
            flags, cursor = _leb(raw, cursor)
            if flags != 0 or cursor >= end or raw[cursor] != 0x41:
                raise SigningError("Unsupported WASM data segment")
            offset, cursor = _leb(raw, cursor + 1)
            if cursor >= end or raw[cursor] != 0x0B:
                raise SigningError("Unsupported WASM offset expression")
            cursor += 1
            length, cursor = _leb(raw, cursor)
            if cursor + length > end:
                raise SigningError("Truncated WASM data segment")
            segment = raw[cursor:cursor + length]
            cursor += length
            left, right, key_size = 1055652, 1056844, 1192
            if offset <= left and right + key_size <= offset + length:
                material = bytes(segment[left-offset+i] ^ segment[right-offset+i]
                                 for i in range(key_size))
                try:
                    return serialization.load_der_private_key(material, password=None)
                except (ValueError, TypeError) as exc:
                    raise SigningError("Invalid WASM key material") from exc
    raise SigningError("WASM key material not found")


def unwrap_secret(encoded: str, private_key) -> bytes:
    try:
        value = "".join(encoded.split()).replace("-", "+").replace("_", "/")
        ciphertext = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
        key = private_key.decrypt(ciphertext, padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        if not key:
            raise ValueError("Empty key")
        return key
    except (ValueError, TypeError, AttributeError) as exc:
        raise SigningError("Failed to unwrap session key") from exc


def sign_request(key: bytes, method: str, path: str, timestamp: str,
                 nonce: str, body: bytes = b"") -> str:
    canonical = "\n".join((method.upper(), path, str(timestamp), nonce,
                           hashlib.sha256(body).hexdigest()))
    return base64.b64encode(hmac.new(key, canonical.encode("utf-8"),
                                     hashlib.sha256).digest()).decode("ascii")


def build_get_path(path: str, params: dict | None = None) -> str:
    if not re.fullmatch(r"/service/[A-Za-z0-9_./-]+", path) or ".." in path:
        raise ValueError("Expected relative /service/ API path")
    path = re.sub(r"/{2,}", "/", path)
    pairs = []
    for key, value in (params or {}).items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("Invalid query parameter")
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, int):
            value = str(value)
        elif isinstance(value, str):
            value = value.strip(JS_SPACE)
        else:
            raise ValueError("Unsupported query parameter type")
        pairs.append(f"{key}={quote(value, safe='!()*-._~,:$').replace('%20', '+')}")
    return path + ("?" + "&".join(pairs) if pairs else "")


class SignedTransport:
    def __init__(self, wasm_path: Path, *, timezone: str = "Asia/Shanghai",
                 timeout: float = 30):
        self.private_key = load_unwrapping_key(wasm_path)
        self.access_key = ""
        self.session_key = b""
        self.access_token = ""
        self.clock_offset = 0.0
        self.timezone = timezone
        self.http = httpx.Client(base_url=BASE_URL, timeout=timeout,
                                 follow_redirects=False,
                                 headers={
                                     "Accept": "application/json, text/plain, */*",
                                     "Origin": "https://app.igpsport.cn",
                                     "Referer": "https://app.igpsport.cn/",
                                     "Qiwu-App-Version": "8.07.08",
                                     "X-Platform": "web",
                                     "TimeZone": timezone,
                                 })

    def close(self):
        self.http.close()

    def _exchange(self, method: str, path: str, body: bytes | None = None,
                  *, signed: bool = False):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if signed:
            if not self.access_key or not self.session_key:
                raise SigningError("Session key not initialized")
            timestamp = str(int(time.time() + self.clock_offset))
            nonce = str(uuid.uuid4())
            headers.update({
                "X-Access-Key": self.access_key,
                "X-Timestamp": timestamp,
                "X-Nonce": nonce,
                "X-Signature": sign_request(self.session_key, method, path,
                                            timestamp, nonce, body or b""),
            })
            if self.access_token:
                headers["Authorization"] = "Bearer " + self.access_token
        try:
            response = self.http.request(method, path, content=body, headers=headers)
            status = response.status_code
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SigningError("iGPSPORT network or JSON response error") from exc
        if not isinstance(result, dict):
            raise SigningError("Invalid API response structure")
        code = result.get("code")
        if not 200 <= status < 300 or code != 0:
            safe_code = str(code) if isinstance(code, int) else "unknown"
            raise SigningError(f"iGPSPORT API rejected request (HTTP {status}, code {safe_code})")
        return result.get("data")

    def initialize(self):
        data = self._exchange("GET", KEY_PATH)
        if not isinstance(data, dict) or not isinstance(data.get("access_key"), str):
            raise SigningError("Missing access key")
        secret = unwrap_secret(data.get("secret_key"), self.private_key)
        if not data["access_key"]:
            raise SigningError("Empty access key")
        self.access_key, self.session_key = data["access_key"], secret
        timestamp = self._exchange("GET", TIME_PATH)
        try:
            value = int(timestamp)
            if isinstance(timestamp, bool) or not 1577808000 < value < 100000000000:
                raise ValueError("Invalid timestamp")
        except (TypeError, ValueError) as exc:
            raise SigningError("Invalid server timestamp") from exc
        self.clock_offset = value - time.time()

    def use_token(self, token: str):
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token or any(ch.isspace() or ord(ch) < 33 or ord(ch) > 126 for ch in token):
            raise ValueError("Invalid access token")
        self.access_token = ""
        self.initialize()
        self.access_token = token

    def login(self, username: str, password: str):
        username, password = username.strip(JS_SPACE), password.strip(JS_SPACE)
        if not username or not password:
            raise ValueError("Username and password are required")
        self.access_token = ""
        self.initialize()
        body = json.dumps({"appId": "igpsport-web", "username": username,
                           "password": password}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        data = self._exchange("POST", LOGIN_PATH, body, signed=True)
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise SigningError("Login response missing access_token")
        self.access_token = data["access_token"]

    def get(self, path: str, params: dict | None = None):
        if not self.access_token:
            raise SigningError("Authenticate before requesting activity data")
        return self._exchange("GET", build_get_path(path, params), signed=True)
