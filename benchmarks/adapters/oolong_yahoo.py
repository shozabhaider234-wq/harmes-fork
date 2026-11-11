"""One-record OOLONG Yahoo adapter. No task runs without --execute."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from harmes_cli import HARMES_COMMIT, MODEL, PROVIDER, ROOT, git_commit, run_harmes, utc_now, write_json

OOLONG_COMMIT = "0bb7eabe839218fee7fe8d007f41cfc2fd3ae24c"
DATASET_REVISION = "f0d59eaf0febf130664cfceb710436c8e3216b2b"


def selected_row(context_len: int, row_id: str, shard_filename: str):
    from huggingface_hub import hf_hub_download
    import pyarrow.parquet as pq

    if not row_id or not shard_filename:
        raise ValueError("An exact --row-id and --shard-filename are required")
    if not shard_filename.startswith("data/test-") or not shard_filename.endswith(".parquet"):
        raise ValueError("--shard-filename must name a test Parquet shard")
    shard = hf_hub_download(
        repo_id="oolongbench/oolong-synth",
        repo_type="dataset",
        filename=shard_filename,
        revision=DATASET_REVISION,
    )
    table = pq.read_table(
        shard,
        columns=["id", "dataset", "context_len", "question", "context_window_text", "answer", "answer_type"],
    )
    rows = table.to_pylist()
    for row in rows:
        if (
            row.get("dataset") == "yahoo"
            and int(row.get("context_len", -1)) == context_len
            and str(row.get("id")) == row_id
        ):
            return row
    raise KeyError(f"No matching Yahoo test row found in {shard_filename} for id={row_id!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Pinned official OOLONG checkout")
    parser.add_argument("--context-len", type=int, default=32768)
    parser.add_argument("--row-id", required=True, help="Exact official test-row id")
    parser.add_argument("--shard-filename", required=True, help="Exact-revision data/test-*.parquet file containing the row")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stage-only", action="store_true", help="Stage only the prompt; do not start Harmes")
    mode.add_argument("--execute", action="store_true", help="Run Harmes and the official scorer")
    parser.add_argument("--staged-prompt", type=Path, help="Prompt file produced by --stage-only")
    parser.add_argument("--harmes", type=Path, default=ROOT / ".venv" / "Scripts" / "hermes.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks" / "artifacts" / "oolong-yahoo")
    parser.add_argument("--stage-root", type=Path, default=ROOT / "benchmarks" / "data" / "oolong-yahoo")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()

    source = args.source.resolve()
    if git_commit(source) != OOLONG_COMMIT:
        raise RuntimeError(f"OOLONG checkout must be {OOLONG_COMMIT}")
    row = selected_row(args.context_len, args.row_id, args.shard_filename)
    if not row.get("question") or not row.get("context_window_text"):
        raise ValueError("Official Yahoo test row is missing its question or context")

    # Official protocol: do not include context_window_text_with_labels or shorten the context.
    query = row["context_window_text"] + "\n" + row["question"]
    task_id = str(row["id"])
    if args.stage_only:
        stage_dir = (args.stage_root / f"yahoo-{args.context_len}-{task_id}").resolve()
        stage_dir.mkdir(parents=True, exist_ok=False)
        prompt_path = stage_dir / "prompt.txt"
        prompt_path.write_text(query, encoding="utf-8")
        write_json(stage_dir / "manifest.json", {
            "benchmark": "OOLONG Yahoo", "dataset": "oolongbench/oolong-synth",
            "dataset_revision": DATASET_REVISION, "split": "test", "source_subset": "yahoo",
            "task_id": task_id, "context_len": int(row["context_len"]),
            "input_format": "context_window_text + newline + question",
            "prompt_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "contains_reference_answer": False,
        })
        print(f"Staged prompt: {prompt_path}")
        print(f"Official row id: {task_id}")
        return 0

    if args.staged_prompt:
        staged_query = args.staged_prompt.read_text(encoding="utf-8")
        if staged_query != query:
            raise ValueError("Staged prompt does not exactly match the pinned official row")
        query = staged_query
    run_dir = args.output / f"yahoo-{args.context_len}-{task_id}"
    run = run_harmes(query, cli=args.harmes.resolve(), output_dir=run_dir, timeout=args.timeout)

    sys.path.insert(0, str(source / "src" / "eval"))
    from eval_helpers import synth_process_response

    scored = synth_process_response(row, run["response_text"], MODEL)
    write_json(run_dir / "official-score.json", scored)
    write_json(run_dir / "manifest.json", {
        "benchmark": "OOLONG Yahoo", "benchmark_commit": OOLONG_COMMIT,
        "dataset": "oolongbench/oolong-synth", "dataset_revision": DATASET_REVISION,
        "split": "test", "source_subset": row["dataset"], "task_id": task_id,
        "context_len": int(row["context_len"]), "input_format": "context_window_text + newline + question",
        "context_sha256": hashlib.sha256(row["context_window_text"].encode("utf-8")).hexdigest(),
        "harmes_commit": HARMES_COMMIT, "provider": PROVIDER, "model": MODEL,
        "harmes_session_id": run["session_id"], "model_reported": run["model_reported"],
        "started_at": run["started_at"], "ended_at": utc_now(), "duration_ms": run["duration_ms"],
        "tokens": run["tokens"], "harmes_exit_code": run["process_exit_code"],
        "harmes_successful": run["successful"], "error": run["error"],
        "harmes_raw_stream": run["raw_stream"], "harmes_stderr": run["stderr_log"],
        "official_score_file": str(run_dir / "official-score.json"),
    })
    print(f"Raw Harmes stream: {run['raw_stream']}")
    print(f"Official score record: {run_dir / 'official-score.json'}")
    return 0 if run["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
