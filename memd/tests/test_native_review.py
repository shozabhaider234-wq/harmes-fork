"""Native review -> native memory tool -> real Rust daemon; no paid model calls."""
import copy
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_persistent_client as support


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    server = support.PersistentClientTests()
    server.setUp()
    home = tmp_path / "profile"
    plugin = home / "plugins" / "hermes-memd"
    shutil.copytree(Path(__file__).resolve().parents[1] / "clients" / "python", plugin,
                    ignore=shutil.ignore_patterns("__pycache__"))
    config = {"memory":{"provider":"hermes-memd", "nudge_interval":2},
              "auxiliary":{"background_review":{"enabled":True}},
              "skills":{"creation_nudge_interval":0}}
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
    (home / "memd.json").write_text(json.dumps({"endpoint":f"tcp:127.0.0.1:{server.address[1]}"}), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(home)
    try:
        yield home, server, config
    finally:
        reset_hermes_home_override(token)
        server.tearDown()


def test_native_store_lifecycle_and_shared_explicit_path(runtime, monkeypatch):
    home, server, config = runtime
    from tools.memory_tool import load_on_disk_store, memory_tool
    from plugins.memory import load_memory_provider
    store = load_on_disk_store()
    frozen = store.format_for_system_prompt("user")
    with store.write_context({"session_id":"conversation", "write_origin":"background_review"}):
        assert json.loads(memory_tool(action="add", target="user", content="User prefers Rust", store=store))["success"]
    assert store.format_for_system_prompt("user") == frozen
    with server.client() as client:
        user_record = client.inventory(store._scope("user"))[0]
        assert user_record["metadata"]["write_origin"] == "background_review"
        assert user_record["source_ref"].endswith("/sessions/conversation")
    provider = load_memory_provider("hermes-memd")
    provider.initialize("conversation", hermes_home=home)
    assert provider.ingest_memory({"content":"User prefers Rust"}, target="user") == user_record["id"]
    general_id = provider.ingest_memory({"content":"Cargo uses workspaces", "tags":["cargo"]}, message_uid="uid-1")
    assert store.add("memory", "Cargo uses workspaces")["success"]
    with server.client() as client:
        general = client.inventory(store._scope("memory"))
        assert len(general) == 1 and general[0]["id"] == general_id
        assert general[0]["source_ref"].endswith("/messages/uid-1")
        with pytest.raises(support.MemoryServiceError) as malformed:
            client.request("compare_exchange", scope=store._scope("memory"), expected="bad", desired=[])
        assert malformed.value.code == "invalid_request"
        with pytest.raises(support.MemoryServiceError):
            client.inventory({"namespace":""})
    assert user_record["id"] in provider.recall("User prefers Rust", token_budget=1000).selected_memory_ids
    result = store.replace("user", "prefers Rust", "User prefers concise Rust")
    assert result["success"] and result["replaced_entry"] == "User prefers Rust"
    with server.client() as client:
        edited = client.get_memory(user_record["id"])
        assert edited["created_at"] == user_record["created_at"]
        assert edited["content"] == "User prefers concise Rust"
    assert not store.apply_batch("user", [{"action":"add","content":"must roll back"}, {"action":"remove","old_text":"absent"}])["success"]
    server.stop()
    server.start()
    # Keep the profile endpoint in step with the restarted ephemeral listener.
    (home / "memd.json").write_text(json.dumps({"endpoint":f"tcp:127.0.0.1:{server.address[1]}"}), encoding="utf-8")
    store = load_on_disk_store()
    assert store.user_entries == ["User prefers concise Rust"]
    assert "Cargo uses workspaces" in store.memory_entries
    assert store.remove("user", "concise Rust")["success"]
    with server.client() as client:
        assert client.get_memory(user_record["id"]) is None
        assert client.get_memory(general_id) is not None
        snapshot = client.inventory(store._scope("memory"))
        client.store({"content":"Concurrent change", **store._scope("memory")})
        assert not client.compare_exchange(store._scope("memory"), snapshot, [])
        assert len(client.inventory(store._scope("memory"))) == 2
    server.stop()
    assert not store.add("memory", "failed daemon write")["success"]
    assert not (home / "memories" / "MEMORY.md").exists()
    assert not (home / "memories" / "USER.md").exists()


def response(text=None, operation=None):
    calls = None if operation is None else [SimpleNamespace(
        id="review-memory", type="function", function=SimpleNamespace(name="memory", arguments=json.dumps(operation)))]
    return SimpleNamespace(model="test/model", usage=None, choices=[SimpleNamespace(
        finish_reason="tool_calls" if calls else "stop", message=SimpleNamespace(
            content=text, reasoning_content=None, reasoning=None, tool_calls=calls))])


def test_explicit_migration_rollback_and_native_consumers(runtime, tmp_path, monkeypatch):
    home, server, config = runtime
    from tools.memory_tool import MemoryStore, load_on_disk_store
    from memd.clients.python.native_migration import import_markdown, export_markdown
    from agent.learning_graph import _memory_cards, memory_node_id
    from agent.learning_mutations import _mutate_memory
    from hermes_cli.main_agent_cmds import _cmd_memory_reset
    store = load_on_disk_store()
    originals = {"MEMORY.md":b"Cargo uses workspaces", "USER.md":b"User likes concise answers"}
    for filename, content in originals.items():
        (home / "memories" / filename).write_bytes(content)
    import_markdown(store, home / "memories")
    import_markdown(store, home / "memories")
    assert len(load_on_disk_store().memory_entries) == 1
    exported = tmp_path / "rollback-export"
    export_markdown(store, exported)
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: exported)
    rollback = MemoryStore()
    rollback.load_from_disk()
    assert rollback.memory_entries == ["Cargo uses workspaces"]
    assert rollback.user_entries == ["User likes concise answers"]
    cards = _memory_cards()
    assert len(cards) == 2
    node = memory_node_id(cards[0], 0)
    assert _mutate_memory(node, "Cargo workspace conventions")["ok"]
    assert load_on_disk_store().memory_entries == ["Cargo workspace conventions"]
    _cmd_memory_reset(SimpleNamespace(target="user",yes=True))
    assert not load_on_disk_store().user_entries
    for filename, content in originals.items():
        assert (home / "memories" / filename).read_bytes() == content


@pytest.mark.parametrize("save", [True, False])
def test_real_native_review_pipeline(runtime, monkeypatch, save):
    home, server, config = runtime
    from run_agent import AIAgent
    import run_agent
    from agent.background_review import _digest_history
    from tools.memory_tool import load_on_disk_store
    monkeypatch.setattr("agent.title_generator.maybe_auto_title", lambda *a, **k: None)
    monkeypatch.setattr("agent.model_metadata.get_model_context_length", lambda *a, **k: 256000)
    monkeypatch.setattr("agent.context_compressor.get_model_context_length", lambda *a, **k: 256000)
    model = MagicMock()
    monkeypatch.setattr("agent.process_bootstrap.OpenAI", lambda **k: model)
    captured = []
    queued = []
    real_thread = run_agent.threading.Thread
    class ReviewThread:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            queued.append(self.target)
    def thread_factory(*a, **k):
        return ReviewThread(**k) if k.get("name") == "bg-review" else real_thread(*a, **k)
    monkeypatch.setattr(run_agent.threading, "Thread", thread_factory)
    def complete(**kwargs):
        captured.append(copy.deepcopy(kwargs["messages"]))
        review = "Review the conversation above" in str(kwargs["messages"][-1].get("content", ""))
        if review and save:
            return response(operation={"action":"add", "target":"user", "content":"User prefers concise Rust explanations"})
        return response("Nothing to save." if review else "done")
    model.chat.completions.create.side_effect = complete
    # Configure real agents identically; only provider I/O is fake.
    original_init = AIAgent.__init__
    def initialize(self, *a, **k):
        original_init(self, *a, **k)
        self._disable_streaming = True
        self.save_trajectories = False
        self.compression_enabled = False
        self.tool_delay = 0
    monkeypatch.setattr(AIAgent, "__init__", initialize)
    parent = AIAgent(model="test/model", provider="openai", api_key="fake", base_url="http://127.0.0.1:1/v1",
                     quiet_mode=True, skip_context_files=True, enabled_toolsets=["memory"], max_iterations=4)
    history = []
    for index in range(30):
        history.extend([{"role":"user","content":f"earlier {index}"}, {"role":"assistant","content":"acknowledged"}])
    history.extend([{"role":"assistant", "content":None, "tool_calls":[{"id":"old-tool", "type":"function", "function":{"name":"memory", "arguments":"{}"}}]},
                    {"role":"tool", "tool_call_id":"old-tool", "name":"memory", "content":"old tool result"},
                    {"role":"assistant", "content":"continued"}])
    first = parent.run_conversation("I prefer concise Rust explanations", conversation_history=history)
    assert not queued and parent._turns_since_memory == 1
    second = parent.run_conversation("Please continue", conversation_history=first["messages"])
    assert parent._turns_since_memory == 0 and len(queued) == 1
    prompt = parent._cached_system_prompt
    queued.pop()()
    assert parent._cached_system_prompt == prompt
    review_requests = [messages for messages in captured if "Review the conversation above" in str(messages)]
    assert len(review_requests) == (2 if save else 1)
    first_review = review_requests[0]
    assert any(m.get("content") == "earlier 0" for m in first_review)
    assert any(m.get("tool_call_id") == "old-tool" for m in first_review)
    assert len(first_review) > 24
    assert len(_digest_history(history)) < len(history)
    assert _digest_history(history)[-24:] == history[-24:]
    store = load_on_disk_store()
    assert store.user_entries == (["User prefers concise Rust explanations"] if save else [])
    assert parent._turns_since_memory == 0
    assert not list((home / "memories").glob("*.md"))
    # The explicit provider surface does not reset the native review counter;
    # admitted native memory calls reset it even when their mutation fails.
    parent._turns_since_memory = 1
    call = response(operation={}).choices[0].message
    call.tool_calls[0].function.name = "memd_memory"
    call.tool_calls[0].function.arguments = json.dumps({"action":"remember", "record":{"content":"Cargo workspace convention"}})
    results = []
    parent._execute_tool_calls_sequential(call, results, "explicit-memory")
    assert json.loads(results[-1]["content"])["id"]
    assert parent._turns_since_memory == 1
    failed = response(operation={"action":"remove", "target":"user", "old_text":"absent entry"}).choices[0].message
    parent._execute_tool_calls_sequential(failed, results, "native-memory")
    assert not json.loads(results[-1]["content"])["success"]
    assert parent._turns_since_memory == 0
    parent.release_clients()
