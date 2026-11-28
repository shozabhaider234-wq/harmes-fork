"""End-to-end compact-context lineage check against the persistent Rust daemon.

Run with ``scripts/run_tests.sh tests/agent/test_compact_initial_context_rust_e2e.py``.
This deliberately uses the real memd executable, real MiniLM embeddings, Hermes SessionDB,
and AIAgent compression path. The ContextCompressor's existing deterministic fallback
summary is selected so no external model call or provider usage is implied.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
MEMD = ROOT / "memd" / "target-gnu" / "release" / "memd.exe"
MODEL = ROOT / "memd" / "models" / "all-MiniLM-L6-v2"
ORT = ROOT / "memd" / "onnxruntime" / "onnxruntime-win-x64-1.31.1" / "lib" / "onnxruntime.dll"
DECISION = (
    "Decision: PROJECT_DECISION_E2E_001: The project's metadata index will use SQLite, "
    "and vector retrieval will use HNSW."
)


class _Daemon:
    def __init__(self, database: Path, log_path: Path):
        self.database = database
        self.log_path = log_path
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.endpoint = f"tcp:127.0.0.1:{self.port}"
        self.process = None

    def start(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [str(MEMD), "--tcp", f"127.0.0.1:{self.port}", "--store", str(self.database),
             "--model-dir", str(MODEL), "--model-revision", "model-v1", "--ort-dylib", str(ORT)],
            cwd=ROOT / "memd", stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        from memd.clients.python.memory_client import MemdClient

        last = None
        for _ in range(150):
            if self.process.poll() is not None:
                break
            try:
                with MemdClient(tcp_address=("127.0.0.1", self.port), timeout=1) as client:
                    client.ping()
                return
            except Exception as exc:  # daemon startup includes loading real model weights
                last = exc
                time.sleep(0.1)
        self.stop()
        detail = self.log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
        raise RuntimeError(f"real memd failed to start ({last}):\n{detail}")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if getattr(self, "_log", None) and not self._log.closed:
            self._log.close()


def _process_rss(pid):
    try:
        import psutil

        return int(psutil.Process(pid).memory_info().rss)
    except Exception:
        return None


def _wire_agent(agent, provider, session_id):
    from agent.memory_manager import MemoryManager

    manager = MemoryManager()
    manager.add_provider(provider)
    agent._memory_manager = manager
    agent._compact_initial_context_requested = True
    agent._compact_initial_context_extract_on_compaction = True
    agent._initial_context_token_budget = 800
    agent._initial_context_allow_create = True
    agent._memory_enabled = True
    agent._user_profile_enabled = True
    agent._session_db.create_session(session_id, "cli", model="test/model")
    return manager


def test_real_rust_memory_survives_hermes_compaction_rotation_and_resume(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("workspace release binary and ONNX Runtime fixture are Windows builds")
    for required in (MEMD, MODEL / "model.onnx", ORT):
        if not required.is_file():
            pytest.skip(f"real Rust/ONNX fixture is unavailable: {required}")

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    config = {"memory": {"provider": "hermes-memd", "memory_enabled": True,
                         "user_profile_enabled": True,
                         "compact_initial_context": {"enabled": True, "token_budget": 800}}}
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")

    database = tmp_path / "isolated-rust-memory.sqlite3"
    daemon = _Daemon(database, tmp_path / "memd.log")
    daemon.start()
    test_started = time.monotonic()
    events = []
    retrieval_samples = []
    phase = {"name": "startup"}
    provider = None
    db = None
    try:
        from memd.clients.python.hermes_bridge import MemdMemoryProvider, profile_identity
        from memd.clients.python.memory_client import MemdClient
        from hermes_state import SessionDB
        from run_agent import AIAgent
        from tools.memory_tool_store import MemoryStore

        original_retrieve = MemdClient.retrieve

        def timed_retrieve(client, *args, **kwargs):
            started = time.monotonic()
            try:
                return original_retrieve(client, *args, **kwargs)
            finally:
                retrieval_samples.append({"phase": phase["name"], "elapsed_ms": round((time.monotonic() - started) * 1000, 2)})

        monkeypatch.setattr(MemdClient, "retrieve", timed_retrieve)

        provider_config = {
            "endpoint": daemon.endpoint, "namespace": "lineage-e2e", "top_k": 20,
            "token_budget": 800, "safe_available_tokens": 800,
            "daemon_path": str(MEMD), "model_dir": str(MODEL), "store_path": str(database),
            "ort_dylib": str(ORT),
        }
        provider = MemdMemoryProvider(provider_config)
        provider.initialize("lineage-A", hermes_home=home, agent_context="primary")
        scope = {"namespace": "lineage-e2e:" + profile_identity(home), "access_scope": "private"}
        with MemdClient(tcp_address=("127.0.0.1", daemon.port), timeout=10) as client:
            assert client.ping()
            assert client.inventory(scope) == []
            assert client.inventory({**scope, "namespace": "lineage-e2e:other-profile"}) == []

        db = SessionDB(db_path=home / "state.db")
        agent = AIAgent(
            api_key="deterministic-test-only", base_url="http://127.0.0.1:1/v1", model="test/model",
            quiet_mode=True, session_db=db, session_id="lineage-A", skip_context_files=True,
            skip_memory=True,
        )
        manager = _wire_agent(agent, provider, "lineage-A")
        legacy = MemoryStore()
        legacy._system_prompt_snapshot = {"memory": "LEGACY_MEMORY_SENTINEL", "user": "LEGACY_USER_SENTINEL"}
        agent._memory_store = legacy
        agent._initial_context_token_counter = lambda value: (len(value) + 3) // 4

        original_build = provider.build_initial_context

        def build_with_event(**kwargs):
            events.append(("context_generation_start", agent.session_id, time.monotonic()))
            text, receipt = original_build(**kwargs)
            events.append(("context_generation_complete", agent.session_id, time.monotonic(), receipt))
            return text, receipt

        provider.build_initial_context = build_with_event
        original_ingest = provider.ingest_memory

        def ingest_with_event(*args, **kwargs):
            started = time.monotonic()
            memory_id = original_ingest(*args, **kwargs)
            events.append(("rust_write_ack", kwargs.get("session_id"), time.monotonic(), memory_id,
                           round((time.monotonic() - started) * 1000, 2)))
            return memory_id

        provider.ingest_memory = ingest_with_event
        sid_a = agent.session_id
        phase["name"] = "lineage_A_initial_context"
        prompt_a = agent._build_system_prompt("")
        agent._cached_system_prompt = prompt_a
        snap_a = db.get_initial_context_snapshot(sid_a)
        assert snap_a and snap_a["token_count"] <= 800 and snap_a["token_budget"] == 800
        assert snap_a["snapshot_hash"] == hashlib.sha256(snap_a["context_text"].encode()).hexdigest()
        assert prompt_a.count(snap_a["context_text"]) == 1
        assert "LEGACY_MEMORY_SENTINEL" not in prompt_a and "LEGACY_USER_SENTINEL" not in prompt_a
        assert DECISION not in prompt_a
        local_token_counter = agent._initial_context_token_counter
        prompt_a_tokens = local_token_counter(prompt_a)

        from agent.turn_request_assembly import build_api_messages
        agent._current_turn_timestamp = time.time()
        wire_a = []
        for _ in range(2):
            outbound, _ = build_api_messages(
                agent, [{"role": "user", "content": "Continue the project."}], current_turn_user_idx=0,
                ext_prefetch_cache="Dynamic retrieval evidence", plugin_user_context="", moa_config=None,
                active_system_prompt=prompt_a,
            )
            wire_a.append(outbound[0]["content"])
            assert outbound[0]["content"].count(snap_a["context_text"]) == 1
            assert "LEGACY_MEMORY_SENTINEL" not in outbound[0]["content"]
            assert "LEGACY_USER_SENTINEL" not in outbound[0]["content"]
            assert "Dynamic retrieval evidence" in outbound[1]["content"]
        assert wire_a[0] == wire_a[1]

        messages = []
        for index in range(10):
            user_text = DECISION if index == 4 else f"User request {index}: retain useful project continuity."
            user_text += "\n" + (f"user-detail-{index} " * 180)
            messages.extend([
                {"role": "user", "content": user_text},
                {"role": "assistant", "content":
                 f"Completed project action {index}; the metadata design remains under discussion.\n"
                 + (f"assistant-detail-{index} " * 220)},
            ])
        messages.append({"role": "user", "content": "Continue with the next implementation step."})
        db.append_message(sid_a, "user", DECISION)
        assert DECISION in "\n".join(str(m.get("content", "")) for m in messages)
        assert DECISION not in agent._cached_system_prompt
        assert agent._build_system_prompt("") == prompt_a
        assert db.get_initial_context_snapshot(sid_a)["snapshot_hash"] == snap_a["snapshot_hash"]

        # The real compressor's documented local fallback summary is deterministic and
        # intentionally avoids an external model call while exercising Hermes commit/rotate.
        compressor = agent.context_compressor
        compressor._compression_feasibility_checked = True
        compressor._feasibility_skip = lambda *_args, **_kwargs: True
        agent.compression_in_place = False
        original_commit = agent.commit_memory_session

        def commit_with_event(*args, **kwargs):
            events.append(("commit_memory_session", sid_a, time.monotonic()))
            return original_commit(*args, **kwargs)

        agent.commit_memory_session = commit_with_event
        original_publish = db.publish_compression_child
        publication_failures = [1]

        def publish_with_event(**kwargs):
            events.append(("child_publication_attempt", kwargs["child_session_id"], time.monotonic()))
            if publication_failures:
                publication_failures.pop()
                events.append(("child_publication_failed", kwargs["child_session_id"], time.monotonic()))
                raise RuntimeError("injected child publication failure after durable extraction")
            result = original_publish(**kwargs)
            events.append(("child_publication_committed", kwargs["child_session_id"], time.monotonic()))
            return result

        db.publish_compression_child = publish_with_event
        phase["name"] = "lineage_B_initial_context"
        agent._compress_context(messages, "", approx_tokens=100_000, task_id="e2e", force=False)
        assert agent.session_id == sid_a
        assert db.get_initial_context_snapshot(sid_a)["snapshot_hash"] == snap_a["snapshot_hash"]
        failed_publication_receipt = db.get_compaction_memory_extraction(f"compression:{sid_a}")
        assert failed_publication_receipt["status"] == "complete"
        assert failed_publication_receipt["result"]["stored"] == 1

        # The next invocation retries the real compression boundary. The Rust receipt
        # is reused, so the write is not repeated and only one child can be published.
        compacted, _ = agent._compress_context(messages, "", approx_tokens=100_000, task_id="e2e", force=True)
        sid_b = agent.session_id
        assert sid_b != sid_a, "Hermes compression did not publish a child lineage"
        assert compacted and any("CONTEXT COMPACTION" in str(row.get("content", "")) for row in compacted)
        snap_b = db.get_initial_context_snapshot(sid_b)
        assert snap_b and snap_b["lineage_id"] == sid_b
        assert snap_b["snapshot_hash"] != snap_a["snapshot_hash"]
        assert snap_b["token_count"] <= 800 and snap_b["token_budget"] == 800
        prompt_b_tokens = local_token_counter(agent._cached_system_prompt)
        assert DECISION.split(": ", 1)[1] in snap_b["context_text"]
        assert db.get_initial_context_snapshot(sid_a)["context_text"] == snap_a["context_text"]
        extraction = db.get_compaction_memory_extraction(f"compression:{sid_a}")
        assert extraction["status"] == "complete" and extraction["result"]["stored"] == 1

        relevant = [event for event in events if event[0] in {
            "commit_memory_session", "rust_write_ack", "child_publication_attempt",
            "child_publication_committed", "context_generation_start",
        }]
        labels = [event[0] for event in relevant]
        assert labels.index("commit_memory_session") < labels.index("rust_write_ack")
        assert labels.index("rust_write_ack") < labels.index("child_publication_attempt")
        committed_publication = next(e for e in relevant if e[0] == "child_publication_committed")
        child_generation = next(e for e in relevant if e[0] == "context_generation_start" and e[1] == sid_b)
        write_ack = next(e for e in relevant if e[0] == "rust_write_ack")
        assert write_ack[2] < child_generation[2], f"event order was {events!r}"
        assert committed_publication[2] < child_generation[2]
        assert len([e for e in events if e[0] == "rust_write_ack"]) == 1
        assert len([e for e in events if e[0] == "child_publication_failed"]) == 1
        assert len([e for e in events if e[0] == "child_publication_committed"]) == 1

        with MemdClient(tcp_address=("127.0.0.1", daemon.port), timeout=10) as client:
            rows = client.inventory(scope)
            found = [row for row in rows if "PROJECT_DECISION_E2E_001" in row.get("content", "")]
            assert len(found) == 1
            stored = found[0]
            assert stored["metadata"]["source_session_id"] == sid_a
            assert stored["metadata"]["compaction_id"] == f"compression:{sid_a}"
            assert stored["session_id"] == sid_a and stored["source_ref"]
            retrieved = client.retrieve("PROJECT_DECISION_E2E_001 SQLite HNSW metadata index", k=5,
                                        scope=scope, mode="hybrid")
            assert any(row.get("id") == stored["id"] for row in retrieved)
            assert client.inventory({**scope, "namespace": scope["namespace"] + ":user"}) == []
            assert client.inventory({**scope, "namespace": "lineage-e2e:other-profile"}) == []

        # The actual request-message builder keeps the compact block in system[0] and
        # task-specific evidence in the separate user message on repeated calls.
        wire_systems = []
        for _ in range(2):
            outbound, _effective = build_api_messages(
                agent, [{"role": "user", "content": "Recall the project decision."}],
                current_turn_user_idx=0, ext_prefetch_cache="Dynamic retrieval evidence",
                plugin_user_context="", moa_config=None, active_system_prompt=agent._cached_system_prompt,
            )
            wire_systems.append(outbound[0]["content"])
            assert outbound[0]["content"].count(snap_b["context_text"]) == 1
            assert "LEGACY_MEMORY_SENTINEL" not in outbound[0]["content"]
            assert "LEGACY_USER_SENTINEL" not in outbound[0]["content"]
            assert "Dynamic retrieval evidence" in outbound[1]["content"]
            assert "Dynamic retrieval evidence" not in outbound[0]["content"]
        assert wire_systems[0] == wire_systems[1]

        # Recreate the provider and agent after both process restarts. The Rust DB is
        # the same isolated persistent SQLite file; the session snapshot is read-only.
        manager.shutdown_all()
        daemon.stop()
        db.close()
        daemon = _Daemon(database, tmp_path / "memd-restarted.log")
        daemon.start()
        provider_config["endpoint"] = daemon.endpoint
        provider2 = MemdMemoryProvider(provider_config)
        provider2.initialize(sid_b, hermes_home=home, agent_context="primary")
        db = SessionDB(db_path=home / "state.db")
        resumed = AIAgent(
            api_key="deterministic-test-only", base_url="http://127.0.0.1:1/v1", model="test/model",
            quiet_mode=True, session_db=db, session_id=sid_b, skip_context_files=True, skip_memory=True,
        )
        _wire_agent(resumed, provider2, sid_b)
        resumed._initial_context_allow_create = False
        resumed._initial_context_token_counter = lambda value: (len(value) + 3) // 4
        phase["name"] = "lineage_B_resume"
        resume_started = time.monotonic()
        resumed_prompt = resumed._build_system_prompt("")
        resume_elapsed_ms = round((time.monotonic() - resume_started) * 1000, 2)
        snap_b_resumed = db.get_initial_context_snapshot(sid_b)
        assert resumed.session_id == sid_b
        assert snap_b_resumed["context_text"] == snap_b["context_text"]
        assert snap_b_resumed["snapshot_hash"] == snap_b["snapshot_hash"]
        assert resumed_prompt.count(snap_b["context_text"]) == 1
        assert db.get_compaction_memory_extraction(f"compression:{sid_a}")["result"]["stored"] == 1
        from agent.context_compressor import is_compaction_summary_message
        assert any(is_compaction_summary_message(row) for row in db.get_messages_as_conversation(sid_b))
        with MemdClient(tcp_address=("127.0.0.1", daemon.port), timeout=10) as client:
            rows_after_restart = client.inventory(scope)
            assert sum("PROJECT_DECISION_E2E_001" in row.get("content", "") for row in rows_after_restart) == 1
            assert client.inventory({**scope, "namespace": "lineage-e2e:other-profile"}) == []

        # Fault-inject a real transport outage at the extraction inventory boundary.
        # The already-committed child snapshot and durable memory must remain unchanged.
        from agent.compaction_memory_extraction import extract_before_child_context
        snapshot_before_outage = db.get_initial_context_snapshot(sid_b)
        daemon.stop()
        phase["name"] = "daemon_unavailable_extraction"
        outage = extract_before_child_context(
            resumed,
            [{"role": "user", "content": "Decision: Offline recovery probe is isolated from project memory."}],
            sid_b,
        )
        assert outage["status"] == "incomplete" and outage.get("error")
        assert db.get_initial_context_snapshot(sid_b)["snapshot_hash"] == snapshot_before_outage["snapshot_hash"]
        daemon.start()
        outage_retry = extract_before_child_context(
            resumed,
            [{"role": "user", "content": "Decision: Offline recovery probe is isolated from project memory."}],
            sid_b,
        )
        assert outage_retry["reused"] is True and outage_retry["status"] == "incomplete"

        with MemdClient(tcp_address=("127.0.0.1", daemon.port), timeout=10) as client:
            rows_after_restart = client.inventory(scope)
            assert sum("PROJECT_DECISION_E2E_001" in row.get("content", "") for row in rows_after_restart) == 1
            phase["name"] = "dynamic_recall"
            dynamic_packages = []
            dynamic_latencies = []
            for _ in range(3):
                retrieval_started = time.monotonic()
                dynamic = provider2.recall("PROJECT_DECISION_E2E_001 SQLite HNSW", session_id=sid_b,
                                           token_budget=300, detail="brief")
                dynamic_latencies.append(round((time.monotonic() - retrieval_started) * 1000, 2))
                dynamic_packages.append(dynamic)
            dynamic = dynamic_packages[-1]
            assert "PROJECT_DECISION_E2E_001" in dynamic.text
            assert dynamic.token_count <= dynamic.token_budget

        # Retry the same extraction boundary: its durable receipt prevents another write.
        before_retry = len(rows_after_restart)
        retry = extract_before_child_context(resumed, messages, sid_a)
        assert retry["reused"] is True
        with MemdClient(tcp_address=("127.0.0.1", daemon.port), timeout=10) as client:
            assert len(client.inventory(scope)) == before_retry
        assert db.get_initial_context_snapshot(sid_b)["snapshot_hash"] == snap_b["snapshot_hash"]

        # Save reproducible diagnostics for the benchmark report, without claiming provider usage.
        artifacts = Path(__file__).resolve().parents[2] / "benchmarks" / "compact_initial_context" / "results"
        artifacts.mkdir(parents=True, exist_ok=True)
        result_file = artifacts / "rust-lineage-e2e-latest.json"
        generation_receipts = [e[3] for e in events if e[0] == "context_generation_complete"]
        result_file.write_text(json.dumps({
            "backend": "Rust MemCoreEngine + SQLite + real MiniLM ONNX",
            "database": str(database), "namespace": scope["namespace"],
            "session_a": sid_a, "lineage_b": sid_b,
            "snapshot_a": {k: snap_a[k] for k in ("snapshot_hash", "token_count", "token_budget", "selected_memory_ids",
                                                       "token_count_method", "selected_memory_count", "truncated",
                                                       "budget_respected", "generation_elapsed_ms")},
            "snapshot_b": {k: snap_b[k] for k in ("snapshot_hash", "token_count", "token_budget", "selected_memory_ids",
                                                       "token_count_method", "selected_memory_count", "truncated",
                                                       "budget_respected", "generation_elapsed_ms")},
            "snapshot_b_generation": next((r for r in generation_receipts if r.get("selected_memory_ids")), None),
            "extraction": extraction, "event_order": [{"event": e[0], "lineage": e[1]} for e in events],
            "event_timing": [{"event": e[0], "lineage": e[1],
                               "elapsed_ms": round((e[2] - test_started) * 1000, 2)} for e in events],
            "daemon_unavailable_extraction": outage,
            "local_measurements": {
                "system_prompt_tokens_estimate": {"lineage_A": prompt_a_tokens, "lineage_B": prompt_b_tokens,
                                                   "method": "fixture_characters_divided_by_four"},
                "rust_retrieval_ms": {"samples": retrieval_samples,
                                       "median": round(statistics.median(x["elapsed_ms"] for x in retrieval_samples), 2) if retrieval_samples else None,
                                       "p95": round(sorted(x["elapsed_ms"] for x in retrieval_samples)[max(0, int(len(retrieval_samples) * .95) - 1)], 2) if retrieval_samples else None},
                "rust_write_ms": [e[4] for e in events if e[0] == "rust_write_ack"],
                "initial_context_generation_ms": [r.get("generation_elapsed_ms") for r in generation_receipts],
                "extraction_ms": extraction["result"].get("elapsed_ms"),
                "dynamic_recall_ms": {"samples": dynamic_latencies, "median": round(statistics.median(dynamic_latencies), 2)},
                "session_resume_prompt_restore_ms": resume_elapsed_ms,
                "daemon_rss_bytes_after_restart": _process_rss(daemon.process.pid),
                "final_api_message_assemblies": {"lineage_A": len(wire_a), "lineage_B": len(wire_systems)},
                "outbound_model_calls": 0,
                "provider_reported_tokens": None,
            },
            "model_calls": 0, "provider_usage": "unavailable (deterministic fallback / no live model)",
        }, indent=2), encoding="utf-8")
    finally:
        daemon.stop()
        if db is not None:
            db.close()
