from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .signing import SignedTransport, SigningError, TIME_PATH

LIST = "/service/web-gateway/web-analyze/activity/queryMyActivity"
DETAIL = "/service/web-gateway/web-analyze/activity/queryActivityDetail/"
LAPS = "/service/sportg/sporth/activity/queryActivityLapV2"
ZONES = "/service/web-gateway/web-analyze/webActivityAnalyze/queryActivityDetail/"
FIT_OSS_HOST = "igp-zh.oss-cn-hangzhou.aliyuncs.com"
MAX_FIT_BYTES = 32 * 1024 * 1024


def _ride_id(value):
    value = str(value)
    if not value.isascii() or not value.isdecimal():
        raise ValueError("Invalid ride ID")
    return value


class IgpsportError(SigningError):
    pass


class IgpsportClient:
    """Signed iGPSPORT CN client. Requires the pinned local WASM asset."""

    def __init__(self, token="", username="", password="", wasm_path=None):
        self.token = token.strip()
        self.username = username
        self.password = password
        path = wasm_path or os.getenv("IGPSPORT_WASM_PATH", "assets/edge_core_wasm_bg-GHC38n8Q.wasm")
        self.wasm_path = Path(path)
        self.transport = None
        self._authenticated = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.transport is not None:
            self.transport.close()

    def _authenticate(self):
        if self._authenticated:
            return
        if not self.token and not (self.username and self.password):
            raise IgpsportError("Configure IGPSPORT_TOKEN or username/password locally")
        if self.transport is None:
            self.transport = SignedTransport(self.wasm_path)
        if self.token:
            self.transport.use_token(self.token)
        elif self.username and self.password:
            self.transport.login(self.username, self.password)
        self._authenticated = True

    def login(self):
        self._authenticate()

    def server_timestamp(self):
        if self.transport is None:
            self.transport = SignedTransport(self.wasm_path)
        return self.transport._exchange("GET", TIME_PATH)

    def get(self, path, params=None):
        self._authenticate()
        return self.transport.get(path, params)

    def activity_page(self, page, page_size=20):
        if type(page) is not int or page < 1 or type(page_size) is not int or page_size < 1:
            raise ValueError("page and page_size must be positive integers")
        data = self.get(LIST, {"pageNo": page, "pageSize": page_size,
                               "reqType": 0, "sort": 1, "sortType": 1})
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            raise IgpsportError("Invalid activity list response")
        return data

    def activity_detail(self, ride_id):
        """查询活动汇总详情，FIT 下载地址仅用于本次可选下载。"""
        data = self.get(DETAIL + _ride_id(ride_id))
        if not isinstance(data, dict):
            raise IgpsportError("Invalid activity detail response")
        return data

    def activity_laps(self, ride_id):
        """查询官网计圈接口，保留不同运动类型的原始统计结构。"""
        data = self.get(LAPS, {"activityId": _ride_id(ride_id)})
        if not isinstance(data, (dict, list)):
            raise IgpsportError("Invalid activity laps response")
        return data

    def activity_zones(self, ride_id):
        """查询官网心率、功率、速度和踏频区间分析。"""
        data = self.get(ZONES + _ride_id(ride_id))
        if not isinstance(data, dict):
            raise IgpsportError("Invalid activity zones response")
        return data

    def download_fit(self, url):
        """从已核对的站点限量下载 FIT，不向文件站点发送认证头。"""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise IgpsportError("Invalid FIT URL")
        host = parsed.hostname.lower().rstrip(".")
        if host != FIT_OSS_HOST and host != "igpsport.com" and not host.endswith(".igpsport.com"):
            raise IgpsportError("FIT host not allowlisted; inspect actual fitUrl before extending")
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_FIT_BYTES:
                        raise IgpsportError("FIT download exceeds size limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
        if len(content) < 14 or content[8:12] != b".FIT":
            raise IgpsportError("Downloaded data is not FIT")
        return content
