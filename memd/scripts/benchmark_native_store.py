"""Native adapter/RPC costs with synthetic vectors; no model/token savings claim."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from benchmark_storage import daemon, summary
from measure import MemdClient, rss_bytes

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from memd.clients.python.native_store import RustMemoryStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("memd/benchmark-results"))
    args = parser.parse_args()
    root = args.output / ("native-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    root.mkdir(parents=True)
    database = root / "memories.db"
    samples = {"native_add":[], "inventory_rpc":[], "hybrid_retrieve_rpc":[], "ping_rpc":[]}
    with daemon(args.binary.resolve(), database, 128, root / "daemon.log") as (address,pid,startup,rss):
        scope = {"namespace":"native-benchmark", "access_scope":"private"}
        store = RustMemoryStore(endpoint={"tcp_address":address}, scope=scope, home=root)
        with MemdClient(tcp_address=address) as client:
            for index in range(10):
                started = time.perf_counter()
                result = store.add("memory", f"Rust Cargo workspace benchmark fact {index}")
                if not result.get("success"):
                    raise RuntimeError(result)
                samples["native_add"].append((time.perf_counter()-started)*1000)
            for _ in range(30):
                for name, operation in (
                    ("ping_rpc",client.ping),
                    ("inventory_rpc",lambda:client.inventory(scope)),
                    ("hybrid_retrieve_rpc",lambda:client.retrieve("Rust Cargo workspace",k=5,scope=scope,mode="hybrid")),
                ):
                    started = time.perf_counter()
                    operation()
                    samples[name].append((time.perf_counter()-started)*1000)
            stats = client.request("storage_stats")
            current_rss = rss_bytes(pid)
            live_storage = sum(path.stat().st_size for path in root.glob("memories.db*"))
    report = {"fixture":"synthetic 128 dimensions, 10 curated records, debug build, loopback TCP",
              "startup_ms":startup, "memory_operations":10, "latency":{name:summary(values) for name,values in samples.items()},
              "rss_bytes":current_rss, "peak_sampled_rss_bytes":max(rss,default=current_rss),
              "live_db_wal_shm_bytes":live_storage, "closed_database_bytes":database.stat().st_size,
              "storage_stats":stats, "review_invocations":0, "review_input_tokens":None,
              "cached_tokens":None, "uncached_tokens":None,
              "limitations":"Storage-only benchmark; no extraction LLM calls, no native-vs-Rust token or speed comparison."}
    (root / "report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"report":str(root / "report.json"), **report},indent=2))


if __name__ == "__main__":
    main()
