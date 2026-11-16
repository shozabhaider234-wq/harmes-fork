#!/usr/bin/env python3
"""Evaluate real local embeddings on a small labeled fixture and retain all results."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import time
import uuid
from benchmark_storage import daemon
from measure import MemdClient, rss_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary",type=Path,required=True)
    parser.add_argument("--model-dir",type=Path,required=True)
    parser.add_argument("--ort-dylib",type=Path,required=True)
    parser.add_argument("--fixture",type=Path,default=Path(__file__).resolve().parents[1] / "tests/fixtures/semantic_quality.json")
    parser.add_argument("--output-dir",type=Path,default=Path("memd/benchmark-results"))
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    root = args.output_dir / ("real-model-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    root.mkdir(parents=True,exist_ok=False)
    report = {"synthetic":False,"fixture":str(args.fixture),"fixture_sha256":hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              "limitation":fixture["description"],"queries":[],"binary_sha256":hashlib.sha256(args.binary.read_bytes()).hexdigest()}
    options = ["--model-dir",str(args.model_dir.resolve()),"--ort-dylib",str(args.ort_dylib.resolve())]
    with daemon(args.binary.resolve(),root / "memories.db",384,root / "daemon.log",options) as (address,pid,startup,samples):
        report["startup_ms"] = startup
        report["startup_rss_bytes"] = rss_bytes(pid)
        with MemdClient(tcp_address=address,timeout=60) as client:
            insertion = []
            for memory in fixture["memories"]:
                start = time.perf_counter()
                client.store({**memory,"source_ref":"fixture://semantic-quality/"+memory["id"]})
                insertion.append((time.perf_counter()-start)*1000)
            report["insertion_median_ms"] = statistics.median(insertion)
            for mode in ("vector","keyword","hybrid"):
                reciprocal_ranks,recalls = [],[]
                for query in fixture["queries"]:
                    start = time.perf_counter()
                    hits = client.retrieve(query["query"],k=3,mode=mode)
                    ids = [h["id"] for h in hits]
                    relevant = set(query["relevant"])
                    ranks = [i+1 for i,memory_id in enumerate(ids) if memory_id in relevant]
                    reciprocal_ranks.append(1/min(ranks) if ranks else 0)
                    recalls.append(len(relevant.intersection(ids))/len(relevant))
                    report["queries"].append({"mode":mode,**query,"returned":ids,"latency_ms":(time.perf_counter()-start)*1000})
                report[mode] = {"recall_at_3":statistics.mean(recalls),"mrr_at_3":statistics.mean(reciprocal_ranks)}
            report["storage"] = client.request("storage_stats")
        report["peak_rss_bytes"] = max(samples) if samples else rss_bytes(pid)
    path = root / "report.json"
    path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "queries"},indent=2))
    print(f"Retained results: {path}")


if __name__ == "__main__":
    main()
