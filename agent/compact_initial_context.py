"""Session-lineage persistence for the immutable Rust-backed initial context."""
from __future__ import annotations

import hashlib
import logging
import time

logger = logging.getLogger(__name__)


def load_or_create(agent, provider):
    session_id = str(getattr(agent, "session_id", "") or "")
    db = getattr(agent, "_session_db", None)
    if not session_id or db is None:
        raise RuntimeError("compact initial context requires a persisted session")
    existing = db.get_initial_context_snapshot(session_id)
    if existing is not None:
        _verify(session_id, existing)
        logger.info(
            "Compact initial context snapshot_id=%s lineage_id=%s token_budget=%s token_count=%s "
            "token_count_method=%s selected_memory_count=%s truncated=%s budget_respected=%s",
            existing["snapshot_hash"], session_id, existing["token_budget"], existing["token_count"],
            existing["token_count_method"], existing["selected_memory_count"], existing["truncated"],
            existing["budget_respected"],
        )
        return existing["context_text"], existing
    if not getattr(agent, "_initial_context_allow_create", True):
        raise RuntimeError("this existing session lineage has no persisted compact initial context")

    get_session = getattr(db, "get_session", None)
    if callable(get_session) and not get_session(session_id):
        # Prompt construction normally precedes the first session-row insert so
        # the row is born with its full prompt. Compact context also needs a row
        # for its FK; the normal turn-start persistence immediately fills prompt.
        ensure = getattr(agent, "_ensure_db_session", None)
        if not callable(ensure):
            raise RuntimeError("session row must exist before compact-context persistence")
        ensure()

    budget = max(1, int(getattr(agent, "_initial_context_token_budget", 800)))
    text, receipt = provider.build_initial_context(
        token_budget=budget,
        token_counter=getattr(agent, "_initial_context_token_counter", None),
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    candidate = {
        "lineage_id": session_id,
        "context_text": text,
        "snapshot_version": 1,
        "snapshot_hash": digest,
        "created_at": time.time(),
        "token_count": int(receipt["token_count"]),
        "token_budget": budget,
        "selected_memory_ids": receipt["selected_memory_ids"],
        "generation_method": receipt["generation_method"],
        "configuration_version": "compact-initial-context-v1",
    }
    stored = db.store_initial_context_snapshot(session_id, candidate)
    db.store_initial_context_diagnostics(session_id, {
        "token_count_method": receipt.get("token_count_method", "unavailable"),
        "selected_memory_count": receipt.get("selected_count", len(receipt["selected_memory_ids"])),
        "truncated": receipt.get("truncated", False),
        "budget_respected": int(receipt["token_count"]) <= budget,
        "generation_elapsed_ms": receipt.get("generation_elapsed_ms", 0),
    })
    stored = db.get_initial_context_snapshot(session_id) or stored
    stored.setdefault("token_count_method", receipt.get("token_count_method", "unavailable"))
    stored.setdefault("selected_memory_count", receipt.get("selected_count", len(receipt["selected_memory_ids"])))
    stored.setdefault("truncated", receipt.get("truncated", False))
    stored.setdefault("budget_respected", int(receipt["token_count"]) <= budget)
    stored.setdefault("generation_elapsed_ms", receipt.get("generation_elapsed_ms"))
    _verify(session_id, stored)
    logger.info(
        "Compact initial context snapshot_id=%s lineage_id=%s token_budget=%s token_count=%s "
        "token_count_method=%s selected_memory_count=%s truncated=%s budget_respected=%s generation_elapsed_ms=%s",
        stored["snapshot_hash"], session_id, stored["token_budget"], stored["token_count"],
        stored["token_count_method"], stored["selected_memory_count"], stored["truncated"],
        stored["budget_respected"], stored["generation_elapsed_ms"],
    )
    return stored["context_text"], stored


def _verify(session_id, snapshot):
    if snapshot["lineage_id"] != session_id:
        raise RuntimeError("persisted compact initial context belongs to another lineage")
    digest = hashlib.sha256(snapshot["context_text"].encode("utf-8")).hexdigest()
    if snapshot["snapshot_hash"] != digest:
        raise RuntimeError("persisted compact initial context failed hash verification")
    if snapshot["token_count"] > snapshot["token_budget"]:
        raise RuntimeError("persisted compact initial context exceeds its token budget")
