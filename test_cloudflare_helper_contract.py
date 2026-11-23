# -*- coding: utf-8 -*-
"""Regression tests for cloudflare_helper.ensure_cloudflared contract with main.py and cloudflare_owner.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from youtube_monitor import cloudflare_helper as ch


def test_ensure_cloudflared_supports_progress_callback_and_status_callback(tmp_path):
    target_bin = tmp_path / "bin" / "cloudflared.exe"
    fake_payload = b"MZ" + b"\x00" * (16 * 1024 * 1024)  # > 15MB valid MZ stub

    fake_resp = MagicMock()
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.headers = {"content-length": str(len(fake_payload))}
    fake_resp.iter_content.return_value = [fake_payload]

    progress_events = []
    status_events = []

    def _on_progress(text, pct):
        progress_events.append((text, pct))

    def _on_status(text):
        status_events.append(text)

    with patch.object(ch, "find_cloudflared", return_value=None), \
         patch.object(ch, "get_default_bin_path", return_value=target_bin), \
         patch("youtube_monitor.cloudflare_helper.requests.get", return_value=fake_resp):
        res_path = ch.ensure_cloudflared(
            status_callback=_on_status,
            progress_callback=_on_progress,
        )

    assert res_path == target_bin
    assert target_bin.exists()
    assert len(progress_events) >= 2
    assert progress_events[-1][1] == 1.0
    assert len(status_events) >= 2


def test_ensure_cloudflared_when_already_exists_fires_progress_1(tmp_path):
    existing = tmp_path / "cloudflared.exe"
    existing.write_bytes(b"MZ" + b"\x00" * (11 * 1024 * 1024))

    events = []
    res = ch.ensure_cloudflared(progress_callback=lambda t, p: events.append((t, p)))
    # If find_cloudflared finds system binary or patched binary
    with patch.object(ch, "find_cloudflared", return_value=existing):
        events.clear()
        res2 = ch.ensure_cloudflared(progress_callback=lambda t, p: events.append((t, p)))
        assert res2 == existing
        assert events[-1][1] == 1.0
