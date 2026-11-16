#!/usr/bin/env python3
"""Real daemon/storage timings using SYNTHETIC vectors; never a retrieval-quality benchmark.

Creates a unique run directory, retaining every database, log and report. Restart
measurements retain the OS page cache; this script does not claim disk-cold I/O.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import json
import hashlib
import platform
from pathlib import Path
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import uuid

from measure import MemdClient, percentile, rss_bytes


def summary(samples):
    return {"count":len(samples), "median_ms":statistics.median(samples), "p95_ms":percentile(samples, 0.95)}


@contextmanager
def daemon(binary, database, dimension, log_path, engine_options=None):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        address = reserved.getsockname()
    started = time.perf_counter()
    with log_path.open("ab") as log:
        process = subprocess.Popen([str(binary), "--tcp", f"127.0.0.1:{address[1]}", "--store", str(database),
                                    *(engine_options if engine_options is not None else ["--synthetic", "--dimension", str(dimension)])], stdout=log, stderr=log)
        samples = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    samples.append(rss_bytes(process.pid))
                except OSError:
                    break
                stop.wait(0.02)

        sampler = threading.Thread(target=sample)
        sampler.start()
        try:
            while time.perf_counter() - started < 15 and process.poll() is None:
                try:
                    with MemdClient(tcp_address=address) as client:
                        client.ping()
                    break
                except OSError:
                    time.sleep(0.01)
            else:
                raise RuntimeError(f"daemon did not become ready; see {log_path}")
            yield address, process.pid, (time.perf_counter() - started) * 1000, samples
        finally:
            stop.set()
            sampler.join()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def measure_set(binary, root, count, dimension, queries, clients, seed_database=None):
    database = root / f"synthetic-{count}.db"
    log = root / f"daemon-{count}.log"
    if seed_database is not None:
        # A page-wise SQLite backup includes committed WAL content without loading
        # the corpus into Python/Rust or mutating the retained source dataset.
        with closing(sqlite3.connect(seed_database.resolve().as_uri()+"?mode=ro",uri=True)) as source:
            with closing(sqlite3.connect(database)) as destination:
                source.backup(destination)
    insertions = []
    with daemon(binary, database, dimension, log) as (address, pid, startup, samples):
        with MemdClient(tcp_address=address, timeout=60) as client:
            if seed_database is not None and client.request("storage_stats")["memory_count"] != count:
                raise ValueError("seed count does not match --sizes")
            for i in range(count if seed_database is None else 0):
                start = time.perf_counter()
                client.store({"id":f"synthetic-{i}", "content":f"Synthetic project {i} solved compiler error topic{i % 997}",
                              "kind":"episodic" if i % 2 else "semantic", "source_ref":f"synthetic://session/{i}"})
                insertions.append((time.perf_counter() - start) * 1000)
        insert_peak = max(samples) if samples else rss_bytes(pid)
    report = {"memories":count, "dimension":dimension, "initial_process_startup_ms":startup,
              "seed_database":str(seed_database) if seed_database else None,
              "insertion":summary(insertions) if insertions else None, "insertion_peak_rss_bytes":insert_peak,
              "raw_float32_embedding_bytes":count * dimension * 4, "warm_trials":[]}
    for trial in range(3):
        with daemon(binary, database, dimension, log) as (address, pid, startup, samples):
            result = {"restart_startup_ms":startup, "startup_rss_bytes":rss_bytes(pid)}
            with MemdClient(tcp_address=address, timeout=60) as client:
                start = time.perf_counter()
                client.retrieve("compiler topic1", k=10, mode="vector")
                result["first_vector_query_after_restart_ms"] = (time.perf_counter() - start) * 1000
                for mode in ("vector", "keyword", "hybrid"):
                    timings = []
                    for query in range(queries):
                        start = time.perf_counter()
                        client.retrieve(f"compiler topic{query % 997}", k=10, mode=mode)
                        timings.append((time.perf_counter() - start) * 1000)
                    result[mode] = summary(timings)

            def retrieve_worker(index):
                timings = []
                with MemdClient(tcp_address=address, timeout=60) as client:
                    for query in range(queries):
                        start = time.perf_counter()
                        client.retrieve(f"compiler topic{(index * queries + query) % 997}", k=10, mode="hybrid")
                        timings.append((time.perf_counter() - start) * 1000)
                return timings

            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=clients) as pool:
                timings = [t for group in pool.map(retrieve_worker, range(clients)) for t in group]
            result["concurrent_hybrid"] = {**summary(timings), "clients":clients,
                "rpc_per_second":len(timings) / (time.perf_counter() - start)}
            result["peak_rss_bytes"] = max(samples) if samples else rss_bytes(pid)
            result["rss_delta_bytes"] = result["peak_rss_bytes"] - result["startup_rss_bytes"]
            with MemdClient(tcp_address=address) as client:
                storage_stats = client.request("storage_stats")
            report["warm_trials"].append(result)
    # Use the daemon's bundled SQLite/dbstat; host Python SQLite may lack dbstat.
    report["vector_allocated_bytes"] = storage_stats["vector_allocated_bytes"]
    report["vector_overhead_bytes"] = report["vector_allocated_bytes"] - report["raw_float32_embedding_bytes"]
    # Include WAL recovery/checkpoint before measuring the final main file size.
    report["database_file_bytes"] = database.stat().st_size
    report["wal_file_bytes"] = database.with_name(database.name + "-wal").stat().st_size if database.with_name(database.name + "-wal").exists() else 0
    report["shm_file_bytes"] = database.with_name(database.name + "-shm").stat().st_size if database.with_name(database.name + "-shm").exists() else 0
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--dimension", type=int, default=128)
    parser.add_argument("--queries", type=int, default=30)
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--build-label", default="unspecified; record target/toolchain separately")
    parser.add_argument("--seed-database",type=Path,help="copy a retained synthetic database into this new run; measure retrieval without reinsertion")
    parser.add_argument("--output-dir", type=Path, default=Path("memd/benchmark-results"))
    args = parser.parse_args()
    if args.seed_database is not None and len(args.sizes) != 1:
        parser.error("--seed-database requires exactly one --sizes value")
    if min(*args.sizes, args.queries, args.clients, args.dimension) < 1:
        parser.error("sizes, queries, clients and dimension must be positive")
    root = args.output_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    report = {"synthetic":True, "semantic_quality_measured":False, "os_cache_flushed":False,
              "platform":platform.platform(), "python":sys.version, "binary":str(args.binary.resolve()),
              "binary_sha256":hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              "build_label":args.build_label, "runs":[]}
    for count in args.sizes:
        result = measure_set(args.binary.resolve(), root, count, args.dimension, args.queries, args.clients,args.seed_database)
        report["runs"].append(result)
        # Preserve partial results if a larger run later fails.
        (root / f"report-{count}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Retained results: {root}", flush=True)


if __name__ == "__main__":
    main()
