import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


DEFAULT_LOG = Path("upload_benchmarks.jsonl")


def _mean(values):
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return round(statistics.mean(nums), 3)


def _fmt(value):
    return "n/a" if value is None else f"{value:.3f}s"


def _driver_mode(row):
    meta = row.get("meta", {}) or {}
    mode = meta.get("driver_mode")
    if mode in ("cold", "warm"):
        return mode
    reused = meta.get("driver_reused_actual")
    if reused is None:
        reused = meta.get("driver_reused_before")
    return "warm" if reused else "cold"


def _phase_mean(records, key):
    return _mean([r.get("phases", {}).get(key) for r in records])


def _build_bucket_summary(records):
    return {
        "runs": len(records),
        "success_runs": sum(1 for r in records if r.get("success")),
        "avg_total": _mean([r.get("total_seconds") for r in records]),
        "avg_driver": _phase_mean(records, "ensure_driver_seconds"),
        "avg_ready": _phase_mean(records, "wait_until_ready_seconds"),
        "avg_post": _phase_mean(records, "post_click_latency_seconds"),
    }


def _print_bucket(label, summary):
    print(
        f"{label:<12}: runs={summary['runs']}, success={summary['success_runs']}, "
        f"avg_total={_fmt(summary['avg_total'])}, avg_driver={_fmt(summary['avg_driver'])}, "
        f"avg_ready={_fmt(summary['avg_ready'])}, avg_post={_fmt(summary['avg_post'])}"
    )


def load_records(path: Path):
    if not path.exists():
        return []

    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"[warn] Bỏ qua dòng JSON lỗi tại #{line_no}")
    return rows


def build_summary(records):
    overall = {
        "runs": len(records),
        "success_runs": sum(1 for r in records if r.get("success")),
        "failed_runs": sum(1 for r in records if not r.get("success")),
        "avg_total": _mean([r.get("total_seconds") for r in records]),
        "avg_driver": _mean([r.get("phases", {}).get("ensure_driver_seconds") for r in records]),
        "avg_ready": _mean([r.get("phases", {}).get("wait_until_ready_seconds") for r in records]),
        "avg_post": _mean([r.get("phases", {}).get("post_click_latency_seconds") for r in records]),
    }

    cold_records = [r for r in records if _driver_mode(r) == "cold"]
    warm_records = [r for r in records if _driver_mode(r) == "warm"]
    overall["cold"] = _build_bucket_summary(cold_records)
    overall["warm"] = _build_bucket_summary(warm_records)

    by_profile = defaultdict(list)
    for row in records:
        by_profile[row.get("profile_name", "<unknown>")].append(row)

    profile_rows = []
    for profile_name, items in sorted(by_profile.items()):
        success_items = [r for r in items if r.get("success")]
        cold_items = [r for r in items if _driver_mode(r) == "cold"]
        warm_items = [r for r in items if _driver_mode(r) == "warm"]

        profile_rows.append({
            "profile_name": profile_name,
            "runs": len(items),
            "success_runs": len(success_items),
            "avg_total": _mean([r.get("total_seconds") for r in items]),
            "avg_driver": _mean([r.get("phases", {}).get("ensure_driver_seconds") for r in items]),
            "avg_ready": _mean([r.get("phases", {}).get("wait_until_ready_seconds") for r in items]),
            "avg_post": _mean([r.get("phases", {}).get("post_click_latency_seconds") for r in items]),
            "avg_total_cold": _mean([r.get("total_seconds") for r in cold_items]),
            "avg_total_warm": _mean([r.get("total_seconds") for r in warm_items]),
        })

    return overall, profile_rows


def print_summary(overall, profile_rows, limit=None):
    print("=== TỔNG QUAN BENCHMARK ===")
    print(f"Runs        : {overall['runs']}")
    print(f"Success     : {overall['success_runs']}")
    print(f"Failed      : {overall['failed_runs']}")
    print(f"Avg total   : {_fmt(overall['avg_total'])}")
    print(f"Avg driver  : {_fmt(overall['avg_driver'])}")
    print(f"Avg ready   : {_fmt(overall['avg_ready'])}")
    print(f"Avg post    : {_fmt(overall['avg_post'])}")
    print("\n=== COLD/WARM BREAKDOWN ===")
    _print_bucket("Cold start", overall["cold"])
    _print_bucket("Warm reuse", overall["warm"])

    print("\n=== THEO PROFILE ===")
    rows = profile_rows[:limit] if limit else profile_rows
    for row in rows:
        print(
            f"- {row['profile_name']}: runs={row['runs']}, success={row['success_runs']}, "
            f"avg_total={_fmt(row['avg_total'])}, avg_driver={_fmt(row['avg_driver'])}, "
            f"avg_ready={_fmt(row['avg_ready'])}, avg_post={_fmt(row['avg_post'])}, "
            f"cold={_fmt(row['avg_total_cold'])}, warm={_fmt(row['avg_total_warm'])}"
        )


def print_latest_runs(records, limit):
    print(f"\n=== {limit} RUN GẦN NHẤT ===")
    for row in records[-limit:]:
        mode = _driver_mode(row)
        print(
            f"- [{row.get('finished_at', '?')}] {row.get('profile_name', '?')} / {row.get('video_name', '?')} | "
            f"mode={mode} | success={row.get('success')} | total={_fmt(row.get('total_seconds'))} | "
            f"driver={_fmt(row.get('phases', {}).get('ensure_driver_seconds'))} | "
            f"ready={_fmt(row.get('phases', {}).get('wait_until_ready_seconds'))} | "
            f"post={_fmt(row.get('phases', {}).get('post_click_latency_seconds'))} | "
            f"reason={row.get('reason', '')}"
        )


def main():
    parser = argparse.ArgumentParser(description="Tổng hợp benchmark upload TikTok Auto Uploader")
    parser.add_argument("--file", default=str(DEFAULT_LOG), help="Đường dẫn file JSONL benchmark")
    parser.add_argument("--latest", type=int, default=5, help="Số run gần nhất cần in")
    parser.add_argument("--limit-profiles", type=int, default=0, help="Giới hạn số profile hiển thị")
    args = parser.parse_args()

    path = Path(args.file)
    records = load_records(path)
    if not records:
        print(f"Không có dữ liệu benchmark trong: {path}")
        print("Hãy chạy app, upload vài video, rồi chạy lại script này để xem số liệu.")
        return

    overall, profile_rows = build_summary(records)
    print_summary(overall, profile_rows, limit=(args.limit_profiles or None))
    if args.latest > 0:
        print_latest_runs(records, args.latest)


if __name__ == "__main__":
    main()