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
    if not _LOCK.acquire(blocking=False):
        return {"status": "busy"}
    created = skipped = pages = 0
    errors = []
    store.record_sync("running")
    try:
        with IgpsportClient(settings.token, settings.username, settings.password) as client:
            page = 1
            while max_pages is None or page <= max_pages:
                rows = client.activity_page(page).get("rows") or []
                if not rows:
                    break
                pages += 1
                for item in rows:
                    ride_id = str(item.get("rideId") or item.get("RideId") or "")
                    if not ride_id.isdecimal():
                        errors.append("missing numeric rideId")
                        continue
                    if store.has_fit(ride_id):
                        skipped += 1
                        continue
                    try:
                        detail = client.activity_detail(ride_id)
                        if not detail.get("fitUrl"):
                            raise ValueError("Missing fitUrl")
                        fit = client.download_fit(detail["fitUrl"])
                        name = "fit/" + ride_id + ".fit"
                        target = store.root / name
                        tmp = target.with_suffix(".fit.tmp")
                        tmp.write_bytes(fit)
                        os.replace(tmp, target)
                        info = {**item, **{k: v for k, v in detail.items() if k not in ("fitUrl", "token")}}
                        store.save(ride_id, info, name, hashlib.sha256(fit).hexdigest())
                        created += 1
                    except Exception as exc:
                        errors.append("ride " + ride_id + ": " + type(exc).__name__)
                        log.warning("Failed to sync ride %s (%s)", ride_id, type(exc).__name__)
                    time.sleep(0.8)
                if len(rows) < 20:
                    break
                page += 1
                time.sleep(0.5)
        result = {"status": "partial" if errors else "ok", "downloaded": created,
                  "already_present": skipped, "pages": pages, "errors": errors[:20]}
        store.record_sync(result["status"], str({k:v for k,v in result.items() if k!="errors"}))
        return result
    except Exception as exc:
        store.record_sync("error", type(exc).__name__)
        raise
    finally:
        _LOCK.release()
