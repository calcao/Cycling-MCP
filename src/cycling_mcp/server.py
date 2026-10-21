from __future__ import annotations
import logging
import threading
from fitdecode import FitError
from apscheduler.schedulers.background import BackgroundScheduler
from mcp.server.fastmcp import FastMCP
from .config import Settings
from .activity import summarize_api
from .fit import summarize_fit
from .storage import ActivityStore
from .sync import sync_activities

settings = Settings.from_env()
store = ActivityStore(settings.data_dir)
mcp = FastMCP("Cycling Data", host=settings.host, port=settings.port,
              stateless_http=True, json_response=True)
_REQUEST_LOCK = threading.Lock()

@mcp.tool()
def list_activities(limit: int = 20, offset: int = 0) -> list[dict]:
    """分页查询已同步活动；距离单位为米，移动时间单位为秒。"""
    return store.list_activities(limit, offset)

@mcp.tool()
def get_activity(activity_id: str) -> dict:
    """查询活动汇总、NP/TSS/IF、计圈和区间；传感器值标注单位，不返回轨迹。"""
    row = store.get_activity(activity_id)
    if row is None:
        return {"error": "activity_not_found"}
    sensors = summarize_api(row["summary"])
    sources = {name: "api" for name in sensors}
    units = {"heart_rate": "bpm", "power": "W", "cadence": "rpm", "speed": "m/s"}
    needs_fit = any(name not in sensors or any(key not in sensors[name] for key in ("average", "maximum"))
                    for name in units)
    if row.get("fit_file") and needs_fit:
        try:
            fallback = summarize_fit(store.root / row["fit_file"])
            for name, values in fallback.items():
                current = sensors.get(name, {})
                if any(key in values and key not in current for key in ("average", "maximum")):
                    sensors[name] = {**values, "unit": units[name], **current}
                    sources[name] = "api+fit" if current else "fit"
        except (ValueError, OSError, EOFError, FitError) as exc:
            row["sensors_error"] = type(exc).__name__
    row["sensors"], row["sensor_sources"] = sensors, sources
    return row

@mcp.tool()
def get_sync_status() -> dict:
    """Return synchronization status and ride count."""
    return {**store.status(), "interval_minutes": settings.interval_minutes,
            "download_fit": settings.download_fit}

@mcp.tool()
def sync_now() -> dict:
    """Request background sync; poll get_sync_status for progress."""
    if not _REQUEST_LOCK.acquire(blocking=False):
        return {"status": "already_requested"}
    def run():
        try:
            sync_activities(settings, store, settings.sync_pages or None)
        except Exception:
            logging.exception("Manual sync failed")
        finally:
            _REQUEST_LOCK.release()
    threading.Thread(target=run, daemon=True).start()
    return {"status": "started"}

def main():
    logging.basicConfig(level=logging.INFO)
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    if settings.interval_minutes > 0:
        scheduler.add_job(sync_activities, "interval", minutes=settings.interval_minutes,
                          args=(settings, store, settings.sync_pages or None),
                          id="igpsport-sync", max_instances=1, coalesce=True)
        scheduler.start()
        threading.Thread(target=_initial_sync, daemon=True).start()
    try:
        mcp.run(transport="streamable-http")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)

def _initial_sync():
    try:
        sync_activities(settings, store, settings.sync_pages or None)
    except Exception:
        logging.exception("Initial sync failed")

if __name__ == "__main__":
    main()
