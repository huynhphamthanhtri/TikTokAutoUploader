# -*- coding: utf-8 -*-
"""
check_features.error_codes - Định nghĩa các mã lỗi chuẩn cho nhóm Check Thông Tin.
"""

from enum import Enum


class CheckFeatureErrorCode(str, Enum):
    SUCCESS = "SUCCESS"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SERVER_REJECTED = "SERVER_REJECTED"
    MUTATION_UNKNOWN = "MUTATION_UNKNOWN"
