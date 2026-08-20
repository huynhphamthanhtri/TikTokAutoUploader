import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))

CONFIG_BASE = {
    "api_keys": [],
    "ngrok_port": 5000,
    "download_workers": 4,
    "max_video_minutes": 0,
    "auto_start": True,
    "cookies_file": "",
    "proxy_rotation": True,
    "concurrent_fragments": 8,
    "youtube_proxy_fallback": False,
    "youtube_cookie_policy": "fallback",
}

NETSCAPE_HEADER = "# Netscape HTTP Cookie File\n"
VALID_YT_COOKIE = (
    ".youtube.com\tTRUE\t/\tFALSE\t4102444800\tTEST\tsecretvalue\n"
    ".youtube.com\tTRUE\t/\tTRUE\t4102444800\tSID\tssidvalue\n"
)
EXPIRED_YT_COOKIE = ".youtube.com\tTRUE\t/\tTRUE\t1000000000\tSID\texpired\n"


def _write_cookie(tmpdir, content):
    p = Path(tmpdir) / "cookies.txt"
    p.write_text(content, encoding="utf-8")
    return str(p)


class TestFailureClassifier(unittest.TestCase):
    def test_real_403_from_activity_log(self):
        from youtube_monitor.core import _classify_failure, _classify_download_error
        err = Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        self.assertEqual(_classify_failure(err), "http_403")
        self.assertEqual(_classify_download_error(err), "retry")

    def test_403_plain(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("HTTP Error 403: Forbidden")
        self.assertEqual(_classify_failure(err), "http_403")

    def test_403_with_ansi(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("\x1b[31mERROR: unable to download video data: HTTP Error 403: Forbidden\x1b[0m")
        self.assertEqual(_classify_failure(err), "http_403")

    def test_403_not_proxy_transport(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("HTTP Error 403: Forbidden")
        self.assertNotEqual(_classify_failure(err), "proxy_transport")

    def test_members_only_permanent(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("ERROR: This video is members-only content")
        self.assertEqual(_classify_failure(err), "permanent")

    def test_private_permanent(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("ERROR: This video is private")
        self.assertEqual(_classify_failure(err), "permanent")

    def test_partial_read_retryable(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("ERROR: unable to download video data: unexpected end of stream")
        cls = _classify_failure(err)
        self.assertNotEqual(cls, "permanent")
        self.assertNotEqual(cls, "auth_required")

    def test_youtube_block(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("Sign in to confirm you're not a bot")
        self.assertEqual(_classify_failure(err), "youtube_block")

    def test_proxy_407(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("cannot connect to proxy 407")
        self.assertEqual(_classify_failure(err), "proxy_transport")

    def test_auth_required(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("ERROR: This video is only available to Music Premium members. Sign in to watch")
        self.assertEqual(_classify_failure(err), "auth_required")

    def test_format_unavailable(self):
        from youtube_monitor.core import _classify_failure
        err = Exception("ERROR: Requested format is not available")
        self.assertEqual(_classify_failure(err), "format_unavailable")

    def test_legacy_values(self):
        from youtube_monitor.core import _classify_download_error
        self.assertEqual(_classify_download_error(Exception("members-only content")), "permanent")
        self.assertEqual(_classify_download_error(Exception("HTTP Error 500: Internal Server Error")), "retry")
        self.assertEqual(_classify_download_error(Exception("Sign in to confirm you're not a bot")), "retry_block")
        self.assertEqual(_classify_download_error(Exception("cannot connect to proxy 407")), "retry_proxy")
        self.assertEqual(_classify_download_error(Exception("HTTP Error 403: Forbidden")), "retry")


class TestAttemptPlan(unittest.TestCase):
    def _cfg(self, **over):
        cfg = dict(CONFIG_BASE)
        cfg.update(over)
        return cfg

    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value=None)
    @patch("youtube_monitor.core._proxy_for_profile", return_value=None)
    @patch("youtube_monitor.core._ytdlp_alternate_client", return_value="")
    def test_first_is_direct(self, _alt, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan
        mock_cfg.return_value = self._cfg()
        attempts = _build_attempt_plan("P1")
        self.assertEqual(attempts[0].route, "direct")
        self.assertEqual(attempts[0].proxy, "")

    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value=None)
    @patch("youtube_monitor.core._proxy_for_profile", return_value=None)
    @patch("youtube_monitor.core._ytdlp_alternate_client", return_value="")
    def test_proxy_fallback_disabled_no_proxy_attempt(self, _alt, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan
        mock_cfg.return_value = self._cfg(youtube_proxy_fallback=False)
        attempts = _build_attempt_plan("P1")
        self.assertFalse(any(a.route == "proxy" for a in attempts))

    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value=None)
    @patch("youtube_monitor.core._proxy_for_profile", return_value="http://proxy.proxy:8080")
    @patch("youtube_monitor.core._ytdlp_alternate_client", return_value="")
    def test_proxy_fallback_enabled_uses_exact_profile_proxy(self, _alt, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan
        mock_cfg.return_value = self._cfg(youtube_proxy_fallback=True)
        attempts = _build_attempt_plan("P1")
        proxy_attempts = [a for a in attempts if a.route == "proxy"]
        self.assertEqual(len(proxy_attempts), 1)
        self.assertEqual(proxy_attempts[0].proxy, "http://proxy.proxy:8080")
        self.assertTrue(proxy_attempts[0].triggers)

    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value=None)
    @patch("youtube_monitor.core._proxy_for_profile", return_value=None)
    @patch("youtube_monitor.core._ytdlp_alternate_client", return_value="")
    def test_missing_profile_proxy_fail_closed(self, _alt, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan
        mock_cfg.return_value = self._cfg(youtube_proxy_fallback=True)
        attempts = _build_attempt_plan("P1")
        self.assertFalse(any(a.route == "proxy" for a in attempts))

    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value="c:/tmp/cookies.txt")
    @patch("youtube_monitor.core._proxy_for_profile", return_value=None)
    @patch("youtube_monitor.core._ytdlp_alternate_client", return_value="")
    def test_cookie_attempt_when_policy_allows(self, _alt, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan
        mock_cfg.return_value = self._cfg()
        attempts = _build_attempt_plan("P1")
        self.assertTrue(any(a.use_cookies for a in attempts))

    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value="c:/tmp/cookies.txt")
    @patch("youtube_monitor.core._proxy_for_profile", return_value=None)
    @patch("youtube_monitor.core._ytdlp_alternate_client", return_value="")
    def test_cookie_attempt_skipped_when_policy_never(self, _alt, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan
        mock_cfg.return_value = self._cfg(youtube_cookie_policy="never")
        attempts = _build_attempt_plan("P1")
        self.assertFalse(any(a.use_cookies for a in attempts))

    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value=None)
    @patch("youtube_monitor.core._proxy_for_profile", return_value=None)
    def test_alternate_client_skipped_when_unsupported(self, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan
        mock_cfg.return_value = self._cfg()
        with patch("youtube_monitor.core._ytdlp_alternate_client", return_value=""):
            attempts = _build_attempt_plan("P1")
        self.assertFalse(any(a.player_client for a in attempts))

    def test_runtime_alternate_client_is_supported(self):
        from youtube_monitor.core import _ytdlp_alternate_client
        client = _ytdlp_alternate_client()
        if client:
            from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
            self.assertIn(client, INNERTUBE_CLIENTS)


class TestSelectNextAttempt(unittest.TestCase):
    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value=None)
    @patch("youtube_monitor.core._proxy_for_profile", return_value=None)
    @patch("youtube_monitor.core._ytdlp_alternate_client", return_value="")
    def test_403_moves_to_alternate_format(self, _alt, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan, _select_next_attempt, FAILURE_HTTP_403
        mock_cfg.return_value = dict(CONFIG_BASE)
        attempts = _build_attempt_plan("P1")
        nxt = _select_next_attempt(attempts, 0, FAILURE_HTTP_403)
        self.assertIsNotNone(nxt)
        self.assertEqual(attempts[nxt].name, "direct-alt-format")

    @patch("youtube_monitor.core.get_config")
    @patch("youtube_monitor.core._resolve_cookies_file", return_value=None)
    @patch("youtube_monitor.core._proxy_for_profile", return_value=None)
    def test_auth_moves_to_cookies(self, _pfp, _cf, mock_cfg):
        from youtube_monitor.core import _build_attempt_plan, _select_next_attempt, FAILURE_AUTH_REQUIRED
        mock_cfg.return_value = dict(CONFIG_BASE)
        with patch("youtube_monitor.core._resolve_cookies_file", return_value="c:/tmp/c.txt"):
            with patch("youtube_monitor.core._ytdlp_alternate_client", return_value=""):
                attempts = _build_attempt_plan("P1")
        nxt = _select_next_attempt(attempts, 0, FAILURE_AUTH_REQUIRED)
        self.assertIsNotNone(nxt)
        self.assertTrue(attempts[nxt].use_cookies)

    def test_permanent_no_next(self):
        from youtube_monitor.core import _select_next_attempt, FAILURE_PERMANENT
        attempts = []
        self.assertIsNone(_select_next_attempt(attempts, 0, FAILURE_PERMANENT))


class TestCookieValidation(unittest.TestCase):
    def test_valid_netscape(self):
        from youtube_monitor.core import validate_youtube_cookie_file
        with tempfile.TemporaryDirectory() as tmp:
            ok, reason = validate_youtube_cookie_file(_write_cookie(tmp, NETSCAPE_HEADER + VALID_YT_COOKIE))
        self.assertTrue(ok)

    def test_empty(self):
        from youtube_monitor.core import validate_youtube_cookie_file
        with tempfile.TemporaryDirectory() as tmp:
            ok, reason = validate_youtube_cookie_file(_write_cookie(tmp, ""))
        self.assertFalse(ok)

    def test_header_only(self):
        from youtube_monitor.core import validate_youtube_cookie_file
        with tempfile.TemporaryDirectory() as tmp:
            ok, reason = validate_youtube_cookie_file(_write_cookie(tmp, NETSCAPE_HEADER))
        self.assertFalse(ok)

    def test_malformed(self):
        from youtube_monitor.core import validate_youtube_cookie_file
        with tempfile.TemporaryDirectory() as tmp:
            ok, reason = validate_youtube_cookie_file(_write_cookie(tmp, "this is not a cookie file at all\n"))
        self.assertFalse(ok)

    def test_non_youtube_domains(self):
        from youtube_monitor.core import validate_youtube_cookie_file
        with tempfile.TemporaryDirectory() as tmp:
            content = NETSCAPE_HEADER + ".example.com\tTRUE\t/\tFALSE\t4102444800\tX\ty\n"
            ok, reason = validate_youtube_cookie_file(_write_cookie(tmp, content))
        self.assertFalse(ok)

    def test_expired(self):
        from youtube_monitor.core import validate_youtube_cookie_file
        with tempfile.TemporaryDirectory() as tmp:
            ok, reason = validate_youtube_cookie_file(_write_cookie(tmp, NETSCAPE_HEADER + EXPIRED_YT_COOKIE))
        self.assertFalse(ok)

    def test_missing_file(self):
        from youtube_monitor.core import validate_youtube_cookie_file
        ok, reason = validate_youtube_cookie_file("")
        self.assertFalse(ok)
        with tempfile.TemporaryDirectory() as tmp:
            ok, reason = validate_youtube_cookie_file(str(Path(tmp) / "missing.txt"))
        self.assertFalse(ok)


class TestBuildYtdlpOpts(unittest.TestCase):
    def test_direct_proxy_empty(self):
        from youtube_monitor.core import _build_ytdlp_opts, YtdlpAttempt, FORMAT_FAST_720P
        base = {"format": FORMAT_FAST_720P, "outtmpl": "x", "extractor_args": {"youtube": {"skip": ["hls"]}}}
        opts = _build_ytdlp_opts(base, YtdlpAttempt("a", "direct", "", False, FORMAT_FAST_720P), tempfile.gettempdir())
        self.assertEqual(opts["proxy"], "")

    def test_direct_ignores_env_proxy(self):
        from youtube_monitor.core import _build_ytdlp_opts, YtdlpAttempt, FORMAT_FAST_720P
        base = {"format": FORMAT_FAST_720P, "outtmpl": "x", "extractor_args": {"youtube": {"skip": ["hls"]}}}
        opts = _build_ytdlp_opts(base, YtdlpAttempt("a", "direct", "", False, FORMAT_FAST_720P), tempfile.gettempdir())
        self.assertEqual(opts["proxy"], "")
        self.assertIn("proxy", opts)

    def test_proxy_attempt_url(self):
        from youtube_monitor.core import _build_ytdlp_opts, YtdlpAttempt, FORMAT_FAST_720P
        base = {"format": FORMAT_FAST_720P, "outtmpl": "x", "extractor_args": {"youtube": {"skip": ["hls"]}}}
        opts = _build_ytdlp_opts(base, YtdlpAttempt("a", "proxy", "http://p:8080", False, FORMAT_FAST_720P), tempfile.gettempdir())
        self.assertEqual(opts["proxy"], "http://p:8080")

    def test_cookies_not_attached_when_disallowed(self):
        from youtube_monitor.core import _build_ytdlp_opts, YtdlpAttempt, FORMAT_FAST_720P
        base = {"format": FORMAT_FAST_720P, "outtmpl": "x", "extractor_args": {"youtube": {"skip": ["hls"]}}, "cookies": "c:/tmp/c.txt"}
        opts = _build_ytdlp_opts(base, YtdlpAttempt("a", "direct", "", False, FORMAT_FAST_720P), tempfile.gettempdir())
        self.assertNotIn("cookies", opts)

    @patch("youtube_monitor.core._resolve_cookies_file", return_value="c:/tmp/c.txt")
    def test_cookies_attached_when_allowed(self, _cf):
        from youtube_monitor.core import _build_ytdlp_opts, YtdlpAttempt, FORMAT_FAST_720P
        base = {"format": FORMAT_FAST_720P, "outtmpl": "x", "extractor_args": {"youtube": {"skip": ["hls"]}}}
        opts = _build_ytdlp_opts(base, YtdlpAttempt("a", "direct", "", True, FORMAT_FAST_720P), tempfile.gettempdir())
        self.assertEqual(opts["cookies"], "c:/tmp/c.txt")

    def test_alt_client_extractor_args(self):
        from youtube_monitor.core import _build_ytdlp_opts, YtdlpAttempt, FORMAT_FAST_720P
        base = {"format": FORMAT_FAST_720P, "outtmpl": "x", "extractor_args": {"youtube": {"skip": ["hls"]}}}
        opts = _build_ytdlp_opts(base, YtdlpAttempt("a", "direct", "", False, FORMAT_FAST_720P, player_client="android_vr"), tempfile.gettempdir())
        self.assertEqual(opts["extractor_args"]["youtube"]["player_client"], ["android_vr"])
        self.assertEqual(opts["extractor_args"]["youtube"]["skip"], ["hls"])


class TestDownloadOneChain(unittest.TestCase):
    def _meta(self, folder, profile=""):
        return {"folder": folder, "profile_name": profile, "process_short": False}

    def _patches(self, folder, meta_profile="", config=None, cookies=None):
        cfg = dict(CONFIG_BASE)
        cfg.update(config or {})
        p = patch.multiple(
            "youtube_monitor.core",
            _claim_download=MagicMock(return_value=True),
            channels_store=MagicMock(get_meta=MagicMock(return_value=self._meta(folder, meta_profile)), mark_seen_only=MagicMock()),
            get_config=MagicMock(return_value=cfg),
            _resolve_cookies_file=MagicMock(return_value=cookies),
            _ytdlp_alternate_client=MagicMock(return_value=""),
            _proxy_for_profile=MagicMock(return_value=None),
            append_activity=MagicMock(),
            append_csv_log=MagicMock(),
            remember_download=MagicMock(),
        )
        return p

    def test_403_then_alternate_format_success(self):
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                def fake_run(video_id, url, opts):
                    if getattr(fake_run, "n", 0) == 0:
                        fake_run.n = 1
                        raise Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")
                    d = os.path.dirname(opts["outtmpl"])
                    path = os.path.join(d, "ok.mp4")
                    with open(path, "wb") as f:
                        f.write(b"x")
                    return {"title": "T", "duration": 30}, path, ""
                fake_run.n = 0
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=fake_run):
                    with patch("youtube_monitor.core._finalize_video", side_effect=lambda p, o, t, v: p):
                        outcome = _download_one_result("c", "v")
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.attempts_used, 2)

    def test_403_exhausted_is_retryable(self):
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")):
                    outcome = _download_one_result("c", "v")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.retryable)
        self.assertFalse(outcome.permanent)
        self.assertEqual(outcome.failure_class, "http_403")
        self.assertGreaterEqual(outcome.attempts_used, 2)

    def test_members_only_permanent(self):
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=Exception("ERROR: This video is members-only content")):
                    outcome = _download_one_result("c", "v")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.permanent)
        self.assertFalse(outcome.retryable)
        self.assertEqual(outcome.failure_class, "permanent")

    def test_auth_required_not_retryable(self):
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=Exception("ERROR: This video requires you to sign in to watch")):
                    outcome = _download_one_result("c", "v")
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.permanent)
        self.assertFalse(outcome.retryable)

    def test_auth_required_clears_pending_not_seen(self):
        import youtube_monitor.core as core
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            core._try_pending("c", "v")
            with self._patches(folder) as patched:
                store_mock = core.channels_store
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=Exception("ERROR: This video requires you to sign in to watch")):
                    outcome = _download_one_result("c", "v")
                self.assertFalse(core._is_pending("c", "v"))
                store_mock.mark_seen_only.assert_not_called()
        with core._retry_lock:
            core._retry_after.pop("c:v:attempt", None)

    def test_duration_limit_skip(self):
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder, config={"max_video_minutes": 1}) as patched:
                with patch("youtube_monitor.core._run_ytdlp_download", return_value=(None, None, "duration > giới hạn 1 phút")):
                    outcome = _download_one_result("c", "v")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.permanent)
        self.assertEqual(outcome.failure_class, "skipped")

    def test_busy_not_retryable(self):
        from youtube_monitor.core import _download_one_result
        with patch("youtube_monitor.core._claim_download", return_value=False):
            outcome = _download_one_result("c", "v")
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.retryable)
        self.assertEqual(outcome.failure_class, "busy")

    def test_info_but_no_file_fails_permanent(self):
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                with patch("youtube_monitor.core._run_ytdlp_download", return_value=({"title": "T", "duration": 30}, "", "")):
                    outcome = _download_one_result("c", "v")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.permanent)

    def test_staging_cleaned_after_failure(self):
        import youtube_monitor.core as core
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")):
                    outcome = _download_one_result("c", "v")
            tmp_root = Path(folder).parent / ".youtube_tmp"
            self.assertTrue(tmp_root.exists())
            self.assertEqual([p.name for p in tmp_root.iterdir()], [".ydl_cache"])

    def test_no_stale_part_selected(self):
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                def fake_run(video_id, url, opts):
                    d = os.path.dirname(opts["outtmpl"])
                    if getattr(fake_run, "n", 0) == 0:
                        fake_run.n = 1
                        with open(os.path.join(d, "direct.part"), "wb") as f:
                            f.write(b"partial")
                        raise Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")
                    path = os.path.join(d, "ok.mp4")
                    with open(path, "wb") as f:
                        f.write(b"x")
                    return {"title": "T", "duration": 30}, path, ""
                fake_run.n = 0
                finalized = []
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=fake_run):
                    with patch("youtube_monitor.core._finalize_video", side_effect=lambda p, o, t, v: (finalized.append(p) or p)):
                        outcome = _download_one_result("c", "v")
        self.assertTrue(outcome.ok)
        self.assertTrue(finalized)
        self.assertNotIn("attempt-01", finalized[0])
        self.assertIn("attempt-02", finalized[0])

    def test_single_terminal_activity_status(self):
        import youtube_monitor.core as core
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")):
                    outcome = _download_one_result("c", "v")
                calls = core.append_activity.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs.get("status"), "fail")

    def test_success_appends_one_success_activity(self):
        import youtube_monitor.core as core
        from youtube_monitor.core import _download_one_result
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder) as patched:
                def fake_run(video_id, url, opts):
                    d = os.path.dirname(opts["outtmpl"])
                    path = os.path.join(d, "ok.mp4")
                    with open(path, "wb") as f:
                        f.write(b"x")
                    return {"title": "T", "duration": 30}, path, ""
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=fake_run):
                    with patch("youtube_monitor.core._finalize_video", side_effect=lambda p, o, t, v: p):
                        outcome = _download_one_result("c", "v")
                calls = core.append_activity.call_args_list
        self.assertTrue(outcome.ok)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs.get("status"), "success")

    def test_no_secret_in_logs_on_failure(self):
        from youtube_monitor.core import _download_one_result, log_queue
        with tempfile.TemporaryDirectory() as folder:
            cookie_path = _write_cookie(folder, NETSCAPE_HEADER + VALID_YT_COOKIE)
            while not log_queue.empty():
                log_queue.get_nowait()
            with self._patches(folder, cookies=cookie_path) as patched:
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")):
                    outcome = _download_one_result("c", "v")
            logs = []
            while not log_queue.empty():
                logs.append(log_queue.get_nowait())
        secret_name = Path(cookie_path).name
        for line in logs:
            self.assertNotIn(secret_name, line)
            self.assertNotIn("secretvalue", line)

    def test_success_fastpath_skipped_without_profile(self):
        from youtube_monitor.core import _download_one_result
        import youtube_monitor.core as core
        with tempfile.TemporaryDirectory() as folder:
            with self._patches(folder, meta_profile="") as patched:
                def fake_run(video_id, url, opts):
                    d = os.path.dirname(opts["outtmpl"])
                    path = os.path.join(d, "ok.mp4")
                    with open(path, "wb") as f:
                        f.write(b"x")
                    return {"title": "T", "duration": 30}, path, ""
                with patch("youtube_monitor.core._run_ytdlp_download", side_effect=fake_run):
                    with patch("youtube_monitor.core._finalize_video", side_effect=lambda p, o, t, v: p):
                        mock_main = MagicMock()
                        with patch.dict("sys.modules", {"main": mock_main}):
                            outcome = _download_one_result("c", "v")
        self.assertTrue(outcome.ok)
        mock_main.enqueue_video.assert_not_called()


class TestWorkerRetryDecisions(unittest.TestCase):
    def _worker_env(self):
        import youtube_monitor.core as core
        core._retry_after.clear()
        core._pending_video_ids.clear()
        while not core.download_queue.empty():
            try:
                core.download_queue.get_nowait()
            except Exception:
                break
        store = MagicMock()
        store.get_meta.return_value = {"folder": tempfile.gettempdir(), "profile_name": "", "seen": set()}
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]
        p = patch.multiple(
            "youtube_monitor.core",
            channels_store=store,
            _download_one_result=MagicMock(),
            stop_event=stop,
        )
        return p

    def test_retryable_schedules_outer_retry(self):
        import youtube_monitor.core as core
        with self._worker_env() as patched:
            outcome = MagicMock(ok=False, retryable=True, permanent=False, failure_class="http_403")
            core._download_one_result.return_value = outcome
            core._try_pending("c", "v")
            core.download_queue.put(("c", "v", None, None))
            core.worker_main(1)
        with core._retry_lock:
            self.assertEqual(core._retry_after.get("c:v:attempt"), 1)

    def test_permanent_no_retry(self):
        import youtube_monitor.core as core
        with self._worker_env() as patched:
            outcome = MagicMock(ok=False, retryable=False, permanent=True, failure_class="permanent")
            core._download_one_result.return_value = outcome
            core._try_pending("c", "v")
            core.download_queue.put(("c", "v", None, None))
            core.worker_main(1)
        with core._retry_lock:
            self.assertIsNone(core._retry_after.get("c:v:attempt"))


class TestBatchResult(unittest.TestCase):
    @patch("youtube_monitor.core.get_config", return_value=dict(CONFIG_BASE))
    def test_zero_of_n_returns_false(self, _cfg):
        from youtube_monitor.core import batch_download_latest
        with tempfile.TemporaryDirectory() as folder:
            with patch("youtube_monitor.core.find_latest_video", return_value=("vid1", "title")):
                with patch("youtube_monitor.core.download_one", return_value=False):
                    ok, msg = batch_download_latest(["link1"], folder)
        self.assertFalse(ok)
        self.assertIn("0/1", msg)

    @patch("youtube_monitor.core.get_config", return_value=dict(CONFIG_BASE))
    def test_partial_returns_false(self, _cfg):
        from youtube_monitor.core import batch_download_latest
        with tempfile.TemporaryDirectory() as folder:
            with patch("youtube_monitor.core.find_latest_video", return_value=("vid1", "title")):
                with patch("youtube_monitor.core.download_one", return_value=False):
                    ok, msg = batch_download_latest(["link1", "link2"], folder)
        self.assertFalse(ok)
        self.assertIn("0/2", msg)

    @patch("youtube_monitor.core.get_config", return_value=dict(CONFIG_BASE))
    def test_all_ok_returns_true(self, _cfg):
        from youtube_monitor.core import batch_download_latest
        with tempfile.TemporaryDirectory() as folder:
            with patch("youtube_monitor.core.find_latest_video", return_value=("vid1", "title")):
                with patch("youtube_monitor.core.download_one", return_value=True):
                    ok, msg = batch_download_latest(["link1"], folder)
        self.assertTrue(ok)
        self.assertIn("1/1", msg)


class TestFormatSelectorDASHPriority(unittest.TestCase):
    @staticmethod
    def _formats():
        def fmt(fid, vcodec="none", acodec="none", height=None):
            f = {"format_id": fid, "ext": "mp4", "vcodec": vcodec, "acodec": acodec, "url": f"http://x/{fid}"}
            if height:
                f["height"] = height
            return f
        return [
            fmt("18", vcodec="avc1.42001E", acodec="mp4a.40.2", height=360),
            fmt("134", vcodec="avc1.4d401e", height=360),
            fmt("135", vcodec="avc1.4d401f", height=480),
            fmt("298", vcodec="avc1.4d4020", height=720),
            fmt("139", acodec="mp4a.40.5"),
            fmt("140", acodec="mp4a.40.2"),
        ]

    def test_format_constants_are_dash_first(self):
        from youtube_monitor.core import FORMAT_FAST_720P, FORMAT_COMPAT_720P
        for spec in (FORMAT_FAST_720P, FORMAT_COMPAT_720P):
            self.assertTrue(spec.startswith("bv["), spec)
            self.assertIn("+ba", spec)

    def test_fast_selector_prefers_dash_over_progressive(self):
        from yt_dlp import YoutubeDL
        from youtube_monitor.core import FORMAT_FAST_720P
        ydl = YoutubeDL({"quiet": True})
        res = list(ydl.build_format_selector(FORMAT_FAST_720P)({"id": "v", "formats": self._formats(), "ext": "mp4"}))[0]
        self.assertNotEqual(res["format_id"], "18")
        self.assertGreaterEqual(len(res.get("requested_formats", [])), 2)
        for rf in res.get("requested_formats", []):
            self.assertIn(rf["format_id"], ("134", "135", "298", "139", "140"))

    def test_compat_selector_prefers_dash_over_progressive(self):
        from yt_dlp import YoutubeDL
        from youtube_monitor.core import FORMAT_COMPAT_720P
        ydl = YoutubeDL({"quiet": True})
        res = list(ydl.build_format_selector(FORMAT_COMPAT_720P)({"id": "v", "formats": self._formats(), "ext": "mp4"}))[0]
        self.assertNotEqual(res["format_id"], "18")

    def test_selector_falls_back_to_progressive_when_no_dash(self):
        from yt_dlp import YoutubeDL
        from youtube_monitor.core import FORMAT_FAST_720P
        only_progressive = [f for f in self._formats() if f["format_id"] == "18"]
        ydl = YoutubeDL({"quiet": True})
        res = list(ydl.build_format_selector(FORMAT_FAST_720P)({"id": "v", "formats": only_progressive, "ext": "mp4"}))[0]
        self.assertEqual(res["format_id"], "18")


class TestGetStatusCookieState(unittest.TestCase):
    @patch("youtube_monitor.core._resolve_cookies_file", return_value=None)
    def test_missing_cookie_status(self, _cf):
        from youtube_monitor.core import get_status
        with patch("youtube_monitor.core.validate_youtube_cookie_file") as _v:
            status = get_status()
        self.assertEqual(status["cookies_set"], False)
        self.assertEqual(status["cookies_status"], "missing")
        _v.assert_not_called()

    @patch("youtube_monitor.core._resolve_cookies_file", return_value="c:/tmp/c.txt")
    @patch("youtube_monitor.core.validate_youtube_cookie_file", return_value=(True, "ok"))
    def test_ok_cookie_status(self, _v, _cf):
        from youtube_monitor.core import get_status
        status = get_status()
        self.assertEqual(status["cookies_status"], "ok")

    @patch("youtube_monitor.core._resolve_cookies_file", return_value="c:/tmp/c.txt")
    @patch("youtube_monitor.core.validate_youtube_cookie_file", return_value=(False, "bad"))
    def test_invalid_cookie_status(self, _v, _cf):
        from youtube_monitor.core import get_status
        status = get_status()
        self.assertEqual(status["cookies_status"], "invalid")


if __name__ == "__main__":
    unittest.main()