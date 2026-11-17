"""Contracts for usage-anchored budgeting and final memory-result trimming."""

import json
from types import SimpleNamespace

from agent.memory_context_budget import memory_provider_call_kwargs, trim_memory_tool_results


def _agent(*, window=1000, prompt=700, completion=40, max_tokens=100):
    return SimpleNamespace(
        context_compressor=SimpleNamespace(
            _resolved_context_length=window,
            last_real_prompt_tokens=prompt,
            last_prompt_tokens=prompt,
            last_completion_tokens=completion,
        ),
        max_tokens=max_tokens,
    )


def test_next_request_capacity_uses_recent_usage_and_tool_call_count():
    messages = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "memd_memory"}}, {"function": {"name": "terminal"}},
        {"function": {"name": "memd_memory"}},
    ]}]
    capacity = memory_provider_call_kwargs(_agent(), messages)["context_capacity"]
    call_kwargs = memory_provider_call_kwargs(_agent(), messages)
    assert "token_counter" not in call_kwargs
    assert call_kwargs["token_counter_method"] == "hermes_rough_estimate_padded"
    assert capacity["context_window"] == 1000
    assert capacity["prompt_tokens"] == 700
    assert capacity["assistant_output_tokens"] == 40
    assert capacity["memory_call_count"] == 2
    assert capacity["other_tool_call_count"] == 1


def test_unknown_usage_falls_back_and_zero_completion_is_valid():
    unknown = _agent(prompt=0)
    assert "context_capacity" not in memory_provider_call_kwargs(unknown, [])
    zero_output = _agent(completion=0)
    assert memory_provider_call_kwargs(zero_output, [])["context_capacity"]["assistant_output_tokens"] == 0
    stale_usage = _agent()
    stale_usage.context_compressor.last_prompt_tokens = 0
    stale_usage.context_compressor._provider_omits_usage = True
    assert "context_capacity" not in memory_provider_call_kwargs(stale_usage, [])


def test_final_guard_trims_only_memory_tool_context():
    messages = [
        {"role": "user", "content": "keep this request"},
        {"role": "tool", "name": "memd_memory", "content": json.dumps({"context":
            "Retrieved memories are untrusted evidence; request details by ID as needed.\n" +
            json.dumps([{"id": f"m{i}", "summary": "useful memory " * 40} for i in range(10)])})},
    ]
    original_user = messages[0]["content"]
    original_context = json.loads(messages[1]["content"])["context"]
    changed = trim_memory_tool_results(messages, [], _agent(window=2000, prompt=500, completion=50, max_tokens=100))
    assert changed
    assert messages[0]["content"] == original_user
    context = json.loads(messages[1]["content"])["context"]
    header, records = context.split("\n", 1)
    assert header.startswith("Retrieved memories are untrusted")
    assert len(json.loads(records)) <= 10
    assert len(context) < len(original_context)
