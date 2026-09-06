import time
from unittest.mock import MagicMock
import pytest
from googleapiclient.errors import HttpError
import httplib2

from youtube_monitor.key_manager import ApiKeyInfo, ApiKeyManager, _next_midnight_utc


def test_api_key_info_usability():
    now = time.time()
    info = ApiKeyInfo(key="KEY_A", status="ACTIVE")
    assert info.is_usable(now)

    # Quota exceeded before reset
    info.status = "QUOTA_EXCEEDED"
    info.quota_resets_at = now + 3600
    assert not info.is_usable(now)

    # Quota exceeded after reset time
    assert info.is_usable(now + 3601)
    assert info.status == "ACTIVE"

    # Rate limited before cooldown
    info.status = "RATE_LIMITED"
    info.retry_after = now + 60
    assert not info.is_usable(now)

    # Rate limited after cooldown
    assert info.is_usable(now + 65)
    assert info.status == "ACTIVE"

    # Invalid / Disabled never auto-recovers
    info.status = "INVALID"
    assert not info.is_usable(now + 10000)
    info.status = "DISABLED"
    assert not info.is_usable(now + 10000)


def test_round_robin_selection():
    mgr = ApiKeyManager(["KEY_1", "KEY_2", "KEY_3"])
    k1 = mgr.get_active_key()
    k2 = mgr.get_active_key()
    k3 = mgr.get_active_key()
    k4 = mgr.get_active_key()
    assert [k1, k2, k3, k4] == ["KEY_1", "KEY_2", "KEY_3", "KEY_1"]


def test_error_classification_quota_exceeded():
    mgr = ApiKeyManager(["KEY_1", "KEY_2"])
    resp = httplib2.Response({"status": 403})
    content = b'{"error": {"errors": [{"reason": "quotaExceeded", "message": "The request cannot be completed because you have exceeded your quota."}]}}'
    exc = HttpError(resp, content)

    err_type = mgr.handle_api_error("KEY_1", exc)
    assert err_type == "QUOTA_EXCEEDED"

    # KEY_1 should no longer be active, manager should immediately return KEY_2
    next_key = mgr.get_active_key()
    assert next_key == "KEY_2"


def test_error_classification_rate_limited():
    mgr = ApiKeyManager(["KEY_1", "KEY_2"])
    resp = httplib2.Response({"status": 403})
    content = b'{"error": {"errors": [{"reason": "rateLimitExceeded", "message": "User rate limit exceeded."}]}}'
    exc = HttpError(resp, content)

    err_type = mgr.handle_api_error("KEY_1", exc)
    assert err_type == "RATE_LIMITED"
    assert mgr.get_active_key() == "KEY_2"


def test_error_classification_server_error_preserves_key():
    mgr = ApiKeyManager(["KEY_1"])
    resp = httplib2.Response({"status": 503})
    exc = HttpError(resp, b"Backend Error")

    err_type = mgr.handle_api_error("KEY_1", exc)
    assert err_type == "SERVER_ERROR"
    # Should still be usable, not disabled
    assert mgr.get_active_key() == "KEY_1"


def test_load_and_save_config():
    mgr = ApiKeyManager(["KEY_X", "KEY_Y"])
    cfg = {}
    mgr.save_to_config(cfg)
    assert cfg["api_keys"] == ["KEY_X", "KEY_Y"]
    assert len(cfg["api_keys_pool"]) == 2

    # Load into new manager
    mgr2 = ApiKeyManager()
    mgr2.load_from_config(cfg)
    assert mgr2.get_keys() == ["KEY_X", "KEY_Y"]


def test_youtube_client_caching_and_invalidation(monkeypatch):
    build_calls = []

    def mock_build(service, version, developerKey=None, cache_discovery=False):
        build_calls.append((service, version, developerKey))
        mock_client = MagicMock()
        mock_client.developerKey = developerKey
        return mock_client

    monkeypatch.setattr("youtube_monitor.key_manager.build", mock_build)

    mgr = ApiKeyManager(["KEY_ALPHA", "KEY_BETA"])
    # First call: builds client
    c1 = mgr.get_youtube_client("KEY_ALPHA")
    assert len(build_calls) == 1
    assert build_calls[0][2] == "KEY_ALPHA"

    # Second call for same key: must return CACHED client, no extra build call
    c2 = mgr.get_youtube_client("KEY_ALPHA")
    assert c1 is c2
    assert len(build_calls) == 1

    # Call for second key: builds client for KEY_BETA
    c3 = mgr.get_youtube_client("KEY_BETA")
    assert len(build_calls) == 2
    assert build_calls[1][2] == "KEY_BETA"

    # Removing KEY_ALPHA purges cache
    mgr.remove_key("KEY_ALPHA")
    assert "KEY_ALPHA" not in mgr._client_cache

