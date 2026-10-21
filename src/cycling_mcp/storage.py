from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .activity import normalize_zones, public_summary, sanitize_activity_data

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

    def save(self, ride_id, info, relative_fit=None, digest=None):
        """保存接口数据；资源未重新获取时保留已有数据及可选 FIT。"""
        with self.connection() as db:
            previous = db.execute("SELECT record_json FROM activities WHERE id=?", (ride_id,)).fetchone()
            record = json.loads(previous["record_json"]) if previous else {}
            record = sanitize_activity_data({**record, **info})
            db.execute("""INSERT INTO activities
                (id,title,start_time,distance_m,moving_time_s,fit_file,sha256,record_json,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,start_time=excluded.start_time,
                distance_m=excluded.distance_m,moving_time_s=excluded.moving_time_s,
                fit_file=COALESCE(excluded.fit_file,activities.fit_file),
                sha256=COALESCE(excluded.sha256,activities.sha256),
                record_json=excluded.record_json,updated_at=excluded.updated_at""",
                (ride_id, str(record.get("title", "")), str(record.get("startTime", "")),
                 record.get("rideDistance"), record.get("totalMovingTime", record.get("movingTime")), relative_fit,
                 digest, json.dumps(record, ensure_ascii=False),
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

    def list_activities(self, limit=20, offset=0):
        """按稳定顺序分页查询已同步活动。"""
        if type(limit) is not int or limit < 1 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be positive and offset must be non-negative")
        limit = max(1, min(100, limit))
        with self.connection() as db:
            rows = db.execute("""SELECT id,title,start_time,distance_m,moving_time_s
                FROM activities ORDER BY start_time DESC,id DESC LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def get_activity(self, ride_id):
        """返回详情汇总、计圈、区间及部分失败状态，不返回位置轨迹。"""
        with self.connection() as db:
            row = db.execute("""SELECT id,title,start_time,distance_m,moving_time_s,fit_file,sha256,
                record_json,updated_at
                FROM activities WHERE id=?""", (ride_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        info = sanitize_activity_data(json.loads(result.pop("record_json")))
        return {**result, "summary": public_summary(info), "laps": info.get("laps"),
                "zones": normalize_zones(info, info.get("zones")), "sync_errors": info.get("sync_errors", [])}
