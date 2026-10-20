from __future__ import annotations
import httpx
from urllib.parse import urlparse

BASE = "https://prod.zh.igpsport.com"
TIMESTAMP = "/service/edge-core/api/public/timestamp"
LIST = "/service/web-gateway/web-analyze/activity/queryMyActivity"
DETAIL = "/service/web-gateway/web-analyze/activity/queryActivityDetail/"

class IgpsportError(RuntimeError):
    pass

class IgpsportClient:
    """Unofficial iGPSPORT CN API. Use an existing bearer token until signing is verified."""
    def __init__(self, token="", username="", password=""):
        self.token = token.strip().removeprefix("Bearer ").strip()
        # Do not retain credentials: current login requires an unknown request signature.
        self.http = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False,
            headers={"Accept": "application/json", "Origin": "https://app.igpsport.cn",
                     "Referer": "https://app.igpsport.cn/", "timezone": "Asia/Shanghai",
                     "qiwu-app-version": "8.07.08", "x-platform": "web"})

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def server_timestamp(self):
        """Read public server timestamp for diagnosing clock skew; no signing implied."""
        r = self.http.get(TIMESTAMP)
        r.raise_for_status()
        return r.json()

    def login(self):
        raise IgpsportError(
            "Automatic login is unavailable: current iGPSPORT Web requires "
            "x-signature, x-nonce and x-timestamp. Supply IGPSPORT_TOKEN locally."
        )

    def get(self, path, params=None):
        if not self.token:
            self.login()
        r = self.http.get(path, params=params,
                          headers={"Authorization": "Bearer " + self.token})
        if r.status_code in (401, 403):
            raise IgpsportError("Bearer token expired or rejected; update IGPSPORT_TOKEN")
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise IgpsportError("iGPSPORT API rejected request")
        return data.get("data") or {}

    def activity_page(self, page):
        if page < 1:
            raise ValueError("page must be positive")
        return self.get(LIST, {"pageNo": page, "pageSize": 20, "reqType": 0, "sort": 1})

    def activity_detail(self, ride_id):
        if not str(ride_id).isdecimal():
            raise ValueError("Invalid ride ID")
        return self.get(DETAIL + str(ride_id))

    def download_fit(self, url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise IgpsportError("Invalid FIT URL")
        host = parsed.hostname.lower().rstrip(".")
        if host != "igpsport.com" and not host.endswith(".igpsport.com"):
            raise IgpsportError("FIT host not allowlisted; inspect actual fitUrl before extending")
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            r = client.get(url)
            r.raise_for_status()
            content = r.content
        if len(content) < 14 or content[8:12] != b".FIT":
            raise IgpsportError("Downloaded data is not FIT")
        return content
