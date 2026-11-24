"""Synthetic-only fixtures for neutral B2 memory-use benchmark tasks."""

import json
from pathlib import Path


def synthetic_records():
    records = [
        {"content": "Prefers concise technical explanations.", "category": "preference", "target": "user", "importance": 0.95},
        {"content": "Borealis deployment zone is sector-amber-4.", "category": "fact", "target": "memory", "importance": 0.9},
        {"content": "At the architecture review, we agreed that event changes should be stored as append-only records.",
         "category": "historical_note", "target": "memory", "kind": "episodic", "importance": 0.9},
        {"content": "The previous 100-record benchmark used 17,300 total provider tokens for original Hermes.", "category": "benchmark", "target": "memory", "importance": 0.85},
        {"content": "The earlier preference for verbose explanations was superseded.", "category": "preference", "target": "user", "importance": 0.1, "superseded_by": "synthetic-current-pref", "expires_at": "2000-01-01T00:00:00Z"},
        {"content": "This is an irrelevant synthetic travel note for a fictional trip.", "category": "irrelevant", "target": "memory", "importance": 0.1},
    ]
    records.extend({
        "content": f"Synthetic irrelevant archive record {index:03d}: fictional note with no active project value.",
        "category": "irrelevant", "target": "memory", "importance": 0.01,
        "kind": "episodic" if index % 10 == 0 else "semantic",
        "updated_at": "2026-11-18T00:00:00Z",
    } for index in range(94))
    return [{"id": f"core-fixture-{index:03d}", "kind": record.get("kind", "semantic"), "tags": [],
             "session_id": "synthetic-session", "source_ref": f"synthetic://memory/{index:03d}",
             "updated_at": "2026-11-18T00:00:00Z", **record}
            for index, record in enumerate(records)]


def task_cases():
    """Return two question variants for each of the six benchmark categories."""
    cases = [
        ("arithmetic", False, False, None,
         [("What is 25 × 16?", "400"), ("Calculate 42 × 3.", "126")]),
        ("technical", False, False, None,
         [("Explain TCP congestion control.", "congestion window"),
          ("Why does TCP slow transmission after packet loss?", "congestion window")]),
        ("core-preference", True, False, "core-fixture-000",
         [("Explain recursion in my preferred style.", "concise"),
          ("How should you describe a recursive function to me?", "concise")]),
        ("explicit-recall", True, True, "core-fixture-001",
         [("What was the Borealis deployment zone?", "sector-amber-4"),
          ("Name the deployment zone saved for Borealis.", "sector-amber-4")]),
        ("implicit-recall", True, True, "core-fixture-002",
         [("For the next release, should changes overwrite the current event record?", "append-only records"),
          ("When updating an event, should the earlier version be replaced?", "append-only records")]),
        ("unknown-history", True, True, None,
         [("What database did we choose for Project Orion?", "unknown"),
          ("Which rollout date did we settle for Project Orion?", "unknown")]),
    ]
    return [
        {"id": f"{category}-v{variant}", "category": category, "question": question,
         "answer": answer, "requires_memory": requires_memory,
         "requires_detailed_retrieval": requires_retrieval, "source_id": source_id,
         "answer_unavailable": category == "unknown-history"}
        for category, requires_memory, requires_retrieval, source_id, variants in cases
        for variant, (question, answer) in enumerate(variants, 1)
    ]


def write_task_case():
    """Separate explicit write fixture; it is not part of the six answer categories."""
    return {
        "id": "explicit-write-v1",
        "category": "explicit-write",
        "question": "Remember that the synthetic Project Atlas release code is R-482.",
        "answer": "R-482",
        "requires_memory": False,
        "requires_detailed_retrieval": False,
        "source_id": None,
        "answer_unavailable": False,
        "memory_write_expected": True,
    }


def write_fixture(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    memories = directory / "memories.jsonl"
    tasks = directory / "tasks.jsonl"
    memories.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in synthetic_records()), encoding="utf-8")
    tasks.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in task_cases()), encoding="utf-8")
    return memories, tasks


if __name__ == "__main__":
    print("\n".join(map(str, write_fixture(Path(__file__).parent / "fixtures"))))
