from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

class ActivityStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (root / "fit").mkdir(exist_ok=True)
        self.db_path = root / "cycling.db"
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS activities (
                id TEXT PRIMARY KEY, title TEXT, start_time TEXT, distance_m REAL,
                moving_time_s INTEGER, fit_file TEXT, sha256 TEXT,
                record_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS sync_status (
                id INTEGER PRIMARY KEY CHECK(id=1), started_at TEXT,
                completed_at TEXT, status TEXT NOT NULL, detail TEXT)""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def has_fit(self, ride_id):
        with self.connection() as db:
            row = db.execute("SELECT fit_file FROM activities WHERE id=?", (ride_id,)).fetchone()
        return bool(row and row["fit_file"] and (self.root / row["fit_file"]).exists())

    def save(self, ride_id, info, relative_fit, digest):
        with self.connection() as db:
            db.execute("""INSERT INTO activities VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,start_time=excluded.start_time,
                distance_m=excluded.distance_m,moving_time_s=excluded.moving_time_s,
                fit_file=excluded.fit_file,sha256=excluded.sha256,
                record_json=excluded.record_json,updated_at=excluded.updated_at""",
                (ride_id, str(info.get("title", "")), str(info.get("startTime", "")),
                 info.get("rideDistance"), info.get("totalMovingTime"), relative_fit,
                 digest, json.dumps(info, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat()))

    def record_sync(self, status, detail=""):
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            db.execute("""INSERT INTO sync_status VALUES (1,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                started_at=CASE WHEN excluded.status='running' THEN excluded.started_at ELSE sync_status.started_at END,
                completed_at=CASE WHEN excluded.status='running' THEN sync_status.completed_at ELSE excluded.completed_at END,
                status=excluded.status,detail=excluded.detail""",
                (now, None if status == "running" else now, status, detail))

    def status(self):
        with self.connection() as db:
            row = db.execute("SELECT * FROM sync_status WHERE id=1").fetchone()
            count = db.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        return {"count": count, "sync": dict(row) if row else None}

    def list_activities(self, limit=20):
        limit = max(1, min(100, limit))
        with self.connection() as db:
            rows = db.execute("""SELECT id,title,start_time,distance_m,moving_time_s
                FROM activities ORDER BY start_time DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def get_activity(self, ride_id):
        with self.connection() as db:
            row = db.execute("""SELECT id,title,start_time,distance_m,moving_time_s,fit_file,sha256
                FROM activities WHERE id=?""", (ride_id,)).fetchone()
        return dict(row) if row else None
