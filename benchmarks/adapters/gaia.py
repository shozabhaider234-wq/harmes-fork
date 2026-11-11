"""Run one authorized GAIA validation record through original Harmes."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

from harmes_cli import HARMES_COMMIT, MODEL, PROVIDER, ROOT, run_harmes, utc_now, write_json

DATASET = "gaia-benchmark/GAIA"
DATASET_REVISION = "682dd723ee1e1697e00360edccf2366dc8418dd9"
SCORER_REPO = "gaia-benchmark/leaderboard"
SCORER_REVISION = "1349a17979f0aca0ee9c46cd7ec26eb2fb41102e"


def official_scorer():
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(
        repo_id=SCORER_REPO,
        repo_type="space",
        filename="scorer.py",
        revision=SCORER_REVISION,
    )
    spec = importlib.util.spec_from_file_location("gaia_official_scorer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import pinned GAIA scorer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.question_scorer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True, help="Exact GAIA validation task ID")
    parser.add_argument("--harmes", type=Path, default=ROOT / ".venv" / "Scripts" / "hermes.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks" / "artifacts" / "gaia")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--execute", action="store_true", help="Required to load a task or contact the model")
    args = parser.parse_args()
    if not args.execute:
        parser.error("refusing to load a GAIA task without --execute")

    from datasets import load_dataset
    from huggingface_hub import hf_hub_download

    rows = load_dataset(
        DATASET,
        "2023_level1",
        split="validation",
        revision=DATASET_REVISION,
    )
    row = next((item for item in rows if str(item["task_id"]) == args.task_id), None)
    if row is None:
        raise KeyError(f"No task {args.task_id!r} in the pinned GAIA level-1 validation split")

    run_dir = args.output / args.task_id
    workspace = run_dir.with_name(run_dir.name + "-workspace")
    workspace.mkdir(parents=True, exist_ok=False)
    question = str(row["Question"])
    attachment_path = row.get("file_path")
    if attachment_path:
        source_file = Path(hf_hub_download(
            repo_id=DATASET,
            repo_type="dataset",
            filename=str(attachment_path),
            revision=DATASET_REVISION,
        ))
        local_file = workspace / Path(str(row.get("file_name") or attachment_path)).name
        shutil.copyfile(source_file, local_file)
        query = f"{question}\n\nThe task attachment is available at: {local_file.resolve()}"
    else:
        query = question

    run = run_harmes(query, cli=args.harmes.resolve(), output_dir=run_dir, timeout=args.timeout)
    score = None
    scorer_error = None
    if run["successful"]:
        try:
            score = bool(official_scorer()(run["response_text"], str(row["Final answer"])))
        except Exception as exc:
            scorer_error = f"{type(exc).__name__}: {exc}"

    write_json(run_dir / "manifest.json", {
        "benchmark": "GAIA",
        "dataset": DATASET,
        "dataset_revision": DATASET_REVISION,
        "split": "validation",
        "config": "2023_level1",
        "scorer_repo": SCORER_REPO,
        "scorer_revision": SCORER_REVISION,
        "task_id": args.task_id,
        "harmes_commit": HARMES_COMMIT,
        "provider": PROVIDER,
        "model": MODEL,
        "started_at": run["started_at"],
        "ended_at": utc_now(),
        "duration_ms": run["duration_ms"],
        "harmes_session_id": run["session_id"],
        "model_reported": run["model_reported"],
        "tokens": run["tokens"],
        "harmes_successful": run["successful"],
        "final_response": run["response_text"],
        "official_score": score,
        "official_scorer_error": scorer_error,
        "harmes_raw_stream": run["raw_stream"],
        "harmes_stderr": run["stderr_log"],
        "workspace": str(workspace.resolve()),
    })
    print(f"Raw Harmes stream: {run['raw_stream']}")
    print(f"Official score: {score!r}" if scorer_error is None else f"Official scorer failed: {scorer_error}")
    return 0 if run["successful"] and scorer_error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
