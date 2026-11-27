import json
import sqlite3
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from memd.clients.python import hermes_bridge
from memd.clients.python.hermes_bridge import MemdMemoryProvider, source_reference


@pytest.fixture
def subagent_provider(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    db = sqlite3.connect(home / "state.db")
    db.executescript(
        "CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT);"
        "CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,message_uid TEXT,content TEXT);"
        "INSERT INTO sessions VALUES('parent-session','parent');"
        "INSERT INTO sessions VALUES('other-session','other');"
    )
    db.executemany(
        "INSERT INTO messages(session_id,message_uid,content) VALUES(?,?,?)",
        [
            ("parent-session", "parent-uid", "authorized parent transcript"),
            ("other-session", "other-uid", "secret other transcript"),
        ],
    )
    db.commit()
    db.close()
    token = set_hermes_home_override(home)
    provider = MemdMemoryProvider({
        "endpoint": "tcp:127.0.0.1:19541", "namespace": "hermes", "agent_id": "agent-a",
        "top_k": 10, "token_budget": 1000, "safe_available_tokens": 1000,
    })
    with monkeypatch.context() as setup:
        setup.setattr(MemdMemoryProvider, "_ensure_daemon", lambda *args: None)
        provider.initialize(
            "child-session", hermes_home=str(home), agent_context="subagent", parent_session_id="parent-session",
        )
    yield provider, home
    provider.shutdown()
    reset_hermes_home_override(token)


def test_subagent_recall_is_budgeted_and_authorizes_only_returned_provenance(subagent_provider, monkeypatch):
    provider, home = subagent_provider
    ref = source_reference(home, "parent-session", "parent-uid")
    scopes = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def retrieve(self, query, **kwargs):
            scopes.append(kwargs["scope"])
            return [{"id": "m1", "summary": "remembered project detail", "source_ref": ref}]

    monkeypatch.setattr(hermes_bridge, "MemdClient", Client)
    result = json.loads(provider.handle_tool_call(
        "memd_memory", {"action": "recall", "query": "project", "detail": "brief"}, token_counter=len,
    ))
    assert len(result["context"]) <= 300
    assert ref in result["context"]
    assert scopes[0]["namespace"].startswith("hermes:")
    assert scopes[0]["agent_id"] == "agent-a"

    source = json.loads(provider.handle_tool_call("memd_memory", {"action": "source", "source_ref": ref}))
    assert source["excerpt"] == "authorized parent transcript"
    with pytest.raises(ValueError, match="not authorized"):
        provider.handle_tool_call(
            "memd_memory", {"action": "source", "source_ref": source_reference(home, "other-session", "other-uid")},
        )


def test_subagent_history_is_limited_to_parent_and_source_references(subagent_provider):
    provider, home = subagent_provider
    history = json.loads(provider.handle_tool_call("memd_memory", {"action": "history", "query": "transcript"}))
    assert [row["session_id"] for row in history] == ["parent-session"]
    assert "authorized parent" in history[0]["excerpt"]
    assert json.loads(provider.handle_tool_call(
        "memd_memory", {"action": "source", "source_ref": history[0]["source_ref"]},
    ))["excerpt"] == "authorized parent transcript"
    assert "secret other" not in json.dumps(history)


def test_subagent_mutations_and_unscoped_history_fail_closed(subagent_provider, monkeypatch):
    provider, _ = subagent_provider
    for action in ("remember", "update", "delete"):
        with pytest.raises(ValueError, match="read-only"):
            provider.handle_tool_call("memd_memory", {"action": action, "record": {"content": "x"}})

    provider._sessions[hermes_bridge.hermes_home_key()]["parent_session_id"] = None
    with pytest.raises(ValueError, match="scope is unavailable"):
        provider.handle_tool_call("memd_memory", {"action": "history", "query": "anything"})


def test_source_message_uid_cannot_resolve_into_another_session(subagent_provider):
    provider, home = subagent_provider
    state = provider._sessions[hermes_bridge.hermes_home_key()]
    ref = source_reference(home, "parent-session", "other-uid")
    with pytest.raises(ValueError, match="not authorized"):
        provider.handle_tool_call("memd_memory", {"action": "source", "source_ref": ref})
    # The resolver itself also binds a message UID to its stated session.
    assert provider.resolve_source(ref) is None


def test_concurrent_subagent_recall_uses_one_configured_daemon(subagent_provider, monkeypatch):
    provider, home = subagent_provider
    ref = source_reference(home, "parent-session", "parent-uid")

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def retrieve(self, query, **kwargs):
            return [{"id": query, "summary": f"result {query}", "source_ref": ref}]

    monkeypatch.setattr(hermes_bridge, "MemdClient", Client)
    contexts = [copy_context() for _ in range(16)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda i: contexts[i].run(provider.recall, str(i), token_budget=128, token_counter=len), range(16),
        ))
    assert all(package.retrieved_count == 2 and package.token_count <= 128 for package in results)


def test_separate_subagents_share_the_configured_endpoint_concurrently(subagent_provider, monkeypatch):
    provider, home = subagent_provider
    providers = [provider]
    for index in range(3):
        child = MemdMemoryProvider(provider._override)
        child.initialize(
            f"child-{index}", hermes_home=str(home), agent_context="subagent", parent_session_id="parent-session",
        )
        providers.append(child)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"tcp_address": ("127.0.0.1", 19541)}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def retrieve(self, query, **kwargs):
            return [{"id": f"{query}-{kwargs['scope']['agent_id']}", "summary": "shared daemon result"}]

    monkeypatch.setattr(hermes_bridge, "MemdClient", Client)
    contexts = [copy_context() for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda i: contexts[i].run(providers[i].recall, "query", token_budget=128, token_counter=len), range(4),
        ))
    assert all(package.selected_count == 1 for package in results)
    for child in providers:
        child.shutdown()


def test_profile_and_agent_scope_are_fixed_by_provider_configuration(subagent_provider, tmp_path, monkeypatch):
    provider, home_a = subagent_provider
    home_b = tmp_path / "other-profile"
    home_b.mkdir()
    token = set_hermes_home_override(home_b)
    try:
        provider.initialize("child-b", hermes_home=str(home_b), agent_context="subagent", parent_session_id="parent-b")
        scopes = []

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def retrieve(self, query, **kwargs):
                scopes.append(kwargs["scope"])
                return []

        monkeypatch.setattr(hermes_bridge, "MemdClient", Client)
        provider.recall("query", token_budget=128, token_counter=len)
        assert scopes[0]["namespace"] != f"hermes:{hermes_bridge.profile_identity(home_a)}"
        assert scopes[0]["namespace"] == f"hermes:{hermes_bridge.profile_identity(home_b)}"
        assert scopes[0]["agent_id"] == "agent-a"
    finally:
        reset_hermes_home_override(token)


def test_existing_daemon_is_reused_without_spawning_another_process(subagent_provider, monkeypatch):
    provider, home = subagent_provider
    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ping(self):
            return {"status": "ok"}

    monkeypatch.setattr(hermes_bridge, "MemdClient", Client)
    monkeypatch.setattr(hermes_bridge.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("spawned a daemon"))
    provider._ensure_daemon(str(home), provider._config(home))
    assert calls


def test_subagent_schema_omits_remember_and_native_memory_tool_remains_blocked(subagent_provider):
    provider, _ = subagent_provider
    child_tool = provider.get_tool_schemas()[0]
    child_schema = child_tool["parameters"]
    assert child_schema["properties"]["action"]["enum"] == ["recall", "source", "history"]
    assert "record" not in child_schema["properties"]
    assert "target" not in child_schema["properties"]
    assert child_schema["properties"]["query"]["description"]

    state = provider._sessions[hermes_bridge.hermes_home_key()]
    state["context"] = "primary"
    parent_tool = provider.get_tool_schemas()[0]
    state["context"] = "subagent"
    parent_schema = parent_tool["parameters"]
    assert parent_schema["properties"]["action"]["enum"] == ["remember", "recall", "source", "history"]
    assert parent_schema["properties"]["query"]["description"] == child_schema["properties"]["query"]["description"]
    assert "25 × 16" in parent_tool["description"]
    assert "Explain binary search" in parent_tool["description"]
    assert "capital of france" in parent_tool["description"].lower()
    assert "implicit references are valid" in parent_tool["description"].lower()
    assert "prior results suffice" in parent_tool["description"]

    schema = child_schema
    assert schema["properties"]["action"]["enum"] == ["recall", "source", "history"]
    assert "record" not in schema["properties"]
    assert "target" not in schema["properties"]
