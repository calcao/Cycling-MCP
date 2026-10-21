from __future__ import annotations
import logging
import threading
from urllib.parse import urlsplit
import uvicorn
from fitdecode import FitError
from apscheduler.schedulers.background import BackgroundScheduler
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from .config import Settings
from .activity import summarize_api
from .fit import summarize_fit
from .storage import ActivityStore
from .sync import sync_activities
from .oauth import OAuthProvider, SCOPE, make_http_app

settings = Settings.from_env()
store = ActivityStore(settings.data_dir)
oauth_provider = OAuthProvider(settings.public_url, settings.admin_username, settings.admin_password_hash,
    settings.oauth_storage_key, store.root / "oauth.db") if settings.public_url else None
auth_options = {}
if oauth_provider is not None:
    auth_options = {"auth_server_provider": oauth_provider, "auth": oauth_provider.auth_settings(),
        "transport_security": TransportSecuritySettings(
            allowed_hosts=[urlsplit(settings.public_url).hostname, "127.0.0.1:*", "localhost:*"],
            allowed_origins=[settings.public_url, "https://chatgpt.com", "https://platform.openai.com"])}
mcp = FastMCP("Cycling Data", host=settings.host, port=settings.port,
              stateless_http=True, json_response=True, **auth_options)
tool_security = {"securitySchemes": [{"type": "oauth2", "scopes": [SCOPE]}]} if oauth_provider else {
    "securitySchemes": [{"type": "noauth"}]}
_REQUEST_LOCK = threading.Lock()

@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request):
    """提供不包含个人数据的容器就绪检查。"""
    store.status()
    return JSONResponse({"status": "ok"})

@mcp.tool(meta=tool_security, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def list_activities(limit: int = 20, offset: int = 0) -> list[dict]:
    """分页查询已同步活动；距离单位为米，移动时间单位为秒。"""
    return store.list_activities(limit, offset)

@mcp.tool(meta=tool_security, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
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

@mcp.tool(meta=tool_security, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def get_sync_status() -> dict:
    """返回同步状态、活动数量和定时配置。"""
    return {**store.status(), "interval_minutes": settings.interval_minutes,
            "download_fit": settings.download_fit}

@mcp.tool(meta=tool_security, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def sync_now() -> dict:
    """请求后台同步，通过 get_sync_status 查询进度。"""
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
        uvicorn.run(make_http_app(mcp, oauth_provider), host=settings.host, port=settings.port, access_log=False)
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
