from __future__ import annotations
import logging
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from mcp.server.fastmcp import FastMCP
from .config import Settings
from .fit import summarize_fit
from .storage import ActivityStore
from .sync import sync_activities

settings = Settings.from_env()
store = ActivityStore(settings.data_dir)
mcp = FastMCP("Cycling Data", host=settings.host, port=settings.port,
              stateless_http=True, json_response=True)
_REQUEST_LOCK = threading.Lock()

@mcp.tool()
def list_activities(limit: int = 20) -> list[dict]:
    """List synchronized iGPSPORT rides."""
    return store.list_activities(limit)

@mcp.tool()
def get_activity(activity_id: str) -> dict:
    """Return ride metadata and aggregated sensors without GPS track."""
    row = store.get_activity(activity_id)
    if row is None:
        return {"error": "activity_not_found"}
    try:
        row["sensors"] = summarize_fit(store.root / row["fit_file"])
    except (ValueError, OSError, EOFError) as exc:
        row["sensors_error"] = type(exc).__name__
    return row

@mcp.tool()
def get_sync_status() -> dict:
    """Return synchronization status and ride count."""
    return {**store.status(), "interval_minutes": settings.interval_minutes}

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
        scheduler.shutdown(wait=False)

def _initial_sync():
    try:
        sync_activities(settings, store, settings.sync_pages or None)
    except Exception:
        logging.exception("Initial sync failed")

if __name__ == "__main__":
    main()
