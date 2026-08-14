"""Standalone driver for the automated login environment runner.

Runs completely without the UI so the automatic proxy rotation + login fix can
be triggered directly:

    python run_login_diagnostic.py --profile "BKT_TUONG 7"

Proxy source order:
  1. ``--proxy IP:PORT:USER:PASS`` arguments (repeatable),
  2. the profile's ``login_test_proxies`` config field,
  3. the profile's current ``proxy_string``.

A timestamped backup of ``configs.json`` is created before the run unless
``--no-backup`` is passed. Promotions (if any) are written back atomically by
the runner; rollback restores the snapshot automatically.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import proxy_diagnostics as diag


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def main():
    parser = argparse.ArgumentParser(description="Automated login environment runner")
    parser.add_argument("--profile", default="BKT_TUONG 7", help="profile name in configs.json")
    parser.add_argument("--proxy", action="append", dest="proxies", help="IP:PORT:USER:PASS")
    parser.add_argument("--no-backup", action="store_true", help="skip configs.json backup")
    args = parser.parse_args()

    config_path = Path(__file__).resolve().parent / "configs.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles", payload)
    if args.profile not in profiles:
        sys.exit("Khong tim thay profile: %s" % args.profile)
    entry = profiles[args.profile]

    proxies = list(args.proxies) if args.proxies else []
    if not proxies:
        candidates = list(entry.get("login_test_proxies", []) or [])
        if entry.get("proxy_string"):
            candidates.append(str(entry.get("proxy_string")))
        seen = set()
        for proxy in candidates:
            label = diag.proxy_label(proxy)
            if label and label not in seen:
                seen.add(label)
                proxies.append(proxy)

    if not proxies:
        sys.exit("Khong co proxy nao de test; dung --proxy hoac config login_test_proxies")

    if not args.no_backup:
        backup = config_path.with_name("configs.json.login-backup-%s.json" % _timestamp())
        shutil.copy2(config_path, backup)
        print("BACKUP=%s" % backup, flush=True)

    from login_environment_runner import LoginEnvironmentRunner

    runner = LoginEnvironmentRunner(
        profile_name=args.profile,
        config=entry,
        proxies=proxies,
        status_callback=lambda message: print(message, flush=True),
    )
    report = runner.run()
    print("LOGIN_TEST_RESULT=" + json.dumps(report, ensure_ascii=True), flush=True)
    return 0 if report.get("overall") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
