"""Real provider discovery, A→B→A profile boundaries, daemon and read-only sources."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest

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
                db.execute("INSERT INTO messages VALUES(1,'session-1','uid-1',?)",(f"Original {home.name} transcript message",))
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
                tool_result = json.loads(manager.handle_tool_call("memd_memory",{"action":"recall","query":"Rust Cargo"}))
                self.assertIn(expected[home],tool_result["context"])
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
