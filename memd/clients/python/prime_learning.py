"""Persistent, gated procedural learning for the optional Hermes memd plugin.

This is a Prime-inspired workflow, not an implementation of Prime Agent. Experiences
remain separate from ordinary memory; only evaluated procedural guidance is activated.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Callable, Iterable, Protocol, runtime_checkable


_local_locks: dict[str, threading.RLock] = {}
_local_locks_guard = threading.Lock()


@runtime_checkable
class LearningProvider(Protocol):
    """Small execution-harness boundary; providers own their persistence."""

    def observe(self, experience: dict) -> str | None: ...
    def learn(self, scope: str, candidate_generator: Callable, fixtures: Iterable,
              evaluator: Callable, *, eligible_ids: Iterable[str] | None = None) -> dict | None: ...
    def list_active_artifacts(self, scope: str) -> list[dict]: ...
    def rollback(self, scope: str, version: int | None) -> int | None: ...


class NoLearningProvider:
    """Default provider: tasks execute normally and learning persistence is untouched."""

    def observe(self, experience: dict) -> None:
        return None

    def learn(self, scope: str, candidate_generator: Callable, fixtures: Iterable,
              evaluator: Callable, *, eligible_ids: Iterable[str] | None = None) -> None:
        return None

    def list_active_artifacts(self, scope: str) -> list[dict]:
        return []

    def rollback(self, artifact_id: str, version: int | None) -> None:
        return None


class PrimeLearner:
    """Persist experiences and activate only validated, non-regressing guidance."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        key = str(self.path.resolve())
        with _local_locks_guard:
            self._thread_lock = _local_locks.setdefault(key, threading.RLock())

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 1, "scopes": {}, "proposals": {}, "experiences": {},
                    "learning_runs": {}, "session_guidance": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("learning store must contain a JSON object")
        data.setdefault("schema_version", 1)
        data.setdefault("scopes", {})
        data.setdefault("proposals", {})
        data.setdefault("experiences", {})
        data.setdefault("learning_runs", {})
        data.setdefault("automatic_triggers", {})
        data.setdefault("session_guidance", {})
        return data

    def _write(self, data: dict) -> None:
        fd, temp_name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            for attempt in range(6):
                try:
                    os.replace(temp_name, self.path)
                    break
                except PermissionError:
                    # Windows scanners can briefly hold the destination after it closes.
                    if os.name != "nt" or attempt == 5:
                        raise
                    time.sleep(0.02 * (2 ** attempt))
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _experience_id(experience: dict) -> str:
        experience_id = str(experience.get("experience_id") or "").strip()
        if not experience_id:
            raise ValueError("experience_id is required")
        if not str(experience.get("task_id") or "").strip():
            raise ValueError("task_id is required")
        if "objective_outcome" not in experience:
            raise ValueError("objective_outcome is required; use null when unknown")
        return experience_id

    def observe(self, experience: dict) -> str:
        """Record an experience once. Unknown outcomes stay unknown, never inferred."""
        if not isinstance(experience, dict):
            raise TypeError("experience must be a mapping")
        experience_id = self._experience_id(experience)
        with self._locked():
            data = self._read()
            existing = data["experiences"].get(experience_id)
            if existing is not None:
                if existing != experience:
                    raise ValueError(f"experience {experience_id!r} already exists with different data")
                return experience_id
            data["experiences"][experience_id] = dict(experience)
            self._write(data)
        return experience_id

    def experience(self, experience_id: str) -> dict | None:
        """Return a detached experience record by its stable identifier."""
        with self._locked():
            item = self._read()["experiences"].get(experience_id)
            return dict(item) if item is not None else None

    def label(self, experience_id: str, outcome: bool | None) -> None:
        """Attach an explicit human outcome; unknown stays unknown until labeled."""
        if outcome is not None and not isinstance(outcome, bool):
            raise ValueError("outcome must be true, false, or null")
        with self._locked():
            data = self._read()
            item = data["experiences"].get(experience_id)
            if item is None:
                raise ValueError(f"unknown experience {experience_id!r}")
            item["objective_outcome"] = outcome
            item["evaluation_status"] = "human_labeled" if outcome is not None else "pending_human_label"
            self._write(data)

    def claim_automatic_trigger(self, trigger_id: str, experience_ids: Iterable[str] = ()) -> bool:
        """Persist a one-shot automatic review claim before starting background work."""
        if not trigger_id:
            raise ValueError("trigger_id is required")
        with self._locked():
            data = self._read()
            triggers = data.setdefault("automatic_triggers", {})
            if trigger_id in triggers:
                return False
            triggers[trigger_id] = {
                "status": "queued", "experience_refs": list(dict.fromkeys(experience_ids)),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write(data)
        return True

    def finish_automatic_trigger(self, trigger_id: str, status: str) -> None:
        if status not in {"completed", "no_candidate", "failed"}:
            raise ValueError("invalid automatic trigger status")
        with self._locked():
            data = self._read()
            trigger = data.setdefault("automatic_triggers", {}).get(trigger_id)
            if trigger is None:
                raise ValueError(f"unknown automatic trigger {trigger_id!r}")
            trigger["status"] = status
            self._write(data)

    def record_session_guidance(self, session_id: str, scope: str, artifact: dict | None) -> None:
        """Remember which frozen artifact was rendered for this session."""
        if not session_id:
            return
        with self._locked():
            data = self._read()
            sessions = data.setdefault("session_guidance", {})
            sessions[session_id] = {
                "scope": scope,
                "artifact": ({"version": artifact.get("version"),
                              "candidate_id": artifact.get("candidate_id")}
                             if artifact else None),
            }
            self._write(data)

    def session_guidance(self, session_id: str) -> dict | None:
        """Read the artifact identity captured when a session prompt was frozen."""
        with self._locked():
            item = self._read().get("session_guidance", {}).get(session_id)
            return dict(item) if item is not None else None

    def propose(self, scope: str, body: str, evidence_refs: Iterable[str], *,
                artifact_type: str = "procedural", candidate_id: str | None = None) -> str:
        if not scope.strip() or not body.strip():
            raise ValueError("scope and body must be non-empty")
        if artifact_type != "procedural":
            raise ValueError("only procedural artifacts are supported")
        if len(body) > 4000:
            raise ValueError("procedural guidance must be at most 4000 characters")
        unsafe = ("ignore all previous instructions", "ignore system instructions",
                  "reveal secrets", "disable safety", "bypass safety controls")
        if any(phrase in body.casefold() for phrase in unsafe):
            raise ValueError("procedural guidance cannot override instructions or weaken safety")
        evidence_refs = list(dict.fromkeys(str(ref) for ref in evidence_refs if str(ref).strip()))
        if not evidence_refs:
            raise ValueError("at least one evidence reference is required")
        proposal_id = uuid.uuid4().hex
        with self._locked():
            data = self._read()
            data["proposals"][proposal_id] = {
                "candidate_id": candidate_id or proposal_id,
                "scope": scope,
                "artifact_type": artifact_type,
                "body": body,
                "evidence_refs": evidence_refs,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "proposed",
            }
            self._write(data)
        return proposal_id

    def validate(self, proposal_id: str, fixtures: Iterable[object],
                 evaluator: Callable[[str, object], float]) -> dict:
        """Compare baseline and candidate, then commit only against the same baseline."""
        fixtures = list(fixtures)
        if not fixtures:
            raise ValueError("at least one validation fixture is required")
        with self._locked():
            data = self._read()
            proposal = data["proposals"][proposal_id]
            if proposal["status"] != "proposed":
                raise ValueError("proposal has already been evaluated")
            proposal["status"] = "validating"
            scope_state = data["scopes"].get(proposal["scope"], {})
            baseline_version = scope_state.get("active_version")
            versions = scope_state.get("versions", [])
            previous = next((v for v in versions if v["version"] == baseline_version), None)
            baseline_body = previous["body"] if previous else ""
            proposal_body = proposal["body"]
            scope = proposal["scope"]
            self._write(data)
        try:
            baseline = [float(evaluator(baseline_body, item)) for item in fixtures]
            candidate = [float(evaluator(proposal_body, item)) for item in fixtures]
            if not all(math.isfinite(score) for score in [*baseline, *candidate]):
                raise ValueError("evaluator scores must be finite numbers")
            accepted = all(new >= old for new, old in zip(candidate, baseline)) and mean(candidate) > mean(baseline)
            report = {
                "accepted": accepted,
                "baseline_scores": baseline,
                "candidate_scores": candidate,
                "baseline_mean": mean(baseline),
                "candidate_mean": mean(candidate),
                "policy": "no_fixture_regression_and_strict_mean_improvement",
            }
            evidence = getattr(evaluator, "evidence", None)
            if isinstance(evidence, list):
                report["evidence"] = list(evidence)
        except Exception as exc:
            with self._locked():
                data = self._read()
                proposal = data["proposals"][proposal_id]
                proposal["status"] = "failed"
                proposal["failure"] = f"{type(exc).__name__}: {exc}"
                self._write(data)
            raise

        with self._locked():
            data = self._read()
            proposal = data["proposals"][proposal_id]
            if proposal["status"] != "validating":
                raise ValueError("proposal changed while it was being evaluated")
            state = data["scopes"].get(scope, {"active_version": None, "versions": []})
            if state.get("active_version") != baseline_version:
                proposal["status"] = "failed"
                proposal["failure"] = "active artifact changed during validation; candidate must be reevaluated"
                self._write(data)
                raise RuntimeError(proposal["failure"])
            versions = state["versions"]
            proposal["validation"] = report
            proposal["status"] = "accepted" if accepted else "rejected"
            if accepted:
                version = versions[-1]["version"] + 1 if versions else 1
                versions.append({
                    "version": version,
                    "body": proposal["body"],
                    "artifact_type": proposal["artifact_type"],
                    "evidence_refs": proposal["evidence_refs"],
                    "candidate_id": proposal["candidate_id"],
                    "validation": report,
                })
                data["scopes"][scope] = {"active_version": version, "versions": versions}
                report["version"] = version
            self._write(data)
        return report

    def learn(self, scope: str, candidate_generator: Callable, fixtures: Iterable,
              evaluator: Callable, *, eligible_ids: Iterable[str] | None = None) -> dict | None:
        """Claim eligible experience once, generate one candidate, and evaluate it."""
        allowed_ids = set(eligible_ids) if eligible_ids is not None else None
        with self._locked():
            data = self._read()
            experiences = list(data["experiences"].values())
            already_used = {ref for item in data["proposals"].values() for ref in item["evidence_refs"]}
            already_reviewed = {
                ref for run in data["learning_runs"].values()
                if run["status"] != "failed" for ref in run["experience_refs"]
            }
            eligible = [item for item in experiences if item.get("objective_outcome") is not None
                        and (allowed_ids is None or item.get("experience_id") in allowed_ids)
                        and item.get("experience_id") not in already_used
                        and item.get("experience_id") not in already_reviewed]
            active_state = data["scopes"].get(scope, {})
            active = next((dict(item) for item in active_state.get("versions", [])
                           if item["version"] == active_state.get("active_version")), None)
            if not eligible:
                return None
            run_id = uuid.uuid4().hex
            data["learning_runs"][run_id] = {
                "scope": scope,
                "experience_refs": [item["experience_id"] for item in eligible],
                "status": "generating",
            }
            self._write(data)
        try:
            candidate = candidate_generator(eligible, active)
            if candidate is None:
                with self._locked():
                    data = self._read()
                    data["learning_runs"][run_id]["status"] = "no_candidate"
                    self._write(data)
                return None
            if not isinstance(candidate, dict) or not isinstance(candidate.get("body"), str):
                raise ValueError("candidate generator must return {body: str, evidence_refs: [...]} or None")
            eligible_refs = {item["experience_id"] for item in eligible}
            refs = candidate.get("evidence_refs") or sorted(eligible_refs)
            if not isinstance(refs, (list, tuple)) or not refs or not set(refs) <= eligible_refs:
                raise ValueError("candidate evidence_refs must reference eligible recorded experiences")
            proposal_id = self.propose(scope, candidate["body"], refs,
                                       artifact_type=candidate.get("artifact_type", "procedural"),
                                       candidate_id=candidate.get("candidate_id"))
            with self._locked():
                data = self._read()
                data["learning_runs"][run_id].update(status="evaluating", proposal_id=proposal_id)
                self._write(data)
            report = self.validate(proposal_id, fixtures, evaluator)
            with self._locked():
                data = self._read()
                data["learning_runs"][run_id]["status"] = "accepted" if report["accepted"] else "rejected"
                self._write(data)
            return {"proposal_id": proposal_id, **report}
        except Exception as exc:
            with self._locked():
                data = self._read()
                data["learning_runs"][run_id].update(status="failed", error_type=type(exc).__name__)
                self._write(data)
            raise

    def active(self, scope: str) -> dict | None:
        with self._locked():
            state = self._read()["scopes"].get(scope)
            if not state:
                return None
            version = next((item for item in state["versions"]
                            if item["version"] == state["active_version"]), None)
            return dict(version) if version else None

    def list_active_artifacts(self, scope: str) -> list[dict]:
        artifact = self.active(scope)
        return [artifact] if artifact else []

    def rollback(self, scope: str, version: int | None) -> int | None:
        with self._locked():
            data = self._read()
            state = data["scopes"].get(scope)
            if not state:
                raise ValueError(f"unknown scope {scope!r}")
            if version is None:
                state["active_version"] = None
            else:
                if not any(item["version"] == version for item in state["versions"]):
                    raise ValueError(f"unknown version {version} for scope {scope!r}")
                state["active_version"] = version
            self._write(data)
            return version

    def deactivate(self, scope: str) -> None:
        self.rollback(scope, None)


def create_learning_provider(name: str, path: str | Path | None = None) -> LearningProvider:
    """Resolve the configured mode without registering an unimplemented provider."""
    if name == "none":
        return NoLearningProvider()
    if name == "prime":
        if path is None:
            raise ValueError("prime learning requires an explicit profile-local store path")
        return PrimeLearner(path)
    raise ValueError(f"unknown learning provider {name!r}; available providers: none, prime")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        learner = PrimeLearner(Path(directory) / "learning.json")
        learner.observe({"experience_id": "exp-1", "task_id": "task-1", "objective_outcome": True})
        result = learner.learn("demo", lambda experiences, active: {
            "body": "inspect the lockfile before changing dependencies",
            "evidence_refs": [experiences[0]["experience_id"]],
        }, [1, 2], lambda body, item: item + (1 if body else 0))
        assert result["accepted"] and learner.active("demo")["version"] == 1
        rollback = learner.propose("demo", "unsafe change", ["exp-1"])
        report = learner.validate(rollback, [1, 2], lambda body, item: 0)
        assert not report["accepted"]
        learner.rollback("demo", None)
        assert learner.active("demo") is None
