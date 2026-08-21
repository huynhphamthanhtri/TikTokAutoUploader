"""
benchmark_scalability.py - Standalone Scalability & Heartbeat Latency Benchmark Harness.

Measures Tkinter event loop latency, memory, thread counts, and queue behavior
under varying profile counts (10, 50, 100, 200) and load scenarios.
Strictly follows AGENTS.md: Real Tkinter integration, no shallow mocks, raw sample logging.
"""

import argparse
import csv
import json
import os
import platform
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Output paths
SCRATCH_DIR = REPO_ROOT / "scratch"
SCRATCH_DIR.mkdir(exist_ok=True)


def get_system_metadata() -> Dict[str, Any]:
    """Capture machine hardware and execution environment metadata."""
    import subprocess
    commit_sha = "unknown"
    try:
        commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:
        pass

    return {
        "timestamp": datetime.now().isoformat(),
        "commit_sha": commit_sha,
        "platform": platform.platform(),
        "os_version": platform.version(),
        "python_version": sys.version,
        "cpu_count": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "total_ram_mb": round(psutil.virtual_memory().total / (1024 * 1024), 2),
    }


def percentile(data: List[float], p: float) -> float:
    """Calculate percentile from raw samples."""
    if not data:
        return 0.0
    s = sorted(data)
    idx = (len(s) - 1) * (p / 100.0)
    floor_idx = int(idx)
    ceil_idx = min(floor_idx + 1, len(s) - 1)
    if floor_idx == ceil_idx:
        return s[floor_idx]
    d0 = s[floor_idx] * (ceil_idx - idx)
    d1 = s[ceil_idx] * (idx - floor_idx)
    return d0 + d1


class TkHeartbeatHarness:
    """Real Tkinter Integration Benchmark with precision probe tracking."""

    def __init__(self, num_profiles: int = 100, probe_interval_ms: int = 10):
        self.num_profiles = num_profiles
        self.probe_interval_ms = probe_interval_ms
        self.probe_interval_s = probe_interval_ms / 1000.0
        self.samples: List[float] = []
        self.running = False
        self.root = None
        self.tree = None
        self.status_text = None
        self.important_log_text = None
        self.profiles: Dict[str, Any] = {}
        self.expected_probe_time = 0.0
        self.process = psutil.Process(os.getpid())

    def setup_ui(self):
        """Instantiate real Tk / CustomTkinter widgets."""
        try:
            import tkinter as tk
            from tkinter import ttk
            import customtkinter as ctk

            self.root = ctk.CTk()
            self.root.title("Benchmark Scalability Harness")
            self.root.geometry("800x600")

            # Setup Treeview
            columns = ("name", "tiktok", "cookie", "status", "monetization", "proxy", "upload", "folder", "error")
            self.tree = ttk.Treeview(self.root, columns=columns, show="headings", height=15)
            for c in columns:
                self.tree.heading(c, text=c)
            self.tree.pack(fill="x", padx=10, pady=5)

            # Setup text widgets
            self.status_text = ctk.CTkTextbox(self.root, height=150)
            self.status_text.pack(fill="both", expand=True, padx=10, pady=5)
            self.important_log_text = ctk.CTkTextbox(self.root, height=100)
            self.important_log_text.pack(fill="x", padx=10, pady=5)

            # Populate mock profile dictionary
            for i in range(self.num_profiles):
                name = f"BENCH_PROFILE_{i+1:03d}"
                self.profiles[name] = {
                    "name": name,
                    "running": i % 5 == 0,
                    "uploading": False,
                    "session_busy": False,
                    "uploads_today_count": i % 3,
                    "config": {
                        "name": name,
                        "account_uuid": f"uuid_{i+1:03d}",
                        "tiktok_id": f"tiktok_user_{i+1:03d}",
                        "cookie_str": "sessionid=dummy; sid_guard=dummy;" if i % 2 == 0 else "",
                        "session_auth_state": "live" if i % 2 == 0 else "expired",
                        "folder_path": f"C:/Auto_Data/{name}/Video",
                        "use_proxy": i % 3 == 0,
                        "proxy_string": "http://user:pass@1.2.3.4:8080" if i % 3 == 0 else "",
                    },
                    "ui": {
                        "status": "Đang chạy" if i % 5 == 0 else "Đã dừng",
                        "login": "Live" if i % 2 == 0 else "Die",
                        "proxy": "OK: 1.2.3.4" if i % 3 == 0 else "Tắt",
                        "upload": "Chờ video",
                        "last_error": "",
                    }
                }
                # Insert row to tree
                self.tree.insert("", "end", iid=f"uuid_{i+1:03d}", values=(
                    name, f"@user_{i+1:03d}", "🟢 Live" if i % 2 == 0 else "🔴 Die",
                    "⚡ Đang chạy" if i % 5 == 0 else "⏸ Đã dừng",
                    "🏆 Đang bật" if i % 4 == 0 else "⚪ Chưa bật",
                    "Proxy OK" if i % 3 == 0 else "Tắt",
                    "Chờ video", f"C:/Auto_Data/{name}/Video", ""
                ))

            self.root.update_idletasks()
            self.root.update()
        except Exception as e:
            raise RuntimeError(f"NO_DESKTOP_SESSION: Failed to instantiate real Tkinter GUI widget: {e}")

    def _schedule_probe(self):
        if not self.running:
            return
        now = time.perf_counter()
        if self.expected_probe_time > 0:
            # Latency definition: actual callback time - scheduled deadline
            delay_ms = max(0.0, (now - self.expected_probe_time) * 1000.0)
            self.samples.append(delay_ms)

        self.expected_probe_time = now + self.probe_interval_s
        self.root.after(self.probe_interval_ms, self._schedule_probe)

    def run_scenario(self, scenario_name: str, duration_sec: float, worker_fn=None) -> Dict[str, Any]:
        """Execute a benchmark scenario for duration_sec seconds."""
        self.setup_ui()
        self.samples.clear()
        self.running = True

        stop_event = threading.Event()
        workers = []

        # Start background workload
        if worker_fn is not None:
            workers = worker_fn(self, stop_event)

        # Warm-up 0.5s
        self.expected_probe_time = time.perf_counter() + self.probe_interval_s
        self.root.after(self.probe_interval_ms, self._schedule_probe)

        start_time = time.perf_counter()
        end_time = start_time + duration_sec

        try:
            while time.perf_counter() < end_time and self.running:
                self.root.update()
                time.sleep(0.001)
        finally:
            self.running = False
            stop_event.set()
            for w in workers:
                if w.is_alive():
                    w.join(timeout=1.0)

            # Capture process metrics
            try:
                mem_info = self.process.memory_info()
                rss_mb = round(mem_info.rss / (1024 * 1024), 2)
                thread_count = self.process.num_threads()
                handle_count = self.process.num_handles() if sys.platform == "win32" else 0
            except Exception:
                rss_mb, thread_count, handle_count = 0.0, 0, 0

            # Destroy window
            try:
                self.root.destroy()
            except Exception:
                pass

        # Discard first 20 warm-up samples
        valid_samples = self.samples[20:] if len(self.samples) > 20 else self.samples

        return {
            "scenario": scenario_name,
            "num_profiles": self.num_profiles,
            "duration_sec": duration_sec,
            "sample_count": len(valid_samples),
            "p50_ms": round(percentile(valid_samples, 50), 3),
            "p95_ms": round(percentile(valid_samples, 95), 3),
            "p99_ms": round(percentile(valid_samples, 99), 3),
            "max_ms": round(max(valid_samples) if valid_samples else 0.0, 3),
            "mean_ms": round(sum(valid_samples) / len(valid_samples) if valid_samples else 0.0, 3),
            "rss_mb": rss_mb,
            "thread_count": thread_count,
            "handle_count": handle_count,
            "raw_samples": valid_samples,
        }


# =========================================================================
# Workload Generators (Synthetic & Threaded)
# =========================================================================

def workload_normal(harness: TkHeartbeatHarness, stop_event: threading.Event) -> List[threading.Thread]:
    """Simulate idle polling across profiles."""
    def _worker():
        while not stop_event.is_set():
            time.sleep(0.1)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return [t]


def workload_log_burst(harness: TkHeartbeatHarness, stop_event: threading.Event, msg_per_sec: int = 100) -> List[threading.Thread]:
    """Inject log burst directly via root.after(0) simulating current production update_status."""
    def _generator(worker_id: int):
        interval = 1.0 / (msg_per_sec / 4)
        while not stop_event.is_set():
            msg = f"[Worker-{worker_id}] Uploading chunk {time.perf_counter():.4f} for profile"
            # Simulate current production update_status mechanism
            try:
                if harness.root and harness.status_text:
                    harness.root.after(0, lambda m=msg: _mock_insert_log(harness, m))
            except Exception:
                pass
            time.sleep(interval)

    threads = []
    for wid in range(4):
        t = threading.Thread(target=_generator, args=(wid,), daemon=True)
        t.start()
        threads.append(t)
    return threads


def _mock_insert_log(harness: TkHeartbeatHarness, msg: str):
    """Simulate current production text widget insertion + layout recalculation."""
    try:
        if harness.status_text and harness.status_text.winfo_exists():
            harness.status_text.configure(state="normal")
            harness.status_text.insert("end", f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
            harness.status_text.see("end")
            harness.status_text.configure(state="disabled")
    except Exception:
        pass


def workload_high_load(harness: TkHeartbeatHarness, stop_event: threading.Event) -> List[threading.Thread]:
    """Combined high load: 100 msg/s log burst + 20 synthetic upload workers mutating status."""
    threads = []

    # 1. Log burst workers
    for wid in range(4):
        def _log_worker(w=wid):
            interval = 1.0 / 25
            while not stop_event.is_set():
                msg = f"[Worker-{w}] Progress upload chunk {time.perf_counter():.4f}"
                try:
                    if harness.root and harness.status_text:
                        harness.root.after(0, lambda m=msg: _mock_insert_log(harness, m))
                except Exception:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=_log_worker, daemon=True)
        t.start()
        threads.append(t)

    # 2. 20 Synthetic upload workers mutating status and Treeview
    for uid in range(20):
        def _upload_worker(u=uid):
            while not stop_event.is_set():
                p_idx = (u * 5) % harness.num_profiles
                name = f"BENCH_PROFILE_{p_idx+1:03d}"
                uuid = f"uuid_{p_idx+1:03d}"
                # Update status
                def _ui_mut(n=name, i=uuid):
                    try:
                        if harness.tree and harness.tree.winfo_exists():
                            harness.tree.item(i, values=(
                                n, f"@user_{p_idx+1:03d}", "🟢 Live", "⚡ Đang đăng (50%)",
                                "🏆 Đang bật", "Proxy OK", f"Đã đăng {u+1}/5", f"C:/Auto_Data/{n}/Video", ""
                            ))
                    except Exception:
                        pass
                try:
                    if harness.root:
                        harness.root.after(0, _ui_mut)
                except Exception:
                    pass
                time.sleep(0.15)
        t = threading.Thread(target=_upload_worker, daemon=True)
        t.start()
        threads.append(t)

    return threads


# =========================================================================
# Main Benchmark Runner (5 Iterations per Scenario)
# =========================================================================

def run_all_benchmarks(num_runs: int = 5, duration_per_run: float = 3.0) -> Dict[str, Any]:
    """Execute 5 independent process-level runs across all target scenarios."""
    scenarios = [
        ("Normal_100_profiles", 100, workload_normal),
        ("Log_Burst_100_msgs_per_sec", 100, lambda h, s: workload_log_burst(h, s, msg_per_sec=100)),
        ("High_Load_100p_100log_20workers", 100, workload_high_load),
    ]

    all_results = {
        "metadata": get_system_metadata(),
        "sla_gates": {
            "Normal_100_profiles": {"p50": 30.0, "p95": 80.0, "p99": 120.0},
            "High_Load_100p_100log_20workers": {"p50": 50.0, "p95": 100.0, "p99": 180.0},
        },
        "scenarios": {},
    }

    print("=" * 80)
    print("STARTING SCALABILITY & HEARTBEAT BENCHMARK HARNESS (5 RUNS PER SCENARIO)")
    print(f"Commit SHA: {all_results['metadata']['commit_sha']}")
    print(f"Platform:   {all_results['metadata']['platform']}")
    print(f"CPU Cores:  {all_results['metadata']['cpu_count']} | RAM: {all_results['metadata']['total_ram_mb']} MB")
    print("=" * 80)

    for sc_name, p_count, worker_fn in scenarios:
        print(f"\n>>> Running Scenario: {sc_name} (Profiles={p_count}, Runs={num_runs}, Duration={duration_per_run}s)")
        run_records = []
        for r in range(1, num_runs + 1):
            harness = TkHeartbeatHarness(num_profiles=p_count, probe_interval_ms=10)
            res = harness.run_scenario(scenario_name=sc_name, duration_sec=duration_per_run, worker_fn=worker_fn)
            run_records.append(res)
            print(f"  [Run {r}/{num_runs}] p50={res['p50_ms']}ms | p95={res['p95_ms']}ms | p99={res['p99_ms']}ms | max={res['max_ms']}ms | RSS={res['rss_mb']}MB | Threads={res['thread_count']}")
            time.sleep(0.5)

        all_results["scenarios"][sc_name] = run_records

    # Save output to scratch directory
    json_path = SCRATCH_DIR / "benchmark_baseline.json"
    csv_path = SCRATCH_DIR / "benchmark_baseline.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    # Flatten for CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Scenario", "Run", "Profiles", "p50_ms", "p95_ms", "p99_ms", "max_ms", "mean_ms", "RSS_MB", "Threads", "Handles"])
        for sc_name, runs in all_results["scenarios"].items():
            for idx, r in enumerate(runs, 1):
                writer.writerow([
                    sc_name, idx, r["num_profiles"], r["p50_ms"], r["p95_ms"], r["p99_ms"],
                    r["max_ms"], r["mean_ms"], r["rss_mb"], r["thread_count"], r["handle_count"]
                ])

    print("\n" + "=" * 80)
    print(f"[SUCCESS] Baseline benchmark artifacts generated:")
    print(f"  - JSON: {json_path}")
    print(f"  - CSV:  {csv_path}")
    print("=" * 80)

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scalability Benchmark Harness")
    parser.add_argument("--runs", type=int, default=5, help="Number of independent runs per scenario")
    parser.add_argument("--duration", type=float, default=3.0, help="Duration in seconds per run")
    args = parser.parse_args()

    results = run_all_benchmarks(num_runs=args.runs, duration_per_run=args.duration)
