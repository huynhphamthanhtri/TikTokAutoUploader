import json
import tempfile
import unittest
from pathlib import Path

import login_environment_runner as runner


def _probe(label, ok=True, latency=100, exit_ip="5.6.7.8", timezone="Asia/Tokyo", cc="JP", proxy="1.2.3.4:8080:u:p"):
    return {
        "label": label,
        "ok": ok,
        "exit_ip": exit_ip,
        "latency_ms": latency,
        "geo": {"timezone": timezone, "country_code": cc, "latitude": 35.68, "longitude": 139.76},
        "error": "",
        "proxy": proxy,
    }


class LocaleTests(unittest.TestCase):
    def test_country_mapping(self):
        self.assertEqual(runner.locale_for_country("JP"), "ja-JP")
        self.assertEqual(runner.locale_for_country("us"), "en-US")
        self.assertEqual(runner.locale_for_country("SG"), "en-SG")
        self.assertEqual(runner.locale_for_country(""), "en-US")
        self.assertEqual(runner.locale_for_country("ZZ"), "en-US")


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_matches_probe(self):
        fp = runner.build_fingerprint_for_probe(_probe("jp"), "1.2.3.4:8080:u:p")
        self.assertEqual(fp["lang"], "ja-JP")
        self.assertEqual(fp["timezone"], "Asia/Tokyo")
        self.assertEqual(fp["geo_exit_ip"], "5.6.7.8")
        self.assertEqual(fp["geolocation"]["latitude"], 35.68)
        self.assertEqual(fp["geo_proxy_hash"], runner.proxy_cache_key({"ip": "1.2.3.4", "port": "8080", "user": "u", "pass": "p"}))
        self.assertEqual(fp["device_preset"], "desktop")

    def test_fingerprint_locale_follows_country(self):
        fp = runner.build_fingerprint_for_probe(_probe("us", cc="US"), "p")
        self.assertEqual(fp["lang"], "en-US")


class PlanCandidatesTests(unittest.TestCase):
    def test_orders_by_latency_and_drops_failures(self):
        probes = [
            _probe("slow", latency=900),
            _probe("bad", ok=False),
            _probe("fast", latency=80),
        ]
        planned = runner.plan_candidates(probes)
        self.assertEqual([c["label"] for c in planned], ["fast", "slow"])

    def test_drops_missing_exit_ip(self):
        probes = [_probe("no-ip", exit_ip="")]
        self.assertEqual(runner.plan_candidates(probes), [])


class ClassificationTests(unittest.TestCase):
    def test_captcha_wins_over_other_signals(self):
        signals = {"has_captcha": True, "has_internal_error": True, "has_credential_error": True}
        self.assertEqual(runner.classify_login_signals(signals), runner.CAPTCHA)

    def test_checkpoint_before_credentials(self):
        self.assertEqual(
            runner.classify_login_signals({"has_checkpoint": True, "has_credential_error": True}),
            runner.CHECKPOINT,
        )

    def test_credential_rejected(self):
        self.assertEqual(
            runner.classify_login_signals({"has_credential_error": True}), runner.CREDENTIAL_REJECTED
        )

    def test_internal_error(self):
        self.assertEqual(
            runner.classify_login_signals({"has_internal_error": True}), runner.INTERNAL_SERVER_ERROR
        )

    def test_login_hub_is_internal_error(self):
        url = "https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Ftiktokstudio&enter_method=redirect"
        body = "Log in to TikTok\nQR code\nUse phone number\nContinue with Google"
        signals = runner.scan_login_signals(body, url)
        self.assertTrue(signals["has_login_hub"])
        self.assertEqual(runner.classify_login_signals(signals), runner.INTERNAL_SERVER_ERROR)

    def test_login_hub_requires_both_url_and_text(self):
        url = "https://www.tiktok.com/login?redirect_url=studio"
        signals = runner.scan_login_signals("nothing relevant", url)
        self.assertFalse(signals["has_login_hub"])
        signals = runner.scan_login_signals("Use QR code", "https://www.tiktok.com/login")
        self.assertFalse(signals["has_login_hub"])

    def test_no_signals_is_none(self):
        self.assertIsNone(runner.classify_login_signals({}))
        self.assertIsNone(runner.classify_login_signals(None))

    def test_scan_finds_text(self):
        signals = runner.scan_login_signals("Internal server error happened", "")
        self.assertTrue(signals["has_internal_error"])
        signals = runner.scan_login_signals("prove you are human", "")
        self.assertTrue(signals["has_captcha"])
        signals = runner.scan_login_signals("checkpoint required to continue", "")
        self.assertTrue(signals["has_checkpoint"])
        signals = runner.scan_login_signals("incorrect password", "")
        self.assertTrue(signals["has_credential_error"])
        self.assertFalse(runner.scan_login_signals("all good", "")["has_internal_error"])


class SnapshotTests(unittest.TestCase):
    def test_snapshot_and_restore(self):
        config = {
            "proxy_string": "old:1:u:p",
            "proxy_type": "http",
            "use_proxy": True,
            "fingerprint": {"lang": "en-US"},
            "browser_profile_path": "X",
            "session_auth_state": "expired",
            "session_verified_proxy_key": "k",
        }
        snapshot = runner.snapshot_config(config)
        config["proxy_string"] = "new:2:u:p"
        config["use_proxy"] = False
        config["browser_profile_path"] = "Y"
        runner.restore_config(config, snapshot)
        self.assertEqual(config["proxy_string"], "old:1:u:p")
        self.assertTrue(config["use_proxy"])
        self.assertEqual(config["browser_profile_path"], "X")
        self.assertEqual(config["session_auth_state"], "expired")

    def test_snapshot_covers_all_touched_fields(self):
        config = {}
        snapshot = runner.snapshot_config(config)
        for key in runner.RESTORED_CONFIG_FIELDS:
            self.assertIn(key, snapshot)


class RedactTests(unittest.TestCase):
    def test_report_never_exposes_proxy_credentials(self):
        report = {
            "candidates": [
                {"label": "1.2.3.4:8080", "proxy": "1.2.3.4:8080:secretuser:secretpass",
                 "proxy_string": "1.2.3.4:8080:secretuser:secretpass", "exit_ip": "9.9.9.9"},
            ]
        }
        redacted = runner.redact_report(report)
        text = json.dumps(redacted)
        self.assertNotIn("secretuser", text)
        self.assertNotIn("secretpass", text)
        self.assertEqual(redacted["candidates"][0]["label"], "1.2.3.4:8080")

    def test_build_report_shape(self):
        candidate = dict(_probe("p"))
        candidate["outcome"] = "internal_server_error"
        report = runner.build_report("A", "FAIL", "no proxy", [candidate], False)
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(report["candidates"][0]["label"], "p")
        self.assertNotIn("proxy", report["candidates"][0])


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "Auto_Data" / "acct"
        self.root.mkdir(parents=True)
        self.official = self.root / "Profile-Patchright"
        self.official.mkdir()
        (self.official / "data").write_text("x", encoding="ascii")
        self.quarantine_dir = self.root / "LoginTests" / "quarantine"
        self.quarantine_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_rollback_restores_quarantined_official(self):
        quarantine = self.quarantine_dir / "Profile-Patchright-r1"
        os_replace = __import__("os").replace
        os_replace(str(self.official), str(quarantine))
        fresh = self.root / "Profile-Patchright"
        fresh.mkdir()
        (fresh / "new").write_text("n", encoding="ascii")
        record = {
            "status": "promoting",
            "old_profile_path": str(self.official),
            "quarantine_path": str(quarantine),
            "official_path": str(fresh),
        }
        runner.rollback_transaction(record)
        self.assertTrue(self.official.is_dir())
        self.assertEqual((self.official / "data").read_text(encoding="ascii"), "x")
        self.assertFalse((self.official / "new").exists())

    def test_recover_cleans_up_and_restores_config(self):
        quarantine = self.quarantine_dir / "Profile-Patchright-r2"
        os_replace = __import__("os").replace
        os_replace(str(self.official), str(quarantine))
        fresh = self.root / "Profile-Patchright"
        fresh.mkdir()
        tests_dir = self.root / "LoginTests"
        record = {
            "status": "promoting",
            "old_profile_path": str(self.official),
            "quarantine_path": str(quarantine),
            "official_path": str(fresh),
            "config_snapshot": {"use_proxy": True},
        }
        (tests_dir / "transaction-abc.json").write_text(json.dumps(record), encoding="utf-8")
        config = {"use_proxy": False}
        recovered = runner.recover_interrupted_transaction(self.root, config, profile_name="acct")
        self.assertEqual(recovered, ["transaction-abc.json"])
        self.assertTrue(self.official.is_dir())
        self.assertEqual((self.official / "data").read_text(encoding="ascii"), "x")
        self.assertFalse((self.official / "new").exists())
        self.assertTrue(config["use_proxy"])
        self.assertFalse((tests_dir / "transaction-abc.json").exists())


class OutcomeTaxonomyTests(unittest.TestCase):
    def test_stop_and_retryable_sets_are_disjoint(self):
        self.assertEqual(runner.STOP_ON_FIRST & runner.RETRYABLE, set())

    def test_stop_set_contains_risk_outcomes(self):
        self.assertEqual(runner.STOP_ON_FIRST, {runner.CAPTCHA, runner.CHECKPOINT, runner.CREDENTIAL_REJECTED})


if __name__ == "__main__":
    unittest.main()
