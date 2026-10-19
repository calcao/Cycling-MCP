from __future__ import annotations
import httpx
from urllib.parse import urlparse

BASE = "https://prod.zh.igpsport.com"
LIST = "/service/web-gateway/web-analyze/activity/queryMyActivity"
DETAIL = "/service/web-gateway/web-analyze/activity/queryActivityDetail/"
LOGIN = "/service/auth/account/login"

class IgpsportError(RuntimeError):
    pass

class IgpsportClient:
    """Unofficial domestic iGPSPORT web endpoints, based on FitSync."""
    def __init__(self, token="", username="", password=""):
        self.token, self.username, self.password = token, username, password
        self.http = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False,
            headers={"Accept": "application/json", "Origin": "https://app.zh.igpsport.com",
                     "Referer": "https://app.zh.igpsport.com/", "timezone": "Asia/Shanghai",
                     "qiwu-app-version": "1.0.0"})

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def login(self):
        if not self.username or not self.password:
            raise IgpsportError("Missing or expired IGPSPORT_TOKEN; configure credentials locally")
        r = self.http.post(LOGIN, json={"username": self.username, "password": self.password, "appId": "igpsport-web"})
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0 or not data.get("data", {}).get("access_token"):
            raise IgpsportError("Login rejected")
        self.token = data["data"]["access_token"]

    def get(self, path, params=None):
        if not self.token:
            self.login()
        r = self.http.get(path, params=params, headers={"Authorization": "Bearer " + self.token})
        if r.status_code in (401, 403) and self.username and self.password:
            self.login()
            r = self.http.get(path, params=params, headers={"Authorization": "Bearer " + self.token})
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
        # Do not allow arbitrary URLs from the upstream detail response.
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise IgpsportError("Invalid FIT URL")
        # Host allowlist is intentionally strict; extend only after inspecting actual fitUrl.
        host = parsed.hostname.lower().rstrip(".")
        if host not in ("igpsport.com", "prod.zh.igpsport.com") and not host.endswith(".igpsport.com"):
            raise IgpsportError("FIT host not allowlisted; inspect actual fitUrl before extending")
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            r = client.get(url)
            r.raise_for_status()
            content = r.content
        if len(content) < 14 or content[8:12] != b".FIT":
            raise IgpsportError("Downloaded data is not FIT")
        return content
