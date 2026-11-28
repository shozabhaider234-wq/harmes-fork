"""Bounded extraction of explicitly marked durable facts at a compression boundary."""
from __future__ import annotations

import json
import logging
import re
import time

logger = logging.getLogger(__name__)

_CANDIDATE = re.compile(r"^\s*(decision|preference|constraint|goal|correction|project progress|lesson)\s*:\s*(.*?)\s*$", re.I)
_SENSITIVE = re.compile(r"(?i)\b(password|passphrase|api[_ -]?key|secret|bearer|access[_ -]?token|refresh[_ -]?token|private key)\b")
_UNCERTAIN = re.compile(r"(?i)\b(maybe|possibly|perhaps|guess|not sure|might be|could be)\b")
_MAX_INPUT_CHARS = 12000
_MAX_CANDIDATES = 8
_WORDS = {"a", "an", "and", "for", "in", "is", "of", "on", "or", "the", "to", "use", "with"}


def _user_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _candidates(messages: list[dict]) -> tuple[list[tuple[str, str]], int]:
    """Only accept user-authored, explicitly categorized statements; tool/model text is data, not authority."""
    found, seen, consumed, rejected = [], set(), 0, 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _user_text(message)[:_MAX_INPUT_CHARS - consumed]
        consumed += len(text)
        for line in text.splitlines():
            match = _CANDIDATE.match(line)
            if not match:
                continue
            category, body = match.group(1).casefold(), " ".join(match.group(2).split())
            normalized = body.casefold()
            if len(body) < 8 or len(body) > 1200 or _SENSITIVE.search(body) or _UNCERTAIN.search(body):
                rejected += 1
                continue
            if normalized in seen:
                continue
            from tools.threat_patterns import scan_for_threats
            if scan_for_threats(body, scope="strict"):
                rejected += 1
                continue
            seen.add(normalized)
            found.append((category, body))
            if len(found) >= _MAX_CANDIDATES:
                return found, rejected
        if consumed >= _MAX_INPUT_CHARS:
            break
    return found, rejected


def extract_before_child_context(agent, messages: list[dict], parent_session_id: str) -> dict:
    """Persist explicit durable candidates before publishing the child prompt.

    The deterministic extractor intentionally favors precision over recall. Failed extraction
    is recorded and compression proceeds with already stored Rust memories.
    """
    db = getattr(agent, "_session_db", None)
    if db is None:
        return {"status": "incomplete", "reason": "session database unavailable", "stored": 0}

    # A parent can publish one compression child. Its id is therefore a stable retry key.
    compaction_id = f"compression:{parent_session_id}"
    try:
        claimed = db.begin_compaction_memory_extraction(compaction_id, parent_session_id)
    except Exception as exc:
        logger.warning("Compaction extraction claim failed compaction_id=%s error=%s", compaction_id, exc)
        return {"status": "incomplete", "reason": "extraction status persistence failed", "stored": 0}
    if not claimed:
        try:
            receipt = db.get_compaction_memory_extraction(compaction_id) or {}
        except Exception as exc:
            logger.warning("Compaction extraction receipt lookup failed compaction_id=%s error=%s", compaction_id, exc)
            return {"status": "incomplete", "reason": "extraction receipt unavailable", "stored": 0, "reused": True}
        return {"status": receipt.get("status", "running"), **(receipt.get("result") or {}), "reused": True}

    stored, duplicate, conflict, rejected = [], 0, 0, 0
    started = time.monotonic()
    try:
        manager = getattr(agent, "_memory_manager", None)
        provider = manager.get_provider("hermes-memd") if manager else None
        if provider is None or not callable(getattr(provider, "ingest_memory", None)):
            result = {"status": "incomplete", "reason": "Rust memory provider unavailable", "stored": 0,
                      "model_calls": 0, "output_tokens": 0, "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}
            db.finish_compaction_memory_extraction(compaction_id, "incomplete", result)
            logger.warning("Compaction memory extraction incomplete compaction_id=%s reason=%s", compaction_id, result["reason"])
            return result
        candidates, rejected = _candidates(messages)
        input_characters = min(_MAX_INPUT_CHARS, sum(len(_user_text(message)) for message in messages
                                                     if isinstance(message, dict) and message.get("role") == "user"))
        inventory = provider.memory_inventory()
        by_content = {" ".join(str(row.get("content", "")).casefold().split()): row for row in inventory}
        for category, body in candidates:
            memory_text = f"{category.title()}: {body}"
            normalized = " ".join(memory_text.casefold().split())
            if normalized in by_content or any(normalized.endswith(key) for key in by_content):
                duplicate += 1
                continue
            kind = "preference" if category == "preference" else "decision" if category in {"decision", "correction"} else category.replace(" ", "_")
            candidate_words = {word for word in re.findall(r"[a-z0-9]+", body.casefold()) if word not in _WORDS}
            conflicts = []
            for row in inventory:
                if category != "correction" and str(row.get("category", "")).casefold() != kind:
                    continue
                prior_words = {word for word in re.findall(r"[a-z0-9]+", str(row.get("content", "")).casefold()) if word not in _WORDS}
                overlap = len(candidate_words & prior_words) / max(1, len(candidate_words | prior_words))
                if overlap >= 0.5 and row.get("id"):
                    conflicts.append(str(row["id"]))
            record = {
                "content": memory_text, "category": kind,
                "kind": "semantic", "importance": 0.9, "confidence": 0.95,
                "tags": ["compaction_extracted"],
                "metadata": {"compaction_id": compaction_id, "source_session_id": parent_session_id,
                             "source_role": "user", "provenance": "explicit_user_statement",
                             # memcore stores metadata as a string map. Keep the complete conflict
                             # provenance while honoring that wire/storage contract.
                             "conflicts_with": json.dumps(conflicts, separators=(",", ":"))},
            }
            try:
                memory_id = provider.ingest_memory(record, session_id=parent_session_id, target="user" if kind == "preference" else "memory")
            except Exception:
                # The incomplete receipt keeps retries from blindly duplicating writes;
                # next lineage can still use all successfully stored and prior records.
                raise
            stored.append(str(memory_id))
            by_content[normalized] = {"id": memory_id, "category": kind}
            if conflicts:
                conflict += 1
        result = {"status": "complete", "stored": len(stored), "memory_ids": stored,
                  "duplicates": duplicate, "conflicts_retained": conflict, "rejected": rejected,
                  "candidate_count": len(candidates), "input_characters": input_characters,
                  "model_calls": 0, "output_tokens": 0, "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                  "method": "explicit_user_statement_rules_v1"}
        try:
            db.finish_compaction_memory_extraction(compaction_id, "complete", result)
        except Exception as exc:
            logger.warning("Compaction extraction receipt failed compaction_id=%s error=%s", compaction_id, exc)
            return {**result, "status": "incomplete", "reason": "extraction status persistence failed"}
        logger.info("Compaction memory extraction completed compaction_id=%s result=%s", compaction_id, json.dumps(result))
        return result
    except Exception as exc:
        result = {"status": "incomplete", "stored": len(stored), "memory_ids": stored,
                  "error": str(exc)[:300], "model_calls": 0, "output_tokens": 0,
                  "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                  "method": "explicit_user_statement_rules_v1"}
        try:
            db.finish_compaction_memory_extraction(compaction_id, "incomplete", result)
        except Exception as receipt_error:
            logger.warning("Compaction extraction failure receipt failed compaction_id=%s error=%s", compaction_id, receipt_error)
        logger.warning("Compaction memory extraction incomplete compaction_id=%s error=%s", compaction_id, exc)
        return result
