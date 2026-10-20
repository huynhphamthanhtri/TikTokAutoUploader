# -*- coding: utf-8 -*-
"""
check_features.services.vpcs_violation_decoder - Giải mã placeholder %s và tính toán hạn gậy VPCS.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Tuple


def decode_violation_text(description_template: str, args: List[Dict[str, Any]]) -> str:
    """
    Điền các placeholder '%s' trong mô tả vi phạm TikTok tuần tự bằng key trong args.
    Tuyệt đối an toàn, không sử dụng eval hay string formatting dễ lỗi.
    """
    if not description_template:
        return ""
    if not args:
        return description_template

    parts = description_template.split("%s")
    if len(parts) <= 1:
        return description_template

    result = []
    arg_idx = 0
    for i, part in enumerate(parts):
        result.append(part)
        if i < len(parts) - 1:
            if arg_idx < len(args):
                val = str(args[arg_idx].get("key") or "")
                result.append(val)
                arg_idx += 1
            else:
                result.append("%s")

    return "".join(result)


def calculate_penalty_expiry(video_posted_date: int) -> Tuple[int, int]:
    """
    Tính ngày hết gậy và số ngày còn lại (quy chuẩn +33 ngày = 33 * 86400 giây).
    Trả về (expiry_timestamp, days_remaining).
    """
    if not video_posted_date:
        return 0, 0

    expiry = video_posted_date + (33 * 86400)
    now = int(time.time())
    diff = expiry - now
    if diff <= 0:
        return expiry, 0
    days = max(1, math.ceil(diff / 86400))
    return expiry, days
