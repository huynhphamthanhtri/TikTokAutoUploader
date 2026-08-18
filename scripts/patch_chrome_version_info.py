"""
scripts/patch_chrome_version_info.py - Update PE branding strings (VS_VERSION_INFO)
of a browser engine's chrome.exe/chrome.dll to DONGLAO, on a STAGING copy only.

Usage:
    python scripts/patch_chrome_version_info.py --source Browser --staging <tmp-dir>

Only same-length UTF-16LE strings are replaced; see
``scripts/browser_engine_patcher.py`` for the safety rules.
"""

import sys
from pathlib import Path

from scripts.browser_engine_patcher import (
    make_staging_copy,
    parse_common_args,
    patch_branding,
)

DEFAULT_ENGINE = "donglao-browser-144"


def main(argv=None) -> int:
    parser = parse_common_args(
        argv,
        "Patch chrome.exe/chrome.dll branding (VS_VERSION_INFO) on a staging copy.",
    )
    parser.add_argument("--engine", default=DEFAULT_ENGINE, help="engine directory name to patch")
    args = parser.parse_args(argv)
    engine_name = args.engine
    source_engine = Path(args.source).resolve() / engine_name
    staging_root = Path(args.staging).resolve()

    if not source_engine.exists():
        print(f"[FAIL] engine not found in source: {source_engine}")
        return 1

    print(f"=== Staging {engine_name} -> {staging_root / engine_name} ===")
    staging_engine = make_staging_copy(source_engine, staging_root / engine_name)

    targets = [
        staging_engine / "chrome.exe",
        staging_engine / "144.0.7559.96" / "chrome.dll",
    ]
    failed = False
    for target in targets:
        if not target.exists():
            print(f"[SKIP] missing: {target}")
            continue
        report = patch_branding(target)
        counts = [p["count"] for p in report["pairs"]]
        print(f"  {target.name:12s}: applied={report['applied']} counts={counts}")
        if not report["applied"]:
            missing = [p["source"] for p in report["pairs"] if p["count"] == 0]
            print(f"  [FAIL] no source branding strings found ({missing})")
            failed = True

    if failed:
        print("\n[FAILED] Branding could not be applied.")
        return 1
    print(f"\n[SUCCESS] Branding applied on staging copy: {staging_engine}")
    return 0


if __name__ == "__main__":
    sys.exit(main())