"""Native Hermes memory semantics with Rust inventory/transaction persistence."""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from difflib import SequenceMatcher
import threading
from uuid import uuid4

from tools.memory_tool_store import MemoryStore
from .memory_client import MemdClient


class RustMemoryStore(MemoryStore):
    uses_markdown = False

    def __init__(self, *, endpoint, scope, home, **kwargs):
        super().__init__(**kwargs)
        self.endpoint, self.scope, self.home = endpoint, scope, home
        self._lock = threading.RLock()
        self._provenance = ContextVar("memd_native_provenance", default={})
        self._explicit_record = ContextVar("memd_explicit_record", default={})

    def _scope(self, target):
        return {**self.scope, "namespace": self.scope["namespace"] + (":user" if target == "user" else "")}

    @contextmanager
    def write_context(self, metadata):
        token = self._provenance.set(metadata)
        try:
            yield
        finally:
            self._provenance.reset(token)

    def _read_file(self, path):
        # The inherited loader supplies native threat scanning and a frozen snapshot.
        # No Markdown content is read, imported or written.
        target = "user" if path.name == "USER.md" else "memory"
        with MemdClient(**self.endpoint) as client:
            return [record["content"] for record in client.inventory(self._scope(target))]

    def _path_for(self, target):
        return self.home / "memories" / ("USER.md" if target == "user" else "MEMORY.md")

    def _desired(self, target, records, entries):
        old = [record["content"] for record in records]
        # Retain existing IDs for unchanged entries and one-for-one replacements.
        assigned = {}
        for tag, a, b, c, d in SequenceMatcher(a=old, b=entries, autojunk=False).get_opcodes():
            if tag in ("equal", "replace"):
                assigned.update((j, records[i]) for i, j in zip(range(a,b), range(c,d)))
        by_text = {record["content"]: record for record in records}
        assigned.update((i, by_text[text]) for i,text in enumerate(entries) if text in by_text)
        # Moving an unchanged entry must not also assign its ID to an edited entry.
        reserved = {record["id"] for i,record in assigned.items() if entries[i] == record["content"]}
        used = set()
        desired = []
        metadata = {k:str(v) for k,v in self._provenance.get().items() if v is not None}
        for index, text in enumerate(entries):
            previous = assigned.get(index)
            if previous and (previous["id"] in used or (previous["id"] in reserved and previous["content"] != text)):
                previous = None
            if previous and previous["content"] == text:
                record = copy.deepcopy(previous)
            else:
                now = datetime.now(timezone.utc).isoformat()
                record = copy.deepcopy(previous) if previous else {
                    "id":"mem_" + uuid4().hex, "created_at":now, "category":"preference" if target == "user" else "fact",
                    "tags":[], "kind":"semantic", "search_text":"", **self._scope(target),
                }
                record.update(content=text, updated_at=now)
                record.update(self._explicit_record.get())
                record["metadata"] = {**record.get("metadata",{}), **metadata, "hermes_target":target}
                if metadata.get("session_id"):
                    from .hermes_bridge import source_reference
                    record["session_id"] = metadata["session_id"]
                    record["source_ref"] = metadata.get("source_ref") or source_reference(self.home, metadata["session_id"])
                elif metadata.get("source_ref"):
                    record["source_ref"] = metadata["source_ref"]
            desired.append(record)
            used.add(record["id"])
        return desired

    def remember(self, record, metadata, target="memory"):
        record = {**record, "content":record["content"].strip()}
        token = self._explicit_record.set(record)
        try:
            with self.write_context(metadata):
                result = self.add(target, record["content"])
            if not result.get("success"):
                raise RuntimeError(result.get("error", "memory write failed"))
            with MemdClient(**self.endpoint) as client:
                return next(r["id"] for r in client.inventory(self._scope(target)) if r["content"] == record["content"].strip())
        finally:
            self._explicit_record.reset(token)

    def _mutate(self, target, mutate, *, skip_drift=False):
        # Share the native store between foreground/reviewer, but not mutable inventories
        # across simultaneous calls. Rust CAS also coordinates other processes.
        with self._lock:
            try:
                with MemdClient(**self.endpoint) as client:
                    records = client.inventory(self._scope(target))
                    self._set_entries(target, list(dict.fromkeys(r["content"] for r in records)))
                    result = mutate(self._entries_for(target), self._char_limit(target))
                    if isinstance(result, dict):
                        return result
                    desired = self._desired(target, records, result[0])
                    if not client.compare_exchange(self._scope(target), records, desired):
                        return {"success":False, "error":"Memory changed concurrently; retry against the current inventory."}
                    self._set_entries(target, result[0])
                    return self._success_response(target, result[1], **(result[2] if len(result)>2 else {}))
            except Exception as error:
                return {"success":False, "error":f"Rust memory persistence failed: {error}"}
