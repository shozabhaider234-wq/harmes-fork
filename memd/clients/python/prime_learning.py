"""Small deterministic, versioned learning-artifact store."""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from pathlib import Path
from statistics import mean
from typing import Callable, Iterable


class PrimeLearner:
    """Persist proposals and activate only measured, non-regressing revisions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"scopes": {}, "proposals": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace prevents a crash from leaving a partially written history.
        fd, temp_name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def propose(self, scope: str, body: str, evidence_refs: Iterable[str]) -> str:
        if not scope.strip() or not body.strip():
            raise ValueError("scope and body must be non-empty")
        evidence_refs = list(dict.fromkeys(ref for ref in evidence_refs if ref.strip()))
        if not evidence_refs:
            raise ValueError("at least one evidence reference is required")
        data = self._read()
        proposal_id = uuid.uuid4().hex
        data["proposals"][proposal_id] = {
            "scope": scope,
            "body": body,
            "evidence_refs": list(evidence_refs),
            "status": "proposed",
        }
        self._write(data)
        return proposal_id

    def validate(
        self,
        proposal_id: str,
        fixtures: Iterable[object],
        evaluator: Callable[[str, object], float],
    ) -> dict:
        data = self._read()
        proposal = data["proposals"][proposal_id]
        if proposal["status"] != "proposed":
            raise ValueError("proposal has already been evaluated")
        fixtures = list(fixtures)
        if not fixtures:
            raise ValueError("at least one validation fixture is required")
        active = data["scopes"].get(proposal["scope"], {})
        versions = active.get("versions", [])
        previous = next(
            (v for v in versions if v["version"] == active.get("active_version")), None
        )
        baseline_body = previous["body"] if previous else ""
        baseline = [float(evaluator(baseline_body, item)) for item in fixtures]
        candidate = [float(evaluator(proposal["body"], item)) for item in fixtures]
        if not all(math.isfinite(score) for score in [*baseline, *candidate]):
            raise ValueError("evaluator scores must be finite numbers")
        accepted = (
            all(new >= old for new, old in zip(candidate, baseline))
            and mean(candidate) > mean(baseline)
        )
        report = {
            "accepted": accepted,
            "baseline_scores": baseline,
            "candidate_scores": candidate,
            "baseline_mean": mean(baseline),
            "candidate_mean": mean(candidate),
        }
        proposal["validation"] = report
        proposal["status"] = "accepted" if accepted else "rejected"
        if accepted:
            version = (versions[-1]["version"] + 1) if versions else 1
            versions.append(
                {
                    "version": version,
                    "body": proposal["body"],
                    "evidence_refs": proposal["evidence_refs"],
                    "validation": report,
                }
            )
            data["scopes"][proposal["scope"]] = {"active_version": version, "versions": versions}
            report["version"] = version
        self._write(data)
        return report

    def active(self, scope: str) -> dict | None:
        state = self._read()["scopes"].get(scope)
        if not state:
            return None
        return next(v.copy() for v in state["versions"] if v["version"] == state["active_version"])

    def rollback(self, scope: str, version: int) -> int:
        data = self._read()
        state = data["scopes"][scope]
        if not any(item["version"] == version for item in state["versions"]):
            raise ValueError(f"unknown version {version} for scope {scope!r}")
        state["active_version"] = version
        self._write(data)
        return version


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        learner = PrimeLearner(Path(directory) / "learning.json")
        proposal = learner.propose("demo", "better", ["turn:1"])
        score = lambda body, item: item + (1 if body == "best" else 0) if body else 0
        report = learner.validate(proposal, [1, 2], score)
        assert report["accepted"] and learner.active("demo")["version"] == 1
        regressing = learner.propose("demo", "regressing", ["turn:2"])
        regressed = learner.validate(
            regressing, [1, 2], lambda body, item: 0 if item == 1 else 10
        )
        assert not regressed["accepted"]
        better = learner.propose("demo", "best", ["turn:3"])
        assert learner.validate(better, [1, 2], score)["version"] == 2
        assert learner.rollback("demo", 1) == 1 and learner.active("demo")["version"] == 1
