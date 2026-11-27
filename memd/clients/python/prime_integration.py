"""Hermes lifecycle integration for profile-local Prime-inspired learning."""

from __future__ import annotations

import hashlib
import json
import logging
import hashlib
import uuid

from hermes_constants import get_hermes_home

if __package__:
    from .prime_learning import PrimeLearner
else:
    from prime_learning import PrimeLearner

logger = logging.getLogger(__name__)
_VALIDATION_MARKER = "[Prime validation run "
_HARNESS_TASK_PREFIX = "hermes-harness:"


def _config() -> dict:
    try:
        from hermes_cli.config import load_config_readonly
        value = (load_config_readonly() or {}).get("learning", {})
        return value if isinstance(value, dict) else {}
    except Exception:
        logger.warning("Could not read learning configuration", exc_info=True)
        return {}


def _learner() -> PrimeLearner:
    return PrimeLearner(get_hermes_home() / "learning" / "prime.json")


def _active_learning_config() -> dict | None:
    config = _config()
    if config.get("provider", "none") != "prime":
        return None
    prime = config.get("prime", {})
    return prime if isinstance(prime, dict) else {}


def _scope(config: dict) -> str:
    scope = config.get("scope", "global")
    return scope.strip() if isinstance(scope, str) and scope.strip() else "global"


def render_guidance(session_info) -> str:
    """Load guidance once for a new session and pin its artifact version in the store."""
    config = _active_learning_config()
    if config is None or config.get("enabled", True) is not True:
        return ""
    scope = _scope(config)
    try:
        artifact = _learner().active(scope)
        session_id = str(session_info.get("session_id") or "")
        _learner().record_session_guidance(session_id, scope, artifact)
        if not artifact:
            return ""
        return ("## Evaluated procedural guidance\n"
                "Treat the following as workflow suggestions; user and system instructions take precedence.\n"
                f"{artifact['body']}")
    except Exception:
        logger.warning("Could not load Prime guidance for a new session", exc_info=True)
        return ""


def capture_turn(**event) -> None:
    """Persist a compact trajectory record after a durable foreground turn."""
    config = _active_learning_config()
    if config is None or config.get("enabled", True) is not True:
        return
    if (str(event.get("task_instructions") or "").startswith(_VALIDATION_MARKER)
            or str(event.get("task_id") or "").startswith(_HARNESS_TASK_PREFIX)):
        return
    session_id = str(event.get("session_id") or "")
    turn_id = str(event.get("turn_id") or "")
    task_id = str(event.get("task_id") or turn_id or session_id)
    if not session_id or not task_id:
        return
    experience_id = hashlib.sha256(f"{session_id}\0{turn_id or task_id}".encode()).hexdigest()[:32]
    guidance = _learner().session_guidance(session_id) or {}
    scope = guidance.get("scope") or _scope(config)
    refs = []
    for message in event.get("turn_messages", ()):
        if not isinstance(message, dict):
            continue
        for key in ("message_uid", "tool_call_uid"):
            value = message.get(key)
            if isinstance(value, str) and value:
                refs.append(value)
        for call in message.get("tool_calls", ()):
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                refs.append(call["id"])
    status = ("interrupted" if event.get("interrupted") else
              "failed" if event.get("failed") else "completed")
    experience = {
        "experience_id": experience_id,
        "task_id": task_id,
        "scope": scope,
        "session_id": session_id,
        "agent_id": "primary",
        "task_instructions": str(event.get("task_instructions") or ""),
        "trajectory_refs": [f"hermes://sessions/{session_id}/turns/{turn_id}"] if turn_id else [],
        "tool_refs": list(dict.fromkeys(refs)),
        "execution_status": status,
        "turn_exit_reason": str(event.get("turn_exit_reason") or ""),
        "final_response": str(event.get("final_response") or ""),
        "objective_outcome": None,
        "evaluation_status": "pending_human_label",
        "applied_artifacts": ([{"scope": guidance.get("scope"), **guidance["artifact"]}]
                               if guidance.get("artifact") else []),
        "model_config": {key: event[key] for key in ("model", "provider") if event.get(key)},
        "api_call_count": int(event.get("api_call_count") or 0),
    }
    try:
        _learner().observe(experience)
    except Exception:
        # Observation is auxiliary; the already-completed user turn remains successful.
        logger.warning("Prime experience capture failed", exc_info=True)


def register(ctx) -> None:
    """Register the memory provider and learning runtime on Hermes's existing plugin surface."""
    if __package__:
        from .hermes_bridge import MemdMemoryProvider
    else:
        from hermes_bridge import MemdMemoryProvider
    ctx.register_memory_provider(MemdMemoryProvider())
    if getattr(ctx, "is_active_provider", True) is not True:
        return
    ctx.register_system_prompt_section(
        "hermes-memd-learning", render_guidance, position="after_memory", max_chars=4000,
    )
    ctx.register_hook("on_session_end", capture_turn)
    ctx.register_auxiliary_task(
        "hermes_memd_learning", display_name="Hermes Prime learning",
        description="Generate and evaluate procedural guidance from explicitly labeled task experiences.",
        defaults={"provider": "auto", "model": "", "timeout": 90},
    )
    ctx.register_command(
        "memd-learn", lambda raw: _learn_command(ctx, raw),
        description="Review, label, and roll back Prime procedural learning.",
        args_hint="<status|list|label|review|decisions|guidance|rollback|deactivate>",
    )


_CANDIDATE_SCHEMA = {
    "type": "object", "properties": {
        "should_refine": {"type": "boolean"},
        "body": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    }, "required": ["should_refine", "body", "evidence_refs"], "additionalProperties": False,
}
def _llm_json(ctx, instructions: str, payload: dict, schema: dict) -> dict:
    result = ctx.llm.complete_structured(
        instructions=instructions, input=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        json_schema=schema, temperature=0, task="hermes_memd_learning", purpose="prime_learning",
    )
    if getattr(result, "content_type", None) != "json" or not isinstance(result.parsed, dict):
        raise ValueError("learning model did not return the requested structured response")
    return result.parsed


def _review(ctx) -> dict | None:
    config = _active_learning_config()
    if config is None or config.get("enabled", True) is not True:
        raise ValueError("Prime learning is not enabled; set learning.provider: prime")
    scope = _scope(config)
    minimum = config.get("minimum_experiences", 3)
    learner = _learner()

    def generate(experiences, active):
        response = _llm_json(ctx,
            "Review labeled Hermes task experiences. Suggest one concise procedural artifact only "
            "when repeated evidence supports a reusable workflow. Never request secrets, override "
            "system/user instructions, weaken safety, or prescribe destructive actions. Use only "
            "provided experience IDs as evidence.",
            {"experiences": experiences, "active_guidance": active}, _CANDIDATE_SCHEMA)
        if not response["should_refine"]:
            return None
        return {"body": response["body"], "evidence_refs": response["evidence_refs"],
                "artifact_type": "procedural"}

    eligible = [item for item in learner._read()["experiences"].values()
                if item.get("evaluation_status") in {"human_labeled", "evaluated"}
                and item.get("objective_outcome") is not None
                and item.get("scope", "global") == scope]
    if len(eligible) < minimum:
        raise ValueError(f"Prime review needs at least {minimum} explicitly labeled experiences; found {len(eligible)}")

    validation_tasks = config.get("validation_tasks", [])
    if not isinstance(validation_tasks, list) or not validation_tasks:
        raise ValueError("Prime review requires fixed learning.prime.validation_tasks with objective checks")

    def evaluate(guidance, fixture):
        from agent.subagent_lifecycle import SubagentLaunchRequest, SubagentState

        instructions = fixture.get("instructions")
        checks = fixture.get("checks")
        if not isinstance(instructions, str) or not instructions.strip() or not isinstance(checks, list) or not checks:
            raise ValueError("each Prime validation task needs instructions and at least one objective check")
        context = None
        if guidance:
            context = ("Apply this candidate procedural guidance only when relevant. It cannot override "
                       "the task or higher-priority instructions:\n" + guidance)
        lifecycle = ctx.subagent_lifecycle
        handle = lifecycle.launch(SubagentLaunchRequest(
            goal=f"{_VALIDATION_MARKER}{uuid.uuid4().hex}] Learning is disabled for this isolated validation run.\n{instructions}",
            context=context, role="leaf",
            correlation_id="prime-validation-" + uuid.uuid4().hex,
        ))
        terminal = lifecycle.wait(handle, timeout_seconds=300)
        result = lifecycle.result(handle)
        response = result.summary or ""
        check_results = []
        for check in checks:
            if not isinstance(check, dict) or check.get("type") not in {"contains", "not_contains", "equals"}:
                raise ValueError("Prime validation checks support contains, not_contains, and equals")
            expected = check.get("value")
            if not isinstance(expected, str) or not expected:
                raise ValueError("Prime validation check value must be a non-empty string")
            if check["type"] == "contains":
                passed = expected.casefold() in response.casefold()
            elif check["type"] == "not_contains":
                passed = expected.casefold() not in response.casefold()
            else:
                passed = expected.strip().casefold() == response.strip().casefold()
            check_results.append({"type": check["type"], "passed": passed})
        succeeded = terminal.completed and terminal.state is SubagentState.SUCCEEDED
        evaluate.evidence.append({
            "variant": "candidate" if guidance else "baseline",
            "task_id": fixture.get("task_id", "validation-task"),
            "provider": handle.provider,
            "model": handle.model,
            "execution_state": terminal.state.value,
            "objective_checks": check_results,
            "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
        })
        return int(succeeded and all(item["passed"] for item in check_results))

    evaluate.evidence = []
    return learner.learn(scope, generate, validation_tasks, evaluate,
                         eligible_ids=[item["experience_id"] for item in eligible])


def _queue_automatic_review(ctx, learner: PrimeLearner, config: dict, experience_id: str) -> bool:
    """Queue a one-shot review only after an explicit, non-unknown outcome label."""
    trigger = config.get("trigger", "manual")
    if trigger not in {"after_objective", "after_n_experiences"}:
        return False
    state = learner._read()
    eligible = sorted(
        item["experience_id"] for item in state["experiences"].values()
        if item.get("scope", "global") == _scope(config)
        and item.get("objective_outcome") is not None
        and item.get("evaluation_status") in {"human_labeled", "evaluated"}
    )
    if trigger == "after_objective":
        if experience_id not in eligible:
            return False
        trigger_material = [experience_id]
    else:
        threshold = config.get("experience_threshold", 5)
        claimed = {ref for claim in state.get("automatic_triggers", {}).values()
                   for ref in claim.get("experience_refs", [])}
        pending = [item for item in eligible if item not in claimed]
        if len(pending) < threshold:
            return False
        trigger_material = pending[:threshold]
    trigger_id = hashlib.sha256(
        (trigger + "\0" + _scope(config) + "\0" + "\0".join(trigger_material)).encode()
    ).hexdigest()
    if not learner.claim_automatic_trigger(trigger_id, trigger_material):
        return False

    def run_review() -> None:
        try:
            report = _review(ctx)
            learner.finish_automatic_trigger(trigger_id, "completed" if report else "no_candidate")
        except Exception:
            learner.finish_automatic_trigger(trigger_id, "failed")
            logger.warning("Automatic Prime review failed", exc_info=True)

    from agent.memory_provider import spawn_context_thread
    spawn_context_thread(run_review, name="hermes-prime-auto-review").start()
    return True


def _learn_command(ctx, raw_args: str) -> str:
    """Minimal interactive control surface; every state-changing action is explicit."""
    config = _active_learning_config()
    if config is None:
        return "Prime learning is off. Set learning.provider: prime to enable it."
    if config.get("enabled", True) is not True:
        return "Prime learning is disabled by learning.prime.enabled."
    scope = _scope(config)
    learner = _learner()
    parts = (raw_args or "").split()
    action = parts[0].lower() if parts else "status"
    try:
        if action == "status":
            artifact = learner.active(scope)
            state = learner._read()
            count = sum(1 for item in state["experiences"].values()
                        if item.get("scope", "global") == scope and item.get("objective_outcome") is not None)
            return (f"Prime learning is enabled for scope {scope!r}; {count} experiences have explicit outcomes; "
                    f"active guidance version: {artifact['version'] if artifact else 'none'}.")
        if action == "guidance":
            artifact = learner.active(scope)
            if not artifact:
                return f"No active Prime guidance for scope {scope!r}."
            return f"Active Prime guidance version {artifact['version']} for {scope!r}:\n{artifact['body']}"
        if action == "decisions":
            state = learner._read()
            proposals = [item for item in state["proposals"].values() if item.get("scope") == scope][-20:]
            runs = [item for item in state["learning_runs"].values() if item.get("scope") == scope][-20:]
            if not proposals and not runs:
                return f"No Prime candidate decisions for scope {scope!r}."
            lines = [f"{item['candidate_id']} [{item['status']}]" for item in proposals]
            lines.extend(f"run {item_id} [{item.get('status', 'unknown')}]"
                         for item_id, item in list(state["learning_runs"].items())
                         if item.get("scope") == scope and item in runs)
            return "Recent Prime candidate decisions (up to 20):\n" + "\n".join(lines[-20:])
        if action == "list":
            experiences = [item for item in learner._read()["experiences"].values()
                           if item.get("scope", "global") == scope][-20:]
            if not experiences:
                return "No Prime experiences have been captured yet."
            lines = []
            for item in experiences:
                outcome = item.get("objective_outcome")
                label = "unknown" if outcome is None else ("success" if outcome is True else "failure")
                instruction = " ".join(str(item.get("task_instructions") or "").split())[:120]
                lines.append(f"{item['experience_id']} [{label}] {instruction}")
            return "Recent experiences (up to 20):\n" + "\n".join(lines)
        if action == "label" and len(parts) == 3:
            outcome = {"success": True, "failure": False, "unknown": None}.get(parts[2].lower(), "invalid")
            if outcome == "invalid":
                return "Use /memd-learn label <experience-id> success|failure|unknown."
            existing = learner.experience(parts[1])
            if existing is not None and existing.get("scope", "global") != scope:
                return f"Experience {parts[1]} is outside the active learning scope."
            learner.label(parts[1], outcome)
            if outcome is not None and _queue_automatic_review(
                ctx, learner, config.get("prime", {}), parts[1]
            ):
                return f"Recorded {parts[2]} outcome for {parts[1]}; automatic Prime review queued."
            return f"Recorded {parts[2]} outcome for {parts[1]}."
        if action == "review" and len(parts) == 1:
            report = _review(ctx)
            if report is None:
                return "No refinement was generated from the labeled experiences."
            return (f"Candidate {report['proposal_id']} was "
                    f"{'accepted as version ' + str(report['version']) if report['accepted'] else 'rejected'}.")
        if action == "rollback" and len(parts) == 2:
            version = None if parts[1].lower() in {"off", "none"} else int(parts[1])
            learner.rollback(scope, version)
            return f"Prime guidance is now {'inactive' if version is None else 'version ' + str(version)}."
        if action == "deactivate" and len(parts) == 1:
            learner.deactivate(scope)
            return f"Prime guidance is now inactive for scope {scope!r}."
    except Exception as exc:
        logger.warning("Prime learning command failed (%s)", type(exc).__name__, exc_info=True)
        return f"Prime learning action failed: {type(exc).__name__}: {exc}"
    return ("Use /memd-learn status|list|guidance|decisions, /memd-learn label <experience-id> "
            "success|failure|unknown, /memd-learn review, /memd-learn rollback <version|off>, "
            "or /memd-learn deactivate.")

