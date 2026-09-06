"""Validated global predictive settings; persisted identifiers stay English."""
import math

DEFAULTS = {
    "poll_interval_seconds": 1.0,
    "window_before_minutes": 10,
    "window_after_minutes": 10,
    "stop_after_detection": True,
}

KEY_STATUS_LABELS = {
    "ACTIVE": "Hoạt động", "QUOTA_EXCEEDED": "Hết hạn mức",
    "RATE_LIMITED": "Tạm giới hạn", "INVALID": "Key không hợp lệ",
    "DISABLED": "API chưa được bật",
}


def normalize_settings(raw=None, strict=False):
    result = dict(raw) if isinstance(raw, dict) else {}
    for key, default in DEFAULTS.items():
        value = result.get(key, default)
        try:
            if key == "stop_after_detection":
                if not isinstance(value, bool):
                    raise ValueError()
            else:
                if isinstance(value, bool):
                    raise ValueError()
                value = float(value)
                if not math.isfinite(value) or value < 0 or (key == "poll_interval_seconds" and value == 0):
                    raise ValueError()
            result[key] = value
        except (ValueError, TypeError, OverflowError):
            if strict:
                raise ValueError("Khoảng cách quét phải là số hữu hạn lớn hơn 0; thời gian trước/sau phải là số hữu hạn không âm; tùy chọn ngừng quét phải là bật hoặc tắt.") from None
            result[key] = default
    return result


def estimate_requests(settings):
    cfg = normalize_settings(settings, strict=True)
    return (cfg["window_before_minutes"] + cfg["window_after_minutes"]) * 60 / cfg["poll_interval_seconds"]
