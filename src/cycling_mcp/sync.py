from __future__ import annotations
import hashlib
import logging
import os
import threading
import time
from .config import Settings
from .igpsport import IgpsportClient
from .storage import ActivityStore

log = logging.getLogger(__name__)
_LOCK = threading.Lock()

def sync_activities(settings: Settings, store: ActivityStore, max_pages=None):
    """优先同步接口数据，按需下载 FIT，并独立记录每项资源的失败。"""
    if max_pages is not None and (type(max_pages) is not int or max_pages < 1):
        raise ValueError("max_pages must be a positive integer or None")
    if not _LOCK.acquire(blocking=False):
        return {"status": "busy"}
    synced = downloaded = already_present = pages = 0
    errors = []
    seen = set()
    try:
        store.record_sync("running")
        with IgpsportClient(settings.token, settings.username, settings.password) as client:
            page = 1
            while max_pages is None or page <= max_pages:
                payload = client.activity_page(page)
                rows = payload["rows"]
                if not rows:
                    break
                pages += 1
                new_ids = 0
                for item in rows:
                    ride_id = str(item.get("rideId") or item.get("RideId") or "") if isinstance(item, dict) else ""
                    if not ride_id.isascii() or not ride_id.isdecimal():
                        errors.append("missing numeric rideId")
                        continue
                    if ride_id in seen:
                        continue
                    seen.add(ride_id)
                    new_ids += 1
                    resource_errors = []

                    def failed(source, exc):
                        resource_errors.append({"source": source, "type": type(exc).__name__})
                        errors.append(f"ride {ride_id} {source}: {type(exc).__name__}")
                        log.warning("Failed to sync ride %s resource %s (%s)", ride_id, source, type(exc).__name__)

                    try:
                        detail = client.activity_detail(ride_id)
                        info = {**item, **detail}
                        for name, fetch in (("laps", client.activity_laps), ("zones", client.activity_zones)):
                            try:
                                info[name] = fetch(ride_id)
                            except Exception as exc:
                                failed(name, exc)
                        relative_fit = digest = None
                        if settings.download_fit:
                            if store.has_fit(ride_id):
                                already_present += 1
                            else:
                                try:
                                    if not detail.get("fitUrl"):
                                        raise ValueError("Missing fitUrl")
                                    fit = client.download_fit(detail["fitUrl"])
                                    name = f"fit/{ride_id}.fit"
                                    target = store.root / name
                                    tmp = target.with_suffix(".fit.tmp")
                                    try:
                                        tmp.write_bytes(fit)
                                        os.replace(tmp, target)
                                    finally:
                                        tmp.unlink(missing_ok=True)
                                    relative_fit, digest = name, hashlib.sha256(fit).hexdigest()
                                    downloaded += 1
                                except Exception as exc:
                                    failed("fit", exc)
                        info["sync_errors"] = resource_errors
                        store.save(ride_id, info, relative_fit, digest)
                        synced += 1
                    except Exception as exc:
                        failed("detail_or_storage", exc)
                    time.sleep(0.8)
                if new_ids == 0:
                    errors.append("Activity pagination returned no new ride IDs")
                    break
                # 使用服务端分页信息，避免服务端缩小分页大小时遗漏历史活动。
                total_pages = payload.get("totalPage")
                if type(total_pages) is int and page >= total_pages:
                    break
                total_rows = payload.get("totalRows")
                if type(total_rows) is int and len(seen) >= total_rows:
                    break
                page_size = payload.get("pageSize")
                if total_pages is None and type(page_size) is int and 0 < len(rows) < page_size:
                    break
                page += 1
                time.sleep(0.5)
        result = {"status": "partial" if errors else "ok", "synced": synced,
                  "downloaded": downloaded, "already_present": already_present,
                  "pages": pages, "error_count": len(errors), "errors": errors[:20]}
        store.record_sync(result["status"], str({key: value for key, value in result.items() if key != "errors"}))
        return result
    except Exception as exc:
        try:
            store.record_sync("error", type(exc).__name__)
        except Exception:
            log.error("Failed to record synchronization failure")
        raise
    finally:
        _LOCK.release()
