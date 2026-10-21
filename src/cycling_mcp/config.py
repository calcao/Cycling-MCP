from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

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

    @classmethod
    def from_env(cls):
        """加载当前目录的本地配置，保留已设置的环境变量优先级。"""
        load_dotenv(Path.cwd() / ".env", override=False, encoding="utf-8-sig")
        download_fit = os.getenv("SYNC_DOWNLOAD_FIT", "false").strip().lower()
        if download_fit not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("SYNC_DOWNLOAD_FIT must be true or false")
        return cls(
            Path(os.getenv("CYCLING_DATA_DIR", "./data")),
            os.getenv("IGPSPORT_TOKEN", "").strip().removeprefix("Bearer ").strip(),
            os.getenv("IGPSPORT_USERNAME", ""),
            os.getenv("IGPSPORT_PASSWORD", ""),
            os.getenv("MCP_HOST", "127.0.0.1"),
            int(os.getenv("MCP_PORT", "8000")),
            int(os.getenv("SYNC_INTERVAL_MINUTES", "60")),
            int(os.getenv("SYNC_PAGES", "0")),
            download_fit in {"true", "1", "yes"},
        )
