"""整理接口汇总指标，并过滤凭据和位置数据。"""

import math


SUMMARY_FIELDS = frozenset("""
    rideId title startTime rideDistance recordTime totalTime movingTime totalMovingTime
    exerciseType recordType label productName deviceInfo avgSpeed maxSpeed avgHrm maxHrm
    avgCad maxCad avgPower maxPower avgTemperature maxTemperature calorie totalAscent
    totalDescent avgAltitude maxAltitude minAltitude pwrNP pwrTSS pwrIF avgBalance
    avgPedSmooth avgTQEffect trainingEffectAerobic trainingEffectAnaerobic
    upslopeAvgGrade upslopeMaxGrade downslopeAvgGrade downslopeMaxGrade
    upslopeAvgVerticalSpeed upslopeMaxVerticalSpeed downslopeAvgVerticalSpeed
    downslopeMaxVerticalSpeed avgStepFrequency maxStepFrequency avgStepStride
    maxStepStride stepCount totalCycles numLengths avgSwolf
    endTime avgMovingSpeed minHrm avgPco peakPower hrZoom powerZoom cadZoom spZoom
    upAndDownSlopeZoom upslopeDistance downslopeDistance manufacturer softwareVersion product
""".split())


def sanitize_activity_data(value):
    """递归移除下载地址、认证字段和位置轨迹，不影响统计数据。"""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            name = key.lower().replace("_", "").replace("-", "")
            if any(part in name for part in (
                "token", "password", "secret", "authorization", "cookie", "url",
                "latitude", "longitude", "position", "coordinate", "polyline", "gps", "track",
            )) or name in {"lat", "lng", "lon", "route", "location"}:
                continue
            result[key] = sanitize_activity_data(child)
        return result
    if isinstance(value, list):
        return [sanitize_activity_data(child) for child in value]
    return value


def public_summary(info):
    """仅返回已核对用途的活动汇总字段，保持接口原始单位。"""
    return {key: sanitize_activity_data(value) for key, value in info.items()
            if key in SUMMARY_FIELDS}


def summarize_api(info):
    """读取官网汇总值；速度单位为米每秒，不从采样值重新计算。"""
    result = {}
    for name, average, maximum, unit in (
        ("heart_rate", "avgHrm", "maxHrm", "bpm"),
        ("power", "avgPower", "maxPower", "W"),
        ("cadence", "avgCad", "maxCad", "rpm"),
        ("speed", "avgSpeed", "maxSpeed", "m/s"),
    ):
        values = {label: info[key] for label, key in (("average", average), ("maximum", maximum))
                  if isinstance(info.get(key), (int, float)) and not isinstance(info[key], bool)
                  and math.isfinite(info[key])}
        if values:
            result[name] = {**values, "unit": unit}
    return result


def normalize_zones(info, analysis):
    """保留新区间接口结果，缺失时使用主详情中的官网兼容区间。"""
    result = dict(analysis) if isinstance(analysis, dict) else {}
    mappings = [("ftpIntervals", "powerZoom"), ("speedIntervals", "spZoom"),
                ("cadenceIntervals", "cadZoom")]
    if not any(result.get(name) for name in ("mhrIntervals", "rhrIntervals", "lthrIntervals")):
        mappings.append(("hrIntervals", "hrZoom"))
    for target, source in mappings:
        if result.get(target) or not isinstance(info.get(source), list):
            continue
        intervals = []
        for entry in info[source]:
            if not isinstance(entry, dict):
                continue
            intervals.append({target_key: entry[source_key] for target_key, source_key in (
                ("zoom", "grade"), ("valueStart", "startNum"), ("valueEnd", "endNum"),
                ("percentage", "percentage"), ("time", "time")) if source_key in entry})
        if intervals:
            result[target] = intervals
    return result if result or analysis is not None else None
