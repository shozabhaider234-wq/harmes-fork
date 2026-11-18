"""Real provider discovery, A→B→A profile boundaries, daemon and read-only sources."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import test_persistent_client as support
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from agent.secret_scope import set_multiplex_active


class HermesBridgeTests(unittest.TestCase):
    def setUp(self):
        self.server = support.RealModelClientTests() if support.REAL_MODEL else support.PersistentClientTests()
        self.server.setUp()
        self.profiles = [self.server.root / name for name in ("A","B")]
        self.originals = {}
        for home in self.profiles:
            plugin = home / "plugins" / "hermes-memd"
            shutil.copytree(Path(__file__).resolve().parents[1] / "clients" / "python",plugin,ignore=shutil.ignore_patterns("__pycache__"))
            (home / "memd.json").write_text(json.dumps({"endpoint":f"tcp:127.0.0.1:{self.server.address[1]}"}),encoding="utf-8")
            with closing(sqlite3.connect(home / "state.db")) as db:
                db.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT); CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,message_uid TEXT,content TEXT);")
                db.execute("INSERT INTO sessions VALUES('session-1','original conversation')")
                db.execute("INSERT INTO sessions VALUES('session-2','unrelated conversation')")
                db.execute("INSERT INTO messages VALUES(1,'session-1','uid-1',?)",(f"Original {home.name} transcript message",))
                db.execute("INSERT INTO messages VALUES(2,'session-2','uid-2',?)",(f"Private other {home.name} transcript",))
                db.commit()
            self.originals[home] = hashlib.sha256((home / "state.db").read_bytes()).digest()
        set_multiplex_active(True)

    def tearDown(self):
        set_multiplex_active(False)
        self.server.tearDown()

    def test_real_plugin_discovery_scoped_ingestion_context_and_original_sources(self):
        from plugins.memory import load_memory_provider
        from agent.memory_manager import MemoryManager
        manager = MemoryManager()
        provider = None
        expected = {}
        system_prompt = None
        for home in [self.profiles[0],self.profiles[1],self.profiles[0]]:
            token = set_hermes_home_override(home)
            try:
                if provider is None:
                    provider = load_memory_provider("hermes-memd")
                    self.assertIsNotNone(provider)
                    manager.add_provider(provider)
                    self.assertIn("memd_memory",manager.get_all_tool_names())
                self.assertTrue(provider.is_available())
                manager.initialize_all("session-1",hermes_home=home)
                self.assertEqual(provider.system_prompt_block(),"")
                if home not in expected:
                    expected[home] = provider.ingest_memory({"content":f"User in profile {home.name} prefers Rust Cargo examples"},message_uid="uid-1")
                package = provider.recall("Rust Cargo",token_budget=512,token_counter=len)
                self.assertEqual(package.selected_memory_ids,(expected[home],))
                self.assertLessEqual(package.token_count,512)
                brief = provider.recall("Rust Cargo", detail="brief", token_counter=len)
                self.assertEqual(brief.token_budget, 300)
                self.assertEqual(brief.detail, "brief")
                tool_result = json.loads(manager.handle_tool_call("memd_memory",{"action":"recall","query":"Rust Cargo"}))
                self.assertIn(expected[home],tool_result["context"])
                with patch.object(provider, "ingest_memory", return_value="tool-write-id"):
                    remembered = json.loads(provider.handle_tool_call(
                        "memd_memory", {"action":"remember", "record":{"content":"primary write"}},
                    ))
                self.assertEqual(remembered["id"], "tool-write-id")
                detailed_result = json.loads(manager.handle_tool_call("memd_memory",{"action":"recall","query":"Rust Cargo","detail":"brief"}))
                self.assertIn(expected[home],detailed_result["context"])
                self.assertEqual(manager.build_system_prompt(),"")
                history = provider.search_sessions("Original")
                self.assertEqual(len(history),1)
                self.assertIn(home.name,provider.resolve_source(history[0]["source_ref"])["excerpt"])
                self.assertEqual(hashlib.sha256((home / "state.db").read_bytes()).digest(),self.originals[home])
                if system_prompt is None:
                    system_prompt = provider.system_prompt_block()
                self.assertEqual(provider.system_prompt_block(),system_prompt)
                # The other profile's stored URI must be rejected even though session/UID match.
                other_home = self.profiles[1] if home == self.profiles[0] else self.profiles[0]
                from memd.clients.python.hermes_bridge import source_reference
                with self.assertRaises(ValueError):
                    provider.resolve_source(source_reference(other_home,"session-1","uid-1"))
                provider.sync_turn("ordinary raw user turn","ordinary assistant reply",session_id="session-1",messages=[])
                with self.server.client() as client:
                    self.assertEqual(client.request("storage_stats")["memory_count"],len(expected))
            finally:
                reset_hermes_home_override(token)

    def test_subagent_read_only_scope_provenance_and_shared_daemon_concurrency(self):
        from plugins.memory import load_memory_provider
        from memd.clients.python.hermes_bridge import source_reference

        home = self.profiles[0]
        token = set_hermes_home_override(home)
        providers = []
        try:
            primary = load_memory_provider("hermes-memd")
            primary.initialize("session-1", hermes_home=str(home))
            memory_id = primary.ingest_memory({"content": "Subagent scoped Cargo recall evidence"}, message_uid="uid-1")
            _, _, _, scope, _ = primary._binding()
            foreign_scope = {**scope, "agent_id": "unrelated-agent"}
            with self.server.client() as client:
                client.store({"content": "Private memory for another agent", **foreign_scope})
                storage_count = client.request("storage_stats")["memory_count"]

            for index in range(4):
                child = load_memory_provider("hermes-memd")
                child.initialize(
                    f"child-{index}", hermes_home=str(home), agent_context="subagent", parent_session_id="session-1",
                )
                providers.append(child)

            contexts = [copy_context() for _ in range(4)]
            with ThreadPoolExecutor(max_workers=4) as pool:
                packages = list(pool.map(
                    lambda index: contexts[index].run(
                        providers[index].recall, "Cargo", token_budget=1024, token_counter=len,
                    ), range(4),
                ))
            self.assertTrue(all(memory_id in package.selected_memory_ids for package in packages), [
                (package.retrieved_count, package.selected_memory_ids, package.token_count, package.text)
                for package in packages
            ])
            self.assertTrue(all(package.token_count <= 1024 for package in packages))
            self.assertTrue(all("another agent" not in package.text for package in packages))
            with self.server.client() as client:
                self.assertEqual(client.request("storage_stats")["memory_count"], storage_count)

            child = providers[0]
            ref = next(value.split("@", 1)[1] for value in packages[0].provenance if "@" in value)
            source = json.loads(child.handle_tool_call("memd_memory", {"action": "source", "source_ref": ref}))
            self.assertIn("Original", source["excerpt"])
            history = json.loads(child.handle_tool_call("memd_memory", {"action": "history", "query": "Private other"}))
            self.assertEqual(history, [])
            with self.assertRaises(ValueError):
                child.handle_tool_call(
                    "memd_memory", {"action": "source", "source_ref": source_reference(home, "session-2", "uid-2")},
                )
            for action in ("remember", "update", "delete"):
                with self.assertRaises(ValueError):
                    child.handle_tool_call("memd_memory", {"action": action, "record": {"content": "forbidden"}})
        finally:
            for provider in providers:
                provider.shutdown()
            reset_hermes_home_override(token)
