"""Provider-neutral functional task execution with durable Prime observation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

if __package__:
    from .prime_learning import LearningProvider
else:
    from prime_learning import LearningProvider


class TaskHarness:
    """Run a task callback with applicable guidance and record its objective outcome.

    The callback owns Hermes/AIAgent construction and returns a mapping containing
    ``final_response`` plus references to durable trajectory/tool records. Task output
    is not treated as success unless an objective evaluator supplies an outcome.
    """

    def __init__(self, learning_provider: LearningProvider, *, on_objective=None):
        self.learning_provider = learning_provider
        self.on_objective = on_objective

    @classmethod
    def from_plugin_context(cls, ctx) -> "TaskHarness":
        """Build the profile-local Prime harness and bind its host-managed reviewer route."""
        if __package__:
            from .prime_integration import _active_learning_config, _learner, _queue_automatic_review
        else:
            from prime_integration import _active_learning_config, _learner, _queue_automatic_review

        config = _active_learning_config()
        if config is None:
            raise ValueError("Prime learning must be enabled to build the Hermes task harness")
        learner = _learner()
        prime_config = dict(config)
        return cls(learner, on_objective=lambda experience: _queue_automatic_review(
            ctx, learner, prime_config, experience["experience_id"],
        ))

    @staticmethod
    def _safe_model_config(config: dict) -> dict:
        """Persist model routing facts without accepting arbitrary credential fields."""
        allowed = ("provider", "model", "api_mode", "reasoning_effort")
        return {key: config[key] for key in allowed if key in config}

    @staticmethod
    def _error_refs(errors) -> list[dict]:
        """Keep error categories/codes while excluding potentially secret messages."""
        if not isinstance(errors, (list, tuple)):
            return []
        result = []
        for error in errors:
            if isinstance(error, dict):
                item = {key: error[key] for key in ("type", "code") if isinstance(error.get(key), (str, int))}
                if item:
                    result.append(item)
            elif isinstance(error, str):
                result.append({"type": "reported_error"})
            else:
                result.append({"type": type(error).__name__})
        return result

    def run(self, task: dict, execute: Callable[[dict, list[dict]], dict],
            evaluate: Callable[[dict, dict], object] | None = None) -> dict:
        task_id = str(task.get("task_id") or uuid.uuid4().hex)
        scope = str(task.get("scope") or "global")
        guidance = self.learning_provider.list_active_artifacts(scope)
        execution_error = None
        try:
            result = execute(task, guidance)
            if not isinstance(result, dict):
                raise TypeError("task executor must return a mapping")
        except Exception as exc:
            execution_error = exc
            result = {"final_response": "", "errors": [{"type": type(exc).__name__}], "execution_status": "failed"}
        evaluation_error = None
        if execution_error is not None or result.get("execution_status") == "failed":
            outcome = None
            evaluation_status = "execution_failed"
        else:
            try:
                outcome = evaluate(task, result) if evaluate else None
                evaluation_status = "evaluated" if evaluate else "not_configured"
            except Exception as exc:
                evaluation_error = exc
                outcome = None
                evaluation_status = "failed"
        experience = {
            "experience_id": uuid.uuid5(uuid.NAMESPACE_URL, f"hermes-experience:{scope}:{task_id}").hex,
            "task_id": task_id,
            "scope": scope,
            "session_id": task.get("session_id"),
            "agent_id": task.get("agent_id", "primary"),
            "task_category": task.get("task_category"),
            "task_instructions": task.get("instructions", ""),
            "trajectory_refs": result.get("trajectory_refs", []),
            "tool_refs": result.get("tool_refs", []),
            "errors": self._error_refs(result.get("errors", [])),
            "execution_status": result.get("execution_status", "completed"),
            "final_response": result.get("final_response", ""),
            "objective_outcome": outcome,
            "evaluation_status": evaluation_status,
            "evaluation_error_type": type(evaluation_error).__name__ if evaluation_error else None,
            "evaluation_score": result.get("evaluation_score"),
            "memory_refs": task.get("memory_refs", []),
            "applied_artifacts": [
                {"scope": scope, "version": item.get("version"), "candidate_id": item.get("candidate_id")}
                for item in guidance
            ],
            "model_config": self._safe_model_config(task.get("model_config", {})),
            "started_at": result.get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        learning_error = None
        try:
            self.learning_provider.observe(experience)
            if outcome is not None and self.on_objective is not None:
                self.on_objective(experience)
        except Exception as exc:
            learning_error = exc
        return {"task_id": task_id, "result": result, "objective_outcome": outcome,
                "experience_id": experience["experience_id"], "applied_artifacts": guidance,
                "execution_status": "failed" if execution_error or result.get("execution_status") == "failed" else "completed",
                "execution_error_type": type(execution_error).__name__ if execution_error else None,
                "evaluation_status": evaluation_status,
                "learning_status": "failed" if learning_error else "recorded",
                "learning_error_type": type(learning_error).__name__ if learning_error else None,
                "evaluation_error_type": type(evaluation_error).__name__ if evaluation_error else None}

    def run_hermes(self, task: dict, *, agent_kwargs: dict | None = None,
                   evaluate: Callable[[dict, dict], object] | None = None) -> dict:
        """Execute a fresh real AIAgent task, then record its objective result.

        A new agent is constructed for each harness task so guidance is frozen at
        construction and never mutates a live conversation's cached prompt.
        ``agent_kwargs`` carries normal Hermes model, tool, and permission settings;
        credentials stay in memory and are excluded from the experience record.
        """
        instructions = task.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("Hermes task instructions must be a non-empty string")
        session_id = str(task.get("session_id") or uuid.uuid4().hex)
        base_kwargs = dict(agent_kwargs or {})
        task_record = dict(task)
        task_record["session_id"] = session_id
        if not task_record.get("model_config"):
            task_record["model_config"] = self._safe_model_config(base_kwargs)

        def execute(current_task: dict, guidance: list[dict]) -> dict:
            from run_agent import AIAgent

            guidance_text = "\n\n".join(
                str(item.get("body") or "").strip() for item in guidance if item.get("body")
            )
            kwargs = dict(base_kwargs)
            prompt = str(kwargs.pop("ephemeral_system_prompt", "") or "")
            if guidance_text:
                prompt = "\n\n".join(part for part in (prompt, guidance_text) if part)
            kwargs.update({
                "session_id": session_id,
                "ephemeral_system_prompt": prompt or None,
                "skip_background_review": True,
            })
            agent = AIAgent(**kwargs)
            try:
                harness_task_id = f"hermes-harness:{current_task.get('task_id') or session_id}"
                response = agent.run_conversation(instructions, conversation_history=[], task_id=harness_task_id)
                if not isinstance(response, dict):
                    raise TypeError("AIAgent.run_conversation must return a mapping")
                messages = response.get("messages") or []
                tool_refs = []
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    if message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
                        tool_refs.append(message["tool_call_id"])
                    for call in message.get("tool_calls") or []:
                        if isinstance(call, dict) and isinstance(call.get("id"), str):
                            tool_refs.append(call["id"])
                return {
                    "final_response": str(response.get("final_response") or ""),
                    "execution_status": "failed" if response.get("error") or response.get("completed") is False else "completed",
                    "errors": response.get("error") and [{"type": "agent_error"}] or [],
                    "trajectory_refs": [f"hermes://sessions/{session_id}/tasks/{current_task.get('task_id') or session_id}"],
                    "tool_refs": list(dict.fromkeys(tool_refs)),
                    "started_at": response.get("started_at"),
                }
            finally:
                close = getattr(agent, "close", None)
                if callable(close):
                    close()

        return self.run(task_record, execute, evaluate)
