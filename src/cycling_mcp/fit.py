from collections import Counter
from pathlib import Path
import fitdecode

def summarize_fit(path: Path) -> dict:
    """Aggregate sensor data without returning GPS traces."""
    counts, totals = Counter(), Counter()
    maximum = {}
    with fitdecode.FitReader(path, check_crc=fitdecode.CrcCheck.WARN) as frames:
        for frame in frames:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                continue
            for name in ("heart_rate", "power", "cadence", "speed"):
                value = frame.get_value(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    counts[name] += 1
                    totals[name] += value
                    maximum[name] = max(maximum.get(name, float("-inf")), value)
    return {name: {"samples": counts[name], "average": round(totals[name] / counts[name], 1),
                   "maximum": maximum[name]} for name in counts}
