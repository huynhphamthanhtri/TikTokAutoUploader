import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_TRACE_DIR = Path("request_traces")


def load_trace_files(trace_dir: Path):
    if not trace_dir.exists():
        return []

    rows = []
    for path in sorted(trace_dir.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data["_file"] = str(path)
            rows.append(data)
        except Exception as e:
            print(f"[warn] Không đọc được {path}: {e}")
    return rows


def summarize_candidates(traces):
    endpoint_counter = Counter()
    endpoint_status = defaultdict(Counter)
    endpoint_methods = defaultdict(Counter)
    header_counter = defaultdict(Counter)

    for trace in traces:
        for item in trace.get("trace", []):
            url = item.get("url", "")
            if not url:
                continue
            endpoint_counter[url] += 1
            endpoint_status[url][str(item.get("status_code"))] += 1
            endpoint_methods[url][item.get("method", "") or "?"] += 1
            for header_name in item.get("request_headers", {}).keys():
                header_counter[url][header_name] += 1

    return endpoint_counter, endpoint_status, endpoint_methods, header_counter


def print_summary(traces, top_n=20):
    print("=== REQUEST TRACE SUMMARY ===")
    print(f"Trace files : {len(traces)}")
    if not traces:
        return

    outcomes = Counter(t.get("outcome", "unknown") for t in traces)
    print("Outcomes    : " + ", ".join(f"{k}={v}" for k, v in outcomes.items()))

    endpoint_counter, endpoint_status, endpoint_methods, header_counter = summarize_candidates(traces)

    print("\n=== TOP CANDIDATE ENDPOINTS ===")
    for url, count in endpoint_counter.most_common(top_n):
        statuses = ", ".join(f"{k}:{v}" for k, v in endpoint_status[url].most_common())
        methods = ", ".join(f"{k}:{v}" for k, v in endpoint_methods[url].most_common())
        common_headers = ", ".join(k for k, _ in header_counter[url].most_common(8))
        print(f"- hits={count} | methods=[{methods}] | status=[{statuses}]")
        print(f"  url     : {url}")
        print(f"  headers : {common_headers}")


def print_latest_trace(traces):
    if not traces:
        return
    latest = traces[-1]
    print("\n=== LATEST TRACE FILE ===")
    print(f"File       : {latest.get('_file')}")
    print(f"Profile    : {latest.get('profile_name')}")
    print(f"Video      : {latest.get('video_name')}")
    print(f"Outcome    : {latest.get('outcome')}")
    print(f"Captured   : {latest.get('captured_at')}")
    ctx = latest.get("request_context", {})
    print(f"Cookies    : {ctx.get('cookie_count', 0)}")
    print(f"Proxy      : {ctx.get('proxy_enabled', False)}")
    print(f"User-Agent : {ctx.get('user_agent')}")
    print(f"Candidates : {len(latest.get('candidate_urls', []))}")


def main():
    parser = argparse.ArgumentParser(description="Tổng hợp trace request upload TikTok")
    parser.add_argument("--dir", default=str(DEFAULT_TRACE_DIR), help="Thư mục chứa trace request")
    parser.add_argument("--top", type=int, default=20, help="Số endpoint top cần hiển thị")
    args = parser.parse_args()

    trace_dir = Path(args.dir)
    traces = load_trace_files(trace_dir)
    if not traces:
        print(f"Không có trace request trong: {trace_dir}")
        print("Hãy chạy upload ít nhất 1 lần để app sinh file trace JSON.")
        return

    print_summary(traces, top_n=args.top)
    print_latest_trace(traces)


if __name__ == "__main__":
    main()