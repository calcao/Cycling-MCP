from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
from .security import normalize_public_url

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token: str
    username: str
    password: str
    host: str
    port: int
    interval_minutes: int
    sync_pages: int
    download_fit: bool = False
    public_url: str = ""
    admin_username: str = "owner"
    admin_password_hash: str = ""
    oauth_storage_key: str = ""

    @classmethod
    def from_env(cls):
        """加载当前目录的本地配置，保留已设置的环境变量优先级。"""
        load_dotenv(Path.cwd() / ".env", override=False, encoding="utf-8-sig", interpolate=False)
        download_fit = os.getenv("SYNC_DOWNLOAD_FIT", "false").strip().lower()
        if download_fit not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("SYNC_DOWNLOAD_FIT must be true or false")
        host = os.getenv("MCP_HOST", "127.0.0.1")
        public_url = os.getenv("MCP_PUBLIC_URL", "").strip()
        if public_url:
            public_url = normalize_public_url(public_url)
            if not all(os.getenv(name, "").strip() for name in (
                "MCP_ADMIN_USERNAME", "MCP_ADMIN_PASSWORD_HASH", "MCP_OAUTH_STORAGE_KEY")):
                raise ValueError("Public deployment requires OAuth configuration; run deploy.sh")
        elif host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Non-loopback MCP_HOST requires MCP_PUBLIC_URL and OAuth configuration")
        return cls(
            Path(os.getenv("CYCLING_DATA_DIR", "./data")),
            os.getenv("IGPSPORT_TOKEN", "").strip().removeprefix("Bearer ").strip(),
            os.getenv("IGPSPORT_USERNAME", ""),
            os.getenv("IGPSPORT_PASSWORD", ""),
            host,
            int(os.getenv("MCP_PORT", "8000")),
            int(os.getenv("SYNC_INTERVAL_MINUTES", "60")),
            int(os.getenv("SYNC_PAGES", "0")),
            download_fit in {"true", "1", "yes"},
            public_url,
            os.getenv("MCP_ADMIN_USERNAME", "owner"),
            os.getenv("MCP_ADMIN_PASSWORD_HASH", ""),
            os.getenv("MCP_OAUTH_STORAGE_KEY", ""),
        )
