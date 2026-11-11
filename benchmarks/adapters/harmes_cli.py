"""Shared, one-shot Harmes CLI transport for benchmark-only adapters."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HARMES_COMMIT = "f42f579cf8bac4918ac9599bece71618afadd846"
PROVIDER = "openai-codex"
MODEL = "gpt-6-luna"
ROOT = Path(__file__).resolve().parents[2]


def git_commit(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def check_harmes(cli: Path) -> None:
    if git_commit(ROOT) != HARMES_COMMIT:
        raise RuntimeError(f"Harmes checkout must be {HARMES_COMMIT}")
    if not cli.is_file():
        raise FileNotFoundError(f"Harmes CLI not found: {cli}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_harmes(query: str, *, cli: Path, output_dir: Path, timeout: int) -> dict:
    """Run exactly one fresh Harmes CLI session and preserve its raw JSONL stream."""
    cli = cli.resolve()
    output_dir = output_dir.resolve()
    check_harmes(cli)
    output_dir.mkdir(parents=True, exist_ok=False)
    prompt_path = output_dir / "prompt.txt"
    stdout_path = output_dir / "harmes-stream.jsonl"
    stderr_path = output_dir / "harmes-stderr.log"
    prompt_path.write_text(query, encoding="utf-8")

    env = os.environ.copy()
    for secret_name in ("OPENAI_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
        env.pop(secret_name, None)
    command = [
        str(cli), "chat", "--query-file", str(prompt_path), "--format", "stream-json",
        "--provider", PROVIDER, "--model", MODEL,
    ]
    started = utc_now()
    start_clock = time.monotonic()
    process = subprocess.run(
        command, cwd=output_dir, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    elapsed_ms = round((time.monotonic() - start_clock) * 1000)
    stdout_path.write_text(process.stdout, encoding="utf-8")
    stderr_path.write_text(process.stderr, encoding="utf-8")

    events = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    init = next((item for item in events if item.get("type") == "system" and item.get("subtype") == "init"), {})
    result = next((item for item in reversed(events) if item.get("type") == "result"), {})
    model_matches = init.get("model") == MODEL
    exit_code = result.get("exit_code", process.returncode)
    return {
        "started_at": started,
        "ended_at": utc_now(),
        "duration_ms": elapsed_ms,
        "command": command,
        "process_exit_code": process.returncode,
        "session_id": result.get("session_id") or init.get("session_id"),
        "model_reported": init.get("model"),
        "model_matches": model_matches,
        "provider": PROVIDER,
        "model": MODEL,
        "harmes_commit": HARMES_COMMIT,
        "tokens": result.get("tokens", {}),
        "response_text": result.get("text", ""),
        "successful": process.returncode == 0 and exit_code == 0 and model_matches and bool(result),
        "error": result.get("error"),
        "raw_stream": str(stdout_path),
        "stderr_log": str(stderr_path),
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
