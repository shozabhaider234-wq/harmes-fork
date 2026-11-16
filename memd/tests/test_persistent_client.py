"""Standalone stdlib tests against a real daemon; no Hermes loop or model service."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "python"))
from memory_client import MemdClient, MemoryServiceError
from context_builder import ContextBuilder


class PersistentClientTests(unittest.TestCase):
    def daemon_options(self):
        return ["--synthetic"]

    def listen_options(self):
        return ["--tcp", f"127.0.0.1:{self.address[1]}"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memd-client-")
        self.root = Path(self.temp.name)
        self.binary = Path(os.environ.get("MEMD_BIN", str(Path(__file__).resolve().parents[1] / "target" / "debug" / ("memd.exe" if os.name == "nt" else "memd"))))
        self.assertTrue(self.binary.is_file(), f"build memd first or set MEMD_BIN: {self.binary}")
        self.start()

    def start(self):
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.address = reserve.getsockname()
        self.log = (self.root / "daemon.log").open("ab")
        self.process = subprocess.Popen([str(self.binary), *self.listen_options(),
                                         "--store", str(self.root / "memories.db"), *self.daemon_options()],
                                        stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                with self.client() as client:
                    self.assertEqual(client.ping(), {"status": "ok"})
                return
            except OSError:
                time.sleep(0.02)
        self.stop()
        self.fail((self.root / "daemon.log").read_text())

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.log.close()

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def client(self):
        return MemdClient(tcp_address=self.address)

    def test_existing_protocol_restart_sources_and_transcript_preservation(self):
        transcript = self.root / "original-session.jsonl"
        transcript.write_bytes(b'{"role":"user","content":"Original conversation stays here"}\n')
        original = hashlib.sha256(transcript.read_bytes()).digest()
        source = "hermes://profile-a/sessions/session-a/messages/message-uid-a"
        with self.client() as client:
            self.assertEqual(client.store({"id":"legacy", "content":"Cargo compiler", "source_ref":source}), "legacy")
            self.assertEqual(client.retrieve("Cargo")[0]["source_ref"], source)
            context = ContextBuilder(token_counter=len).build(client.retrieve("Cargo"), token_budget=512)
            self.assertLessEqual(context.token_count, context.token_budget)
            self.assertIn("legacy", context.selected_memory_ids)
            self.assertTrue(any(source in reference for reference in context.provenance))
            self.assertEqual(client.search_sessions("Original conversation"), [])
            scoped = {"namespace":"project-a", "access_scope":"team", "agent_id":"agent-a"}
            self.assertEqual(client.store({"id":"episodic", "text":"Fixed Cargo compiler error", "kind":"episodic",
                                           "session_id":"session-a", "source_ref":source, **scoped}), "episodic")
            stats = client.request("storage_stats")
            self.assertEqual(stats["memory_count"], 2)
            self.assertTrue(stats["sqlite_version"])
            self.assertTrue(stats["vec_version"])
            self.assertEqual(stats["raw_float32_embedding_bytes"], stats["memory_count"] * stats["dimension"] * 4)
            for mode in ("vector", "keyword", "hybrid"):
                self.assertEqual(client.retrieve("Cargo compiler", scope=scoped, mode=mode)[0]["id"], "episodic")
            self.assertEqual(len(client.retrieve("Cargo compiler")), 1)
            self.assertEqual(client.source_lookup("episodic"), {"source_ref":source, "session_id":"session-a"})
            with self.assertRaises(MemoryServiceError):
                client.store({"content":"invalid kind", "kind":"prime_artifact"})
            self.assertEqual(client.ping(), {"status":"ok"})
        self.stop()
        self.start()
        with self.client() as client:
            self.assertEqual(client.get_memory("episodic")["kind"], "episodic")
            self.assertEqual(client.retrieve("Cargo")[0]["id"], "legacy")
            with self.assertRaises(MemoryServiceError) as missing:
                client.update_memory({"id":"missing", "content":"cannot update missing memory"})
            self.assertEqual(missing.exception.code, "not_found")
            client.update_memory({"id":"legacy", "content":"garden flowers"})
            self.assertEqual(client.retrieve("Cargo", mode="keyword"), [])
            self.assertTrue(client.delete_memory("legacy"))
            self.assertIsNone(client.get_memory("legacy"))
        self.assertEqual(hashlib.sha256(transcript.read_bytes()).digest(), original)

    def test_concurrent_clients_and_structured_wire_errors(self):
        self.concurrent_clients()
        self.check_wire_errors()

    def concurrent_clients(self):
        def worker(index):
            with self.client() as client:
                for item in range(10):
                    memory_id = f"client-{index}-{item}"
                    self.assertEqual(client.store({"id":memory_id, "content":f"Cargo compiler {memory_id}"}), memory_id)
                    self.assertTrue(client.retrieve("Cargo", k=2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(worker, range(4)))

    def check_wire_errors(self):
        with socket.create_connection(self.address) as stream:
            stream.sendall(b'bad json\n{"v":2,"id":42,"op":"ping"}\n{"v":1,"id":43,"op":"ping"}\n')
            with stream.makefile("rb") as reader:
                first, second, third = [json.loads(reader.readline()) for _ in range(3)]
            self.assertEqual(first["error"]["code"], "invalid_json")
            self.assertEqual(second["id"], 42)
            self.assertEqual(second["error"]["code"], "unsupported_version")
            self.assertTrue(third["ok"])
        with socket.create_connection(self.address) as stream:
            stream.sendall(b" " * (1024 * 1024 + 1))
            with stream.makefile("rb") as reader:
                response = json.loads(reader.readline())
            self.assertEqual(response["error"]["code"], "request_too_large")


@unittest.skipUnless(os.name == "nt", "native Windows pipes")
class NamedPipeClientTests(PersistentClientTests):
    def listen_options(self):
        return ["--pipe", self.root.name]

    def client(self):
        return MemdClient(pipe_name=self.root.name)

    def check_wire_errors(self):
        with self.client() as client:
            with self.assertRaises(MemoryServiceError) as version:
                client.request("ping", v=2)
            self.assertEqual(version.exception.code,"unsupported_version")
            self.assertEqual(client.ping(), {"status":"ok"})
            with self.assertRaises(MemoryServiceError) as oversized:
                client.store({"content":"x" * (1024 * 1024)})
            self.assertEqual(oversized.exception.code, "request_too_large")
        # The first-instance flag prevents a second daemon from taking over this name.
        second = subprocess.run([str(self.binary), "--pipe", self.root.name, "--mock"], capture_output=True, timeout=5)
        self.assertNotEqual(second.returncode,0)
        for _ in range(5):
            with self.client() as abandoned:
                abandoned._connect()
        with self.client() as healthy:
            self.assertEqual(healthy.ping(),{"status":"ok"})
        with MemdClient(pipe_name=self.root.name,timeout=0.1) as stalled:
            stalled._connect()
            stalled._pipe.write(b"{")  # Deliberately incomplete request: server awaits newline.
            with self.assertRaises(TimeoutError):
                stalled._reader.readline()


@unittest.skipUnless(os.name == "posix", "native Unix sockets")
class UnixSocketClientTests(PersistentClientTests):
    def listen_options(self):
        return ["--socket",str(self.root / "memd.sock")]

    def client(self):
        return MemdClient(socket_path=str(self.root / "memd.sock"))

    def stop(self):
        super().stop()
        # This path belongs only to this test's fresh temporary directory.
        (self.root / "memd.sock").unlink(missing_ok=True)

    def check_wire_errors(self):
        with self.client() as client:
            with self.assertRaises(MemoryServiceError) as version:
                client.request("ping",v=2)
            self.assertEqual(version.exception.code,"unsupported_version")


REAL_MODEL = bool(os.environ.get("MEMD_TEST_MODEL_DIR") and os.environ.get("MEMD_TEST_ORT_DYLIB"))


def model_options():
    return ["--model-dir",os.environ["MEMD_TEST_MODEL_DIR"], "--ort-dylib",os.environ["MEMD_TEST_ORT_DYLIB"]]


@unittest.skipUnless(REAL_MODEL, "set MEMD_TEST_MODEL_DIR and MEMD_TEST_ORT_DYLIB for real-model client tests")
class RealModelClientTests(PersistentClientTests):
    def daemon_options(self):
        return model_options()

    def test_bad_runtime_is_reported_without_panic(self):
        result = subprocess.run([str(self.binary), "--tcp","127.0.0.1:0", "--store",str(self.root / "bad-runtime.db"),
                                 "--model-dir",os.environ["MEMD_TEST_MODEL_DIR"], "--ort-dylib",str(self.root / "missing.dll")],
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("ONNX runtime",result.stderr)
        self.assertNotIn("panicked",result.stderr)
        self.assertFalse((self.root / "bad-runtime.db").exists())
        if os.name == "nt":
            old_runtime = Path(os.environ["SystemRoot"]) / "System32" / "onnxruntime.dll"
            if old_runtime.exists():
                result = subprocess.run([str(self.binary),"--tcp","127.0.0.1:0","--store",str(self.root / "incompatible.db"),
                                         "--model-dir",os.environ["MEMD_TEST_MODEL_DIR"],"--ort-dylib",str(old_runtime)],
                                        capture_output=True,text=True,timeout=15)
                self.assertNotEqual(result.returncode,0)
                self.assertIn("not compatible",result.stderr)
                self.assertNotIn("panicked",result.stderr)


@unittest.skipUnless(REAL_MODEL and os.name == "nt", "requires real model and native Windows pipes")
class RealModelPipeClientTests(NamedPipeClientTests):
    def daemon_options(self):
        return model_options()


if __name__ == "__main__":
    unittest.main()
