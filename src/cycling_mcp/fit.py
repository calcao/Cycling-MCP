from collections import Counter
from pathlib import Path
import fitdecode

def summarize_fit(path: Path) -> dict:
    """汇总可选 FIT 的传感器数据，允许缺少部分传感器，不返回轨迹。"""
    counts, totals = Counter(), Counter()
    maximum = {}
    with fitdecode.FitReader(path, check_crc=fitdecode.CrcCheck.WARN) as frames:
        for frame in frames:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                continue
            for name in ("heart_rate", "power", "cadence", "speed"):
                value = frame.get_value(name, fallback=None)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    counts[name] += 1
                    totals[name] += value
                    maximum[name] = max(maximum.get(name, float("-inf")), value)
    return {name: {"samples": counts[name], "average": round(totals[name] / counts[name], 1),
                   "maximum": maximum[name]} for name in counts}
