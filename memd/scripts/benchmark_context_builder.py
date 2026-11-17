"""Reproducible synthetic comparison of fixed, adaptive, and selective packaging."""
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "python"))
from context_builder import ContextBuilder


def run(label, builder, rows, budget, loops=100):
    required = {f"m{i:03}" for i in range(0, 120, 12)}
    relevant = required | {f"m{i:03}" for i in (3, 8, 17, 24, 33, 47, 51, 68, 75, 89, 93, 107)}
    durations = []
    tracemalloc.start()
    package = None
    for _ in range(loops):
        start = time.perf_counter()
        package = builder.build(rows, budget)
        durations.append((time.perf_counter() - start) * 1000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    kept = required.intersection(package.selected_memory_ids)
    relevant_kept = relevant.intersection(package.selected_memory_ids)
    return {"variant": label, "memory_tokens": package.token_count, "selected": package.selected_count,
            "required_retained": len(kept), "required_total": len(required),
            "required_coverage": round(len(kept) / len(required), 3),
            "relevant_recall": round(len(relevant_kept) / len(relevant), 3),
            "selected_precision": round(len(relevant_kept) / max(1, package.selected_count), 3),
            "tokens_per_relevant_retained": round(package.token_count / max(1, len(relevant_kept)), 2),
            "required_evidence_omission": int(bool(required - kept)),
            "median_ms": round(statistics.median(durations), 3), "peak_bytes": peak}


def main():
    rows = [{"id": f"m{i:03}", "summary": (f"Important project constraint {i}: preserve tenant scope and avoid changing "
                                               "the established memory boundary." if i % 12 == 0 else
                                               f"Low-priority note {i}: " + "detail " * (15 + i % 30)),
             "score": 1 / (60 + i + 1), "importance": 0.5, "confidence": 1.0} for i in range(120)]
    results = [run("A_fixed", ContextBuilder(token_counter=lambda s: (len(s.encode("utf-8")) + 3) // 4), rows, 1000),
               run("B_adaptive", ContextBuilder(token_counter=lambda s: (len(s.encode("utf-8")) + 3) // 4), rows, 700),
               run("C_adaptive_selective", ContextBuilder(token_counter=lambda s: (len(s.encode("utf-8")) + 3) // 4,
                                                           minimum_utility_per_token=0.002), rows, 700)]
    print(json.dumps({"method": "same synthetic retrieval results; fixed 1,000-token baseline; adaptive ceiling 700; 100 runs",
                      "results": results}, indent=2))


if __name__ == "__main__":
    main()
