from types import SimpleNamespace
import threading
from unittest.mock import MagicMock, patch

from tools.delegate_tool import _build_child_agent, _parent_has_exposed_memd


def _parent(*, exposed=True):
    provider = SimpleNamespace(name="hermes-memd")
    manager = MagicMock()
    manager.providers = [provider]
    manager.has_tool.return_value = exposed
    tools = [{"type": "function", "function": {"name": "memd_memory"}}] if exposed else []
    parent = MagicMock()
    parent.base_url = "https://openrouter.ai/api/v1"
    parent.api_key = "test-key"
    parent.provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "test-model"
    parent.platform = "cli"
    parent._session_db = None
    parent.session_id = "parent-session"
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent._memory_manager = manager
    parent.tools = tools
    parent.enabled_toolsets = ["terminal", "file", "web"]
    parent.disabled_toolsets = ["memory"]
    return parent


def _build(parent, enabled):
    with (
        patch("tools.delegate_tool._load_config", return_value={"memd": {"enabled": enabled}}),
        patch("run_agent.AIAgent") as agent_cls,
    ):
        agent_cls.return_value = MagicMock()
        _build_child_agent(
            task_index=0, goal="recall", context=None, toolsets=None, model=None,
            max_iterations=3, task_count=1, parent_agent=parent,
        )
    return agent_cls.call_args.kwargs


def test_parent_memd_capability_requires_provider_and_exposed_tool():
    assert _parent_has_exposed_memd(_parent())
    assert not _parent_has_exposed_memd(_parent(exposed=False))

    parent = _parent()
    parent._memory_manager.providers = [SimpleNamespace(name="another-provider")]
    assert not _parent_has_exposed_memd(parent)


def test_subagent_memd_is_opt_in_and_inherits_parent_capability():
    assert _build(_parent(), True)["allow_subagent_memd"] is True
    assert _build(_parent(), False)["allow_subagent_memd"] is False
    assert _build(_parent(exposed=False), True)["allow_subagent_memd"] is False


def test_nested_subagent_cannot_regain_a_parent_denied_memd_tool():
    parent = _parent(exposed=False)
    parent.platform = "subagent"
    assert _build(parent, True)["allow_subagent_memd"] is False


def test_native_memory_tool_remains_blocked_for_children():
    from tools.delegate_tool_toolsets import _resolve_child_toolsets

    parent = _parent()
    parent.enabled_toolsets = ["terminal", "file", "web", "memory", "delegation"]
    enabled, disabled = _resolve_child_toolsets(parent, None, "leaf")
    assert "memory" not in enabled
    assert "memory" in disabled


def test_subagent_initializes_only_memd_and_exposes_no_native_memory(monkeypatch):
    from agent.agent_init import _init_memory
    class Provider:
        name = "hermes-memd"

        def __init__(self):
            self.init_kwargs = None

        def get_tool_schemas(self):
            return [{"name": "memd_memory", "parameters": {"type": "object"}}]

        def is_available(self):
            return True

        def initialize(self, session_id, **kwargs):
            self.init_kwargs = kwargs

    provider = Provider()
    monkeypatch.setattr("tools.memory_tool.get_builtin_memory_config", lambda cfg: {"provider": "hermes-memd"})
    monkeypatch.setattr("plugins.memory.load_memory_provider", lambda name: provider)
    agent = SimpleNamespace(
        _allow_subagent_memd=True, platform="subagent", enabled_toolsets=[], disabled_toolsets=["memory"],
        tools=[], valid_tool_names=set(), session_id="child-session", _parent_session_id="parent-session",
        _session_db=None, session_cwd=None, _memory_store=None, _memory_manager=None,
        _user_id=None, _user_id_alt=None, _user_name=None, _chat_id=None, _chat_name=None,
        _chat_type=None, _thread_id=None, _gateway_session_key=None,
    )

    _init_memory(agent, {}, skip_memory=True, platform="subagent")

    assert agent._memory_enabled is False
    assert agent._memory_store is None
    assert [schema["function"]["name"] for schema in agent.tools] == ["memd_memory"]
    assert provider.init_kwargs["agent_context"] == "subagent"
    assert provider.init_kwargs["parent_session_id"] == "parent-session"
    assert agent.disabled_toolsets.count("memory") == 1

    from agent.memory_manager import inject_memory_provider_tools
    assert inject_memory_provider_tools(agent) == 0


def test_subagent_does_not_initialize_another_memory_provider(monkeypatch):
    from agent.agent_init import _init_memory

    monkeypatch.setattr("tools.memory_tool.get_builtin_memory_config", lambda cfg: {"provider": "other"})
    load_provider = MagicMock()
    monkeypatch.setattr("plugins.memory.load_memory_provider", load_provider)
    agent = SimpleNamespace(
        _allow_subagent_memd=True, platform="subagent", enabled_toolsets=[], disabled_toolsets=["memory"],
        tools=[], valid_tool_names=set(), session_id="child", _parent_session_id="parent", _session_db=None,
        session_cwd=None, _memory_store=None, _memory_manager=None,
        _user_id=None, _user_id_alt=None, _user_name=None, _chat_id=None, _chat_name=None,
        _chat_type=None, _thread_id=None, _gateway_session_key=None,
    )
    _init_memory(agent, {}, skip_memory=True, platform="subagent")
    load_provider.assert_not_called()
    assert agent._memory_manager is None

    agent._allow_subagent_memd = False
    monkeypatch.setattr("tools.memory_tool.get_builtin_memory_config", lambda cfg: {"provider": "hermes-memd"})
    _init_memory(agent, {}, skip_memory=True, platform="subagent")
    load_provider.assert_not_called()
