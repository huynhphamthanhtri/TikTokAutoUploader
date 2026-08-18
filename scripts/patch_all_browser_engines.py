"""
scripts/patch_all_browser_engines.py - Patch browser engines with DONGLAO branding
and the 6-NOP license bypass, on a STAGING copy only.

Usage:
    python scripts/patch_all_browser_engines.py --source Browser --staging <tmp-dir>

The pristine ``Browser/`` tree in the workspace is never modified; the script
copies each engine into ``--staging`` and patches there. See
``scripts/browser_engine_patcher.py`` for the safety rules.
"""

import sys
from pathlib import Path

from scripts.browser_engine_patcher import (
    MIN_DLL_SIZE,
    _verify_engine_report,
    ensure_staging_outside,
    make_staging_copy,
    parse_common_args,
    patch_engine_dir,
)

DEFAULT_ENGINES = ("donglao-browser-144", "orbita-browser-144")


def main(argv=None) -> int:
    parser = parse_common_args(
        argv,
        "Patch Dong Lao browser engines (license bypass + branding) on a staging copy.",
    )
    parser.add_argument(
        "--engines",
        default=",".join(DEFAULT_ENGINES),
        help="comma-separated engine directory names to patch",
    )
    parser.add_argument(
        "--min-dll-size",
        type=int,
        default=MIN_DLL_SIZE,
        help="minimum chrome.dll size in bytes (0 disables the guard)",
    )
    args = parser.parse_args(argv)
    source_root = Path(args.source).resolve()
    staging_root = Path(args.staging).resolve()
    ensure_staging_outside(source_root, staging_root)
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]

    failed = False
    for name in engines:
        source_engine = source_root / name
        if not source_engine.exists():
            print(f"[SKIP] engine not found in source: {source_engine}")
            continue
        print(f"\n=== Staging {name} -> {staging_root / name} ===")
        staging_engine = make_staging_copy(source_engine, staging_root / name)
        report = patch_engine_dir(staging_engine, allowed_root=staging_root, min_dll_size=args.min_dll_size)
        problems = _verify_engine_report(report)
        print(f"  license state : {report.get('license', {}).get('state')}")
        branding = report.get("branding") or {}
        for which, item in branding.items():
            if item:
                print(f"  {which:12s}: applied={item['applied']} counts={[p['count'] for p in item['pairs']]}")
        if problems:
            failed = True
            for problem in problems:
                print(f"  [FAIL] {problem}")
        else:
            print(f"  [OK] {name} staged and patched at {staging_engine}")

    if failed:
        print("\n[FAILED] One or more engines could not be patched.")
        return 1
    print("\n[SUCCESS] All engines staged and patched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())