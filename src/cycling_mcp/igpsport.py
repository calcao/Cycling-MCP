from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .signing import SignedTransport, SigningError, TIME_PATH

LIST = "/service/web-gateway/web-analyze/activity/queryMyActivity"
DETAIL = "/service/web-gateway/web-analyze/activity/queryActivityDetail/"


class IgpsportError(SigningError):
    pass


class IgpsportClient:
    """Signed iGPSPORT CN client. Requires the pinned local WASM asset."""

    def __init__(self, token="", username="", password="", wasm_path=None):
        self.token = token.strip()
        self.username = username
        self.password = password
        path = wasm_path or os.getenv("IGPSPORT_WASM_PATH", "assets/edge_core_wasm_bg-GHC38n8Q.wasm")
        self.transport = SignedTransport(Path(path))
        self._authenticated = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.transport.close()

    def _authenticate(self):
        if self._authenticated:
            return
        if self.token:
            self.transport.use_token(self.token)
        elif self.username and self.password:
            self.transport.login(self.username, self.password)
        else:
            raise IgpsportError("Configure IGPSPORT_TOKEN or username/password locally")
        self._authenticated = True

    def login(self):
        self._authenticate()

    def server_timestamp(self):
        return self.transport._exchange("GET", TIME_PATH)

    def get(self, path, params=None):
        self._authenticate()
        return self.transport.get(path, params)

    def activity_page(self, page, page_size=20):
        if type(page) is not int or page < 1 or type(page_size) is not int or page_size < 1:
            raise ValueError("page and page_size must be positive integers")
        data = self.get(LIST, {"pageNo": page, "pageSize": page_size,
                               "reqType": 0, "sort": 1, "sortType": 1})
        if not isinstance(data, dict):
            raise IgpsportError("Invalid activity list response")
        return data

    def activity_detail(self, ride_id):
        if not str(ride_id).isdecimal():
            raise ValueError("Invalid ride ID")
        data = self.get(DETAIL + str(ride_id))
        if not isinstance(data, dict):
            raise IgpsportError("Invalid activity detail response")
        return data

    def download_fit(self, url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise IgpsportError("Invalid FIT URL")
        host = parsed.hostname.lower().rstrip(".")
        if host != "igpsport.com" and not host.endswith(".igpsport.com"):
            raise IgpsportError("FIT host not allowlisted; inspect actual fitUrl before extending")
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            response = client.get(url)
            response.raise_for_status()
            content = response.content
        if len(content) < 14 or content[8:12] != b".FIT":
            raise IgpsportError("Downloaded data is not FIT")
        return content
