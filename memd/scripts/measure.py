#!/usr/bin/env python3
"""Measure concurrent memd RPC latency/RSS; optionally sum Hermes token usage JSONL."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "python"))
from memory_client import MemdClient  # noqa: E402


def rss_bytes(pid: int) -> int:
    if sys.platform == "win32":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel.CloseHandle.restype = ctypes.c_int
        psapi.GetProcessMemoryInfo.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong,
        )
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        handle = kernel.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
            return int(counters.WorkingSetSize)
        finally:
            kernel.CloseHandle(handle)
    if os.path.exists(f"/proc/{pid}/statm"):
        with open(f"/proc/{pid}/statm", encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    output = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True).strip()
    return int(output) * 1024


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * p) - 1)]


def run_client(index: int, count: int, endpoint: dict[str, object]) -> list[float]:
    latencies = []
    with MemdClient(**endpoint) as client:
        for item in range(count):
            query = f"memd-bench-client-{index}-item-{item}"
            started = time.perf_counter()
            client.store({"content": query, "source": "memd-measurement"})
            latencies.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            client.retrieve(query, k=1)
            latencies.append((time.perf_counter() - started) * 1000)
    return latencies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--socket", dest="socket_path", help="Unix domain socket path")
    transport.add_argument("--tcp", metavar="HOST:PORT", help="loopback TCP address for development")
    parser.add_argument("--clients", type=int, default=1)
    parser.add_argument("--requests-per-client", type=int, default=100,
                        help="each iteration performs one store and one retrieve")
    parser.add_argument("--pid", type=int, help="memd PID to sample for RSS")
    parser.add_argument("--usage-jsonl", type=Path,
                        help="optional Hermes usage records, one {\"total_tokens\": N} per line")
    args = parser.parse_args()
    if args.clients < 1 or args.requests_per_client < 1 or (args.pid is not None and args.pid < 1):
        parser.error("clients, requests-per-client, and PID must be positive")

    endpoint: dict[str, object]
    if args.socket_path:
        endpoint = {"socket_path": args.socket_path}
    else:
        host, separator, port = args.tcp.rpartition(":")
        if not separator:
            parser.error("--tcp must be HOST:PORT")
        endpoint = {"tcp_address": (host, int(port))}

    rss_samples: list[int] = []
    stop_sampling = threading.Event()
    if args.pid is not None:
        try:
            rss_samples.append(rss_bytes(args.pid))
        except (OSError, ValueError, IndexError) as exc:
            parser.error(f"cannot read RSS for PID {args.pid}: {exc}")

        def sample_rss() -> None:
            while not stop_sampling.is_set():
                try:
                    rss_samples.append(rss_bytes(args.pid))
                except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                    break
                stop_sampling.wait(0.05)

        sampler = threading.Thread(target=sample_rss, daemon=True)
        sampler.start()
    else:
        sampler = None

    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=args.clients) as pool:
            futures = [pool.submit(run_client, i, args.requests_per_client, endpoint)
                       for i in range(args.clients)]
            latencies = [latency for future in futures for latency in future.result()]
    finally:
        stop_sampling.set()
        if sampler:
            sampler.join()
    elapsed = time.perf_counter() - started

    token_count = None
    if args.usage_jsonl:
        token_count = 0
        with args.usage_jsonl.open(encoding="utf-8") as usage_file:
            for line_number, line in enumerate(usage_file, start=1):
                if line.strip():
                    try:
                        token_count += int(json.loads(line)["total_tokens"])
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                        parser.error(f"invalid usage record on line {line_number}: {exc}")

    report = {
        "clients": args.clients,
        "iterations_per_client": args.requests_per_client,
        "rpc_count": len(latencies),
        "elapsed_seconds": round(elapsed, 3),
        "rpc_per_second": round(len(latencies) / elapsed, 2),
        "latency_ms_median": round(statistics.median(latencies), 3),
        "latency_ms_p95": round(percentile(latencies, 0.95), 3),
        "daemon_rss_start_bytes": rss_samples[0] if rss_samples else None,
        "daemon_rss_peak_bytes": max(rss_samples) if rss_samples else None,
        "daemon_rss_delta_bytes": max(rss_samples) - rss_samples[0] if rss_samples else None,
        "total_tokens": token_count,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
