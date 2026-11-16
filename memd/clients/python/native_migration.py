"""Explicit, non-destructive import/export of native curated memory.

Run with the intended Hermes profile active; never runs at startup.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path

from tools.memory_tool import ENTRY_DELIMITER, MemoryStore, load_on_disk_store


def import_markdown(store, directory):
    plans = {}
    for target, filename in (("memory","MEMORY.md"), ("user","USER.md")):
        path = Path(directory) / filename
        raw = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        entries = MemoryStore._parse_entries(raw)
        if ENTRY_DELIMITER.join(entries) != raw.strip():
            raise ValueError(f"{filename} does not round-trip; resolve formatting before import")
        # Validate both targets before the first committed write.
        result = store.resolve_batch_entries(target, [{"action":"add","content":entry} for entry in entries]) if entries else {"success":True}
        if not result.get("success"):
            raise ValueError(f"{filename}: {result.get('error')}")
        plans[target] = (entries, path)
    results = {}
    for target, (entries, path) in plans.items():
        write_context = getattr(store, "write_context", None)
        metadata = {"write_origin":"markdown_import", "source_ref":path.resolve().as_uri(),
                    "source_sha256":hashlib.sha256(path.read_bytes()).hexdigest()} if entries else {}
        with write_context(metadata) if write_context else nullcontext():
            results[target] = store.apply_batch(target, [{"action":"add","content":entry} for entry in entries]) if entries else {"success":True}
        if not results[target].get("success"):
            raise RuntimeError(f"Import incomplete at {target}; original files unchanged; retry is idempotent: {results[target]}")
    return results


def export_markdown(store, directory):
    directory = Path(directory)
    # A new directory prevents overwriting the preserved rollback files.
    directory.mkdir(parents=True, exist_ok=False)
    for target, filename in (("memory","MEMORY.md"), ("user","USER.md")):
        # Reload through the selected backend, not a stale prompt snapshot.
        result = store._mutate(target, lambda entries, limit: {"success":True,"entries":list(entries)}, skip_drift=True)
        if not result.get("success"):
            raise RuntimeError(result.get("error"))
        (directory / filename).write_text(ENTRY_DELIMITER.join(result["entries"]), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["import", "export"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    store = load_on_disk_store()
    if args.action == "import":
        print(json.dumps(import_markdown(store, args.directory)))
    else:
        export_markdown(store, args.directory)


if __name__ == "__main__":
    main()
