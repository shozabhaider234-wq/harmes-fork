"""Regression: learning hooks receive only the current user turn's evidence refs."""

from agent.turn_finalizer import _current_turn_messages


def test_current_turn_evidence_excludes_prior_session_messages():
    old_user = {"role": "user", "content": "old request"}
    old_call = {"role": "assistant", "tool_calls": [{"id": "old-call"}]}
    current_user = {"role": "user", "content": "current request"}
    current_call = {"role": "assistant", "tool_calls": [{"id": "current-call"}]}
    current_result = {"role": "tool", "tool_call_id": "current-call"}
    current_final = {"role": "assistant", "content": "done"}

    assert _current_turn_messages([
        old_user, old_call, current_user, current_call, current_result, current_final,
    ]) == [current_user, current_call, current_result, current_final]
