from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

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

    @classmethod
    def from_env(cls):
        return cls(
            Path(os.getenv("CYCLING_DATA_DIR", "./data")),
            os.getenv("IGPSPORT_TOKEN", "").strip().removeprefix("Bearer ").strip(),
            os.getenv("IGPSPORT_USERNAME", ""),
            os.getenv("IGPSPORT_PASSWORD", ""),
            os.getenv("MCP_HOST", "127.0.0.1"),
            int(os.getenv("MCP_PORT", "8000")),
            int(os.getenv("SYNC_INTERVAL_MINUTES", "60")),
            int(os.getenv("SYNC_PAGES", "0")),
        )
