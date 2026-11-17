"""Small adapter between the live turn accounting and external memory providers."""

from __future__ import annotations

from typing import Any


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _tool_call_names(message: dict) -> list[str]:
    names = []
    for call in message.get("tool_calls") or []:
        function = call.get("function", {}) if isinstance(call, dict) else getattr(call, "function", None)
        name = function.get("name") if isinstance(function, dict) else getattr(function, "name", None)
        if isinstance(name, str):
            names.append(name)
    return names


def memory_provider_call_kwargs(agent: Any, messages: list) -> dict:
    """Reuse the just-completed provider usage; never probe model metadata in a tool call."""
    kwargs = {
        "token_counter_method": "hermes_rough_estimate_padded",
    }
    compressor = getattr(agent, "context_compressor", None)
    window = _positive_int(getattr(compressor, "_resolved_context_length", None))
    prompt = _positive_int(getattr(compressor, "last_real_prompt_tokens", None))
    # last_real_prompt_tokens intentionally survives usage-less responses for
    # compression telemetry. Do not mistake that stale value for this call.
    current_prompt = _positive_int(getattr(compressor, "last_prompt_tokens", None))
    if getattr(compressor, "_provider_omits_usage", False):
        current_prompt = None
    completion = _nonnegative_int(getattr(compressor, "last_completion_tokens", None))
    if window is None or prompt is None or current_prompt is None or completion is None:
        return kwargs

    assistant = next((m for m in reversed(messages or [])
                      if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")), {})
    names = _tool_call_names(assistant)
    memory_calls = sum(name == "memd_memory" for name in names) or 1
    kwargs["context_capacity"] = {
        "context_window": window,
        "prompt_tokens": prompt,
        # Provider-reported completion usage includes the assistant's tool calls.
        "assistant_output_tokens": completion,
        "reserved_output_tokens": _positive_int(getattr(agent, "max_tokens", None)) or 4096,
        "memory_call_count": memory_calls,
        "other_tool_call_count": max(0, len(names) - memory_calls),
    }
    return kwargs


def trim_memory_tool_results(messages: list, tools: list, agent: Any) -> bool:
    """Trim only API-copy memd results if the complete next request approaches its window."""
    from agent.model_metadata import estimate_request_tokens_rough, estimate_tokens_rough
    import json

    compressor = getattr(agent, "context_compressor", None)
    window = _positive_int(getattr(compressor, "_resolved_context_length", None))
    if not window:
        return False
    output_reserve = _positive_int(getattr(agent, "max_tokens", None)) or 4096
    safety_margin = 1024
    input_limit = window - output_reserve - safety_margin
    if input_limit <= 0:
        max_memory_tokens = 0
    else:
        max_memory_tokens = input_limit

    def current_tokens() -> int:
        return estimate_request_tokens_rough(messages, tools=tools or None)

    changed = False
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "tool" or message.get("name") != "memd_memory":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            continue
        context = payload.get("context") if isinstance(payload, dict) else None
        if not isinstance(context, str):
            continue
        before = current_tokens()
        if before <= input_limit:
            break
        excess = before - input_limit
        context_tokens = estimate_tokens_rough(context)
        target_tokens = max(0, min(max_memory_tokens, context_tokens - excess - 64))
        header, separator, body = context.partition("\n")
        try:
            records = json.loads(body) if separator else None
        except (TypeError, ValueError):
            records = None
        best = ""
        if isinstance(records, list):
            def formatted(items):
                return header + "\n" + json.dumps(items, ensure_ascii=False, separators=(",", ":"))

            while records and estimate_tokens_rough(formatted(records)) > target_tokens:
                records.pop()
            if estimate_tokens_rough(formatted(records)) <= target_tokens:
                best = formatted(records)
                if records and isinstance(records[-1], dict) and isinstance(records[-1].get("summary"), str):
                    original = records[-1]["summary"]
                    low, high = 0, len(original)
                    while low <= high:
                        middle = (low + high) // 2
                        partial = dict(records[-1])
                        partial["summary"] = original[:middle].rstrip() + ("…" if middle < len(original) else "")
                        candidate_records = [*records[:-1], partial]
                        candidate = formatted(candidate_records)
                        if estimate_tokens_rough(candidate) <= target_tokens:
                            best = candidate
                            low = middle + 1
                        else:
                            high = middle - 1
        payload["context"] = best
        message["content"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        changed = changed or best != context
        max_memory_tokens = 0
    return changed
