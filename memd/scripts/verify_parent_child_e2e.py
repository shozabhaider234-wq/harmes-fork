"""Live OAuth receipt check; run with the existing .memd-test-venv interpreter.

Uses the repository's finite-session launcher and its normal synchronous
delegation fallback. Does not claim background/subsequent-turn coverage.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def worker(home: Path, evidence: Path, marker: str, query: str) -> int:
    # No imports of Hermes before the isolated home is selected.
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_RUNTIME_DIR"] = str(home / "runtime")
    for key in ("HERMES_PROFILE", "HERMES_IGNORE_USER_CONFIG", "HERMES_SESSION_KEY",
                "HERMES_SESSION_SOURCE", "HERMES_UI_SESSION_ID"):
        os.environ.pop(key, None)
    lock = threading.Lock()

    def emit(event, **data):
        with lock, evidence.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "time_ns": time.time_ns(), **data},
                                    ensure_ascii=False) + "\n")

    from hermes_cli import auth, auth_codex

    def no_refresh(*args, **kwargs):
        raise RuntimeError("OAuth refresh is disabled for this receipt check; existing credentials were preserved")

    # A copied refresh grant must never be consumed: Codex rotates single-use grants.
    for module in (auth, auth_codex):
        module.refresh_codex_oauth_pure = no_refresh
        module._refresh_codex_auth_tokens = no_refresh
    original_resolve = auth.resolve_codex_runtime_credentials

    def resolve_existing(**kwargs):
        return original_resolve(read_only=True)

    auth.resolve_codex_runtime_credentials = resolve_existing
    auth_codex.resolve_codex_runtime_credentials = resolve_existing
    if not (home / "auth.json").is_file():
        tokens = auth._import_codex_cli_tokens()  # Repository's read-only external-login reader.
        if not tokens:
            raise RuntimeError("No valid existing Codex login found; select the authenticated Hermes profile with --source-home")
        (home / "auth.json").write_text("{}", encoding="utf-8")
        auth._save_codex_tokens(tokens)  # Writes only the disposable profile; never the source login.
        emit("oauth_source", source="existing_codex_login", refresh_enabled=False)
    else:
        emit("oauth_source", source="temporary_hermes_auth_copy", refresh_enabled=False)

    from plugins import memory
    original_load = memory.load_memory_provider

    def load(name):
        provider = original_load(name)
        if name != "hermes-memd" or provider is None or getattr(provider, "_e2e_observed", False):
            return provider
        provider._e2e_observed = True
        original_initialize = provider.initialize
        original_recall = provider.recall
        original_handle = provider.handle_tool_call

        def initialize(session_id, **kwargs):
            result = original_initialize(session_id, **kwargs)
            emit("provider_initialized", session_id=session_id,
                 context=kwargs.get("agent_context", "primary"),
                 parent_session_id=kwargs.get("parent_session_id"),
                 actions=provider.get_tool_schemas()[0]["parameters"]["properties"]["action"]["enum"])
            return result

        def recall(*args, **kwargs):
            package = original_recall(*args, **kwargs)
            state = provider._binding()[1]
            emit("recall", session_id=state["session_id"], context=state["context"],
                 retrieved_count=package.retrieved_count, selected_count=package.selected_count,
                 selected_memory_ids=package.selected_memory_ids,
                 token_count=package.token_count, token_budget=package.token_budget,
                 budget_reason=package.budget_reason, token_count_method=package.token_count_method,
                 marker_present=marker in package.text)
            return package

        def handle(tool_name, args, **kwargs):
            state = provider._binding()[1]
            emit("memory_tool", session_id=state["session_id"], context=state["context"],
                 tool=tool_name, action=args.get("action"))
            return original_handle(tool_name, args, **kwargs)

        provider.initialize, provider.recall, provider.handle_tool_call = initialize, recall, handle
        return provider

    memory.load_memory_provider = load
    from run_agent import AIAgent
    original_init = AIAgent.__init__
    original_run = AIAgent.run_conversation
    seeded = False

    def initialize_agent(self, *args, **kwargs):
        nonlocal seeded
        # Isolate the live receipt from checkout instructions and background learning.
        kwargs.update(skip_context_files=True, skip_background_review=True)
        kwargs["max_iterations"] = min(kwargs.get("max_iterations", 12), 12)
        original_init(self, *args, **kwargs)
        names = [tool.get("function", {}).get("name") for tool in self.tools]
        emit("agent_initialized", session_id=self.session_id, platform=self.platform,
             parent_session_id=self._parent_session_id, provider=self.provider,
             model=self.model, api_mode=self.api_mode, tools=names)
        assert self.provider == "openai-codex" and self.model == "gpt-6-luna", "Provider/model substitution"
        assert names.count("memd_memory") == 1, "Missing or duplicate memd tool"
        if self.platform != "subagent" and not seeded:
            assert "delegate_task" in names
            assert marker not in str(getattr(self, "_cached_system_prompt", ""))
            provider = self._memory_manager.providers[0]
            assert provider.name == "hermes-memd"
            memory_id = provider.ingest_memory({"content": f"{query}: verification receipt is {marker}."})
            seeded = True
            emit("memory_seeded", memory_id=memory_id, parent_session_id=self.session_id)
            # Supported provider read, outside the conversation, proves primary access.
            assert marker in provider.recall(query).text
        elif self.platform == "subagent":
            assert "memory" not in names
            schema = next(t["function"] for t in self.tools if t["function"]["name"] == "memd_memory")
            assert set(schema["parameters"]["properties"]["action"]["enum"]) == {"recall", "history", "source"}

    def run(self, *args, **kwargs):
        prompt = kwargs.get("user_message", args[0] if args else "")
        emit("turn_started", session_id=self.session_id, platform=self.platform,
             prompt_contains_marker=marker in str(prompt))
        result = original_run(self, *args, **kwargs)
        if self.platform != "subagent":
            # Prove the receipt did not enter through a regenerated native-memory prefix.
            clean_prefix = marker not in str(getattr(self, "_cached_system_prompt", ""))
            emit("parent_prefix_checked", session_id=self.session_id, marker_absent=clean_prefix)
            assert clean_prefix, "Receipt leaked into the parent's system prompt"
        # Record only visible messages and tool events, never hidden reasoning or runtime credentials.
        messages = [{k: m[k] for k in ("role", "content", "tool_calls", "tool_call_id", "name") if k in m}
                    for m in result.get("messages", [])]
        emit("turn_completed", session_id=self.session_id, platform=self.platform,
             completed=result.get("completed"), failed=result.get("failed", False),
             final_response=result.get("final_response", ""), messages=messages,
             provider=self.provider, model=self.model)
        return result

    AIAgent.__init__, AIAgent.run_conversation = initialize_agent, run
    from hermes_cli.oneshot import run_oneshot
    prompt = (
        f"Delegate exactly one task using delegate_task. The child must call memd_memory "
        f"with action='recall', query='{query}', retrieve the verification receipt, "
        "and return the exact MEMD_PARENT_CHILD_E2E_ receipt found in that memory. "
        "Do not supply a model override. You must not recall memory yourself. "
        "Use only the child's completed result to answer. Your final answer must "
        "quote its receipt and explain that it came from the delegated memory recall. "
        "If dispatch is asynchronous, end the turn and use the normal completion when delivered."
    )
    assert marker not in prompt
    return run_oneshot(prompt, provider="openai-codex", model="gpt-6-luna",
                       toolsets=["delegation", "memory"])


def summarize(events, returncode):
    parents = [e for e in events if e["event"] == "agent_initialized" and e["platform"] != "subagent"]
    children = [e for e in events if e["event"] == "agent_initialized" and e["platform"] == "subagent"]
    turns = [e for e in events if e["event"] == "turn_completed"]
    parent_turns = [e for e in turns if e["platform"] != "subagent"]
    child_turns = [e for e in turns if e["platform"] == "subagent"]
    marker = next((e["marker"] for e in events if e["event"] == "test_started"), "")
    calls, delivered = [], []
    for turn in parent_turns:
        names = {}
        for message in turn["messages"]:
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call.get("function", {}).get("name")
                if names[call["id"]] == "delegate_task":
                    calls.append(call)
            if message.get("role") == "tool" and names.get(message.get("tool_call_id")) == "delegate_task":
                try:
                    payload = json.loads(message.get("content", ""))
                except (ValueError, TypeError):
                    continue
                if any(r.get("status") == "completed" and marker in r.get("summary", "")
                       for r in payload.get("results", [])):
                    delivered.append(payload)
    child_ids = {e["session_id"] for e in children}
    recalled = any(e["event"] == "recall" and e.get("session_id") in child_ids
                   and e.get("marker_present") and e["selected_count"] > 0
                   and e["token_count"] <= e["token_budget"] for e in events)
    child_tool = any(e["event"] == "memory_tool" and e.get("session_id") in child_ids
                     and e.get("action") == "recall" for e in events)
    parent_recalled = any(e["event"] == "memory_tool" and e.get("context") == "primary"
                          and e.get("action") == "recall" for e in events)
    child_done = any(e.get("completed") is True and marker in e["final_response"] for e in child_turns)
    used = bool(marker and parent_turns and parent_turns[-1].get("completed") is True
                and marker in parent_turns[-1]["final_response"] and not parent_recalled)
    launched = any(e["provider"] == "openai-codex" and e["model"] == "gpt-6-luna"
                   and e["tools"].count("memd_memory") == 1 and "delegate_task" in e["tools"]
                   for e in parents)
    stages = dict(zip(("Parent launched", "Delegation invoked", "Child memory recall", "Child completed",
                       "Result delivered to parent", "Parent used retrieved memory"),
                      (launched, bool(calls), recalled and child_tool, child_done, bool(delivered), used and bool(delivered))))
    return {"stages": {k: "Pass" if v else "Fail" for k, v in stages.items()},
            "success": all(stages.values()) and returncode == 0,
            "launcher_exit_code": returncode, "parent_session_ids": [e["session_id"] for e in parents],
            "child_session_ids": list(child_ids), "delegation_invocations": calls,
            "delivery_payloads": delivered,
            "parent_final_answer": parent_turns[-1]["final_response"] if parent_turns else None,
            "background_subsequent_turn_verified": False,
            "delivery_mode": "finite-session normal inline delegation fallback"}


def self_check():
    """A child-only success must never pass the parent-delivery stages."""
    marker = "MEMD_PARENT_CHILD_E2E_selfcheck"
    events = [{"event": "test_started", "marker": marker},
              {"event": "agent_initialized", "platform": "cli", "session_id": "parent",
               "provider": "openai-codex", "model": "gpt-6-luna", "tools": ["memd_memory", "delegate_task"]},
              {"event": "agent_initialized", "platform": "subagent", "session_id": "child"},
              {"event": "memory_tool", "session_id": "child", "action": "recall"},
              {"event": "recall", "session_id": "child", "marker_present": True, "selected_count": 1,
               "token_count": 50, "token_budget": 1000},
              {"event": "turn_completed", "platform": "subagent", "completed": True,
               "final_response": marker}]
    report = summarize(events, 0)
    assert report["stages"]["Child completed"] == "Pass"
    assert report["stages"]["Result delivered to parent"] == "Fail"
    assert report["success"] is False
    events.append({"event": "turn_completed", "platform": "cli", "completed": True,
                   "final_response": marker, "messages": [
                       {"role": "assistant", "tool_calls": [{"id": "call", "function": {"name": "delegate_task"}}]},
                       {"role": "tool", "tool_call_id": "call", "content": json.dumps(
                           {"results": [{"status": "completed", "summary": marker}]})}]})
    assert summarize(events, 0)["success"] is True
    events[-1]["final_response"] = "Waiting for the child."
    assert summarize(events, 0)["success"] is False
    print("Receipt checker self-check passed (synthetic checker data; not a live test).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-home", type=Path, default=Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")))
    parser.add_argument("--endpoint", help="Existing daemon endpoint; defaults to source-home/memd.json")
    parser.add_argument("--output", type=Path, default=ROOT / "memd" / ".tmp-parent-child-e2e")
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--self-check", action="store_true", help="Check receipt validation without live model calls")
    parser.add_argument("--worker", nargs=4, metavar=("HOME", "EVIDENCE", "MARKER", "QUERY"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.worker:
        home, evidence, marker, query = args.worker
        return worker(Path(home), Path(evidence), marker, query)
    run_id = uuid.uuid4().hex
    output = args.output.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "events.jsonl"
    marker, query = f"MEMD_PARENT_CHILD_E2E_{run_id}", f"receipt lookup {run_id[:12]}"
    endpoint = args.endpoint or json.loads((args.source_home / "memd.json").read_text(encoding="utf-8-sig"))["endpoint"]
    config = {"endpoint": endpoint, "namespace": f"parent-child-e2e-{run_id}", "token_budget": 1000}
    evidence.write_text(json.dumps({"event": "test_started", "marker": marker, "query": query}) + "\n", encoding="utf-8")
    home = Path(tempfile.mkdtemp(prefix="hermes-memd-e2e-"))
    cleanup_ok = False
    worker_started = False
    try:
        shutil.copytree(ROOT / "memd" / "clients" / "python", home / "plugins" / "hermes-memd",
                        ignore=shutil.ignore_patterns("__pycache__"))
        # No refresh grant may be spent; worker enforces read-only credential resolution.
        auth = args.source_home / "auth.json"
        if auth.is_file():
            shutil.copy2(auth, home / "auth.json")
        (home / "memd.json").write_text(json.dumps(config), encoding="utf-8")
        (home / "config.yaml").write_text(
            "model:\n  default: gpt-6-luna\n  provider: openai-codex\n"
            "memory:\n  provider: hermes-memd\n  memory_enabled: true\n  user_profile_enabled: true\n"
            "delegation:\n  memd:\n    enabled: true\n  max_iterations: 8\n  child_timeout_seconds: 180\n"
            "  model: gpt-6-luna\n  provider: openai-codex\n", encoding="utf-8")
        # Explicitly connect only: never auto-start, replace or stop the user's daemon.
        from memd.clients.python.memory_client import MemdClient
        from memd.clients.python.hermes_bridge import endpoint_options
        try:
            with MemdClient(**endpoint_options(endpoint), timeout=5) as client:
                client.ping()
        except OSError as error:
            report = summarize([], 2)
            report["stages"] = {stage: "Blocked" for stage in report["stages"]}
            report["error"] = f"Existing memd endpoint {endpoint!r} is unavailable: {error}"
            report["worker_started"] = False
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
            print(f"Evidence: {output}")
            return 2
        env = dict(os.environ, HERMES_HOME=str(home), HERMES_RUNTIME_DIR=str(home / "runtime"))
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(home), str(evidence), marker, query]
        # Capture the isolated launcher's visible output; credentials are never printed by this harness.
        with (output / "launcher.txt").open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(command, cwd=home, env=env, stdout=stream, stderr=subprocess.STDOUT)
            worker_started = True
            try:
                code = process.wait(timeout=args.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                process.terminate()  # Only our worker; no daemon was started by this check.
                process.wait(timeout=15)
                code = 124
        events = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()]
        report = summarize(events, code)
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        print(f"Evidence: {output}")
        # Preserve native session/tracing evidence, excluding credentials/config/runtime.
        for folder in ("sessions", "logs", "cache/delegation"):
            source = home / folder
            if source.exists():
                shutil.copytree(source, output / folder, dirs_exist_ok=True)
        if (home / "state.db").exists():
            import sqlite3
            # sqlite3's own context manager ends a transaction; it does not close the file.
            with closing(sqlite3.connect(home / "state.db")) as source_db, closing(sqlite3.connect(output / "state.db")) as target_db:
                source_db.backup(target_db)
        return 0 if report["success"] else 1
    finally:
        # Scope cleanup to this run's profile hash and unique namespace, including native user target.
        try:
            if worker_started:
                from memd.clients.python.memory_client import MemdClient
                from memd.clients.python.hermes_bridge import endpoint_options, profile_identity
                with MemdClient(**endpoint_options(endpoint), timeout=5) as client:
                    for suffix in ("", ":user"):
                        scope = {"namespace": config["namespace"] + ":" + profile_identity(home) + suffix,
                                 "access_scope": "private", "agent_id": None}
                        for record in client.inventory(scope):
                            client.delete_memory(record["id"])
                        assert not client.inventory(scope), "Synthetic cleanup incomplete"
            # Before worker creation there cannot be a synthetic write to clean up.
            cleanup_ok = True
        finally:
            # home was allocated by mkdtemp above; never remove the source profile or evidence.
            try:
                shutil.rmtree(home)
            finally:
                with evidence.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"event": "cleanup", "synthetic_namespace_empty": cleanup_ok,
                                             "daemon_cleanup_skipped": not worker_started,
                                             "isolated_home_removed": not home.exists()}) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
