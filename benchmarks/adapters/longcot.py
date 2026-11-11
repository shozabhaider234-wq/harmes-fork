"""One-task LongCoT-Mini adapter. No task runs without --execute."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from harmes_cli import HARMES_COMMIT, MODEL, PROVIDER, ROOT, git_commit, run_harmes, utc_now, write_json

LONGCOT_COMMIT = "fb9649423f15f5b0091f8e988b100596cac592ca"


def official_questions(source: Path):
    evaluator = source / "run_eval.py"
    spec = importlib.util.spec_from_file_location("longcot_official_evaluator", evaluator)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import official evaluator: {evaluator}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_all_questions()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Pinned official LongCoT checkout")
    parser.add_argument("--domain", default="logic")
    parser.add_argument("--difficulty", default="easy")
    parser.add_argument("--question-id", default="BlocksWorld_easy_1")
    parser.add_argument("--harmes", type=Path, default=ROOT / ".venv" / "Scripts" / "hermes.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks" / "artifacts" / "longcot")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--execute", action="store_true", help="Required to load and execute the selected task")
    args = parser.parse_args()
    if not args.execute:
        parser.error("refusing to load or execute a task without --execute")

    source = args.source.resolve()
    if git_commit(source) != LONGCOT_COMMIT:
        raise RuntimeError(f"LongCoT checkout must be {LONGCOT_COMMIT}")
    task_key = (args.domain, args.difficulty, args.question_id)
    question = official_questions(source).get(task_key)
    if question is None:
        raise KeyError(f"No official LongCoT task matches {task_key!r}")

    run_dir = args.output / args.question_id
    run = run_harmes(question.prompt, cli=args.harmes.resolve(), output_dir=run_dir, timeout=args.timeout)
    response_path = run_dir / "official-responses.jsonl"
    response = {
        "question_id": question.question_id,
        "domain": question.domain,
        "difficulty": question.difficulty,
        "successful": run["successful"],
        "response_text": run["response_text"],
    }
    response_path.write_text(json.dumps(response, ensure_ascii=False) + "\n", encoding="utf-8")

    evaluator_env = os.environ.copy()
    evaluator_env["PYTHONPATH"] = str(source / "src") + os.pathsep + evaluator_env.get("PYTHONPATH", "")
    score_path = run_dir / "official-score.json"
    evaluator = subprocess.run(
        [sys.executable, str(source / "run_eval.py"), str(response_path), "--output", str(score_path), "--no-fallback"],
        cwd=source, env=evaluator_env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    (run_dir / "scorer-stdout.log").write_text(evaluator.stdout, encoding="utf-8")
    (run_dir / "scorer-stderr.log").write_text(evaluator.stderr, encoding="utf-8")
    write_json(run_dir / "manifest.json", {
        "benchmark": "LongCoT-Mini", "benchmark_commit": LONGCOT_COMMIT,
        "harmes_commit": HARMES_COMMIT, "provider": PROVIDER, "model": MODEL,
        "task": task_key, "started_at": run["started_at"], "ended_at": utc_now(),
        "duration_ms": run["duration_ms"], "harmes_exit_code": run["process_exit_code"],
        "harmes_session_id": run["session_id"], "harmes_successful": run["successful"],
        "model_reported": run["model_reported"], "tokens": run["tokens"], "error": run["error"],
        "harmes_raw_stream": run["raw_stream"], "harmes_stderr": run["stderr_log"],
        "official_response_file": str(response_path), "official_score_file": str(score_path),
        "official_scorer_exit_code": evaluator.returncode,
    })
    print(f"Raw Harmes stream: {run['raw_stream']}")
    print(f"Official score record: {score_path}")
    return 0 if evaluator.returncode == 0 else evaluator.returncode


if __name__ == "__main__":
    raise SystemExit(main())
