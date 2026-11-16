"""Opt-in Hermes adapter: native review persistence and independent budgeted recall."""
import hashlib
from contextlib import closing
import filecmp
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import quote, unquote, urlsplit

from agent.memory_provider import MemoryProvider
from hermes_constants import get_hermes_home, hermes_home_key

if __package__:
    from .memory_client import MemdClient
    from .context_builder import ContextBuilder
else:
    from memory_client import MemdClient
    from context_builder import ContextBuilder


def profile_identity(home):
    return hashlib.sha256(hermes_home_key(home).encode()).hexdigest()


def source_reference(home, session_id, message_uid=None):
    if not session_id:
        raise ValueError("a session ID is required for provenance")
    reference = f"hermes://{profile_identity(home)}/sessions/{quote(session_id,safe='')}"
    if message_uid:
        reference += "/messages/" + quote(message_uid,safe="")
    return reference


def endpoint_options(endpoint):
    scheme,separator,address = endpoint.partition(":")
    if not separator or not address:
        raise ValueError("endpoint must be pipe:NAME, unix:PATH or tcp:LOOPBACK:PORT")
    if scheme == "pipe":
        return {"pipe_name":address}
    if scheme == "unix":
        if not Path(address).is_absolute():
            raise ValueError("Unix socket path must be absolute")
        return {"socket_path":address}
    if scheme == "tcp":
        host,separator,port = address.rpartition(":")
        host = host.strip("[]")
        if not separator or not ipaddress.ip_address(host).is_loopback:
            raise ValueError("TCP endpoint must be loopback")
        return {"tcp_address":(host,int(port))}
    raise ValueError("unsupported endpoint scheme")


class MemdMemoryProvider(MemoryProvider):
    name = "hermes-memd"
    _start_lock = threading.Lock()

    def __init__(self, config=None):
        self._override = config
        self._sessions = {}

    def _config(self, home):
        config = self._override if self._override is not None else json.loads((Path(home) / "memd.json").read_text(encoding="utf-8-sig"))
        endpoint_options(config["endpoint"])
        return config

    def is_available(self):
        try:
            self._config(get_hermes_home())
            return True
        except (OSError,ValueError,KeyError,TypeError):
            return False

    def unavailable_reason(self):
        return "Configure endpoint in this profile's memd.json with hermes memory setup."

    def get_config_schema(self):
        return [{"key":"endpoint","description":"Local memd endpoint","default":self._default_endpoint()}]

    @staticmethod
    def _default_endpoint():
        if os.name == "nt":
            return "pipe:hermes-memd"
        return "unix:" + str(get_hermes_home() / "memd" / "memd.sock")

    @staticmethod
    def _copy_if_different(source, target):
        target = Path(target)
        if target.is_file() and filecmp.cmp(source, target, shallow=False):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        return Path(shutil.copy2(source, target))

    @staticmethod
    def _find_distribution_root():
        # Setup is normally run from the Hermes checkout. The installed provider and
        # daemon are copied into the profile, so later sessions do not depend on cwd.
        for parent in (Path.cwd(), *Path.cwd().parents):
            root = parent / "memd"
            if (root / "Cargo.toml").is_file() and (root / "models" / "all-MiniLM-L6-v2").is_dir():
                return root
        return None

    @staticmethod
    def _build_daemon(source):
        if os.name == "nt":
            script = source / "scripts" / "windows.ps1"
            runtime = source / "onnxruntime" / "onnxruntime-win-x64-1.31.1" / "lib" / "onnxruntime.dll"
            if not script.is_file() or not runtime.is_file():
                raise RuntimeError("The Windows memd build helper or ONNX Runtime DLL is missing from the checkout.")
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                            "-Action", "build", "-Release"], check=True, cwd=source.parent)
            return source / "target-gnu" / "release" / "memd.exe"
        subprocess.run(["cargo", "build", "--release", "--manifest-path", str(source / "Cargo.toml"), "-p", "memd"],
                       check=True, cwd=source.parent)
        return source / "target" / "release" / "memd"

    def post_setup(self, hermes_home, config):
        """Install local runtime assets and persist the default daemon configuration."""
        home = Path(hermes_home)
        target = home / "memd"
        source = self._find_distribution_root()
        if source is None:
            raise RuntimeError(
                "Could not find the Hermes checkout containing the local memd model. Run `hermes memory setup` "
                "from the checkout that contains the Team A memd directory."
            )
        print("  Preparing the local memd runtime; first setup may take a few minutes...")
        source_binary = self._build_daemon(source)
        target.mkdir(parents=True, exist_ok=True)
        binary = "memd.exe" if os.name == "nt" else "memd"
        self._copy_if_different(source_binary, target / binary)
        shutil.copytree(source / "models" / "all-MiniLM-L6-v2", target / "models" / "all-MiniLM-L6-v2",
                        dirs_exist_ok=True, copy_function=self._copy_if_different)
        runtime = source / "onnxruntime" / "onnxruntime-win-x64-1.31.1" / "lib" / "onnxruntime.dll"
        if os.name == "nt":
            if not runtime.is_file():
                raise RuntimeError(f"ONNX Runtime library is missing: {runtime}")
            (target / "lib").mkdir(exist_ok=True)
            self._copy_if_different(runtime, target / "lib" / runtime.name)
        else:
            runtime_name = "libonnxruntime.dylib" if sys.platform == "darwin" else "libonnxruntime.so"
            configured_runtime = os.environ.get("ORT_DYLIB_PATH")
            runtime = Path(configured_runtime) if configured_runtime else next(source.rglob(runtime_name), None)
            if runtime is None or not runtime.is_file():
                raise RuntimeError(f"Could not find {runtime_name}; install ONNX Runtime and set ORT_DYLIB_PATH before setup.")
            (target / "lib").mkdir(exist_ok=True)
            self._copy_if_different(runtime, target / "lib" / runtime.name)

        provider_config = {
            "endpoint": self._default_endpoint(),
            "namespace": "hermes",
            "top_k": 10,
            "token_budget": 1000,
            "daemon_path": str((target / binary).resolve()),
            "model_dir": str((target / "models" / "all-MiniLM-L6-v2").resolve()),
            "store_path": str((target / "memories.db").resolve()),
        }
        runtime_path = target / "lib" / ("onnxruntime.dll" if os.name == "nt" else runtime.name)
        provider_config["ort_dylib"] = str(runtime_path.resolve())
        path = home / "memd.json"
        if path.is_file():
            try:
                previous = json.loads(path.read_text(encoding="utf-8-sig"))
                preserved = {key: value for key, value in previous.items()
                             if key in {"endpoint", "namespace", "top_k", "token_budget", "store_path"}}
                if "endpoint" in preserved:
                    endpoint_options(preserved["endpoint"])
                provider_config.update(preserved)
            except (OSError, ValueError, AttributeError, TypeError):
                pass
        path.write_text(json.dumps(provider_config, indent=2), encoding="utf-8")
        config.setdefault("memory", {})["provider"] = self.name
        config["memory"][self.name] = provider_config
        from hermes_cli.config import save_config
        save_config(config)
        print("  Installed local memd runtime and selected hermes-memd. It starts automatically with Hermes.")

    def _ensure_daemon(self, home, config):
        options = endpoint_options(config["endpoint"])
        try:
            with MemdClient(**options, timeout=1) as client:
                client.ping()
                return
        except Exception:
            pass
        binary = config.get("daemon_path")
        model_dir = config.get("model_dir")
        store_path = config.get("store_path") or str(Path(home) / "memd" / "memories.db")
        if not binary or not model_dir or not Path(binary).is_file() or not Path(model_dir).is_dir():
            # Manual daemon deployments remain supported; only auto-start when setup
            # installed a complete local runtime.
            return
        args = [binary]
        if options.get("pipe_name"):
            args += ["--pipe", options["pipe_name"]]
        elif options.get("socket_path"):
            Path(options["socket_path"]).parent.mkdir(parents=True, exist_ok=True)
            args += ["--socket", options["socket_path"]]
        args += ["--store", store_path, "--model-dir", model_dir, "--model-revision", "model-v1"]
        if config.get("ort_dylib"):
            args += ["--ort-dylib", config["ort_dylib"]]
        Path(store_path).parent.mkdir(parents=True, exist_ok=True)
        log_path = Path(home) / "logs" / "memd.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._start_lock:
            try:
                with MemdClient(**options, timeout=1) as client:
                    client.ping()
                    return
            except Exception:
                pass
            with log_path.open("ab") as log:
                kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "cwd": str(Path(binary).parent)}
                if os.name == "nt":
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
                else:
                    kwargs["start_new_session"] = True
                subprocess.Popen(args, **kwargs)
            for _ in range(100):
                try:
                    with MemdClient(**options, timeout=1) as client:
                        client.ping()
                        return
                except Exception:
                    time.sleep(0.1)
        raise RuntimeError(f"memd did not become ready; see {log_path}")

    def save_config(self, values, hermes_home):
        endpoint_options(values["endpoint"])
        path = Path(hermes_home) / "memd.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values,indent=2),encoding="utf-8")

    def initialize(self, session_id, **kwargs):
        home = kwargs.get("hermes_home") or get_hermes_home()
        config = self._config(home)
        self._ensure_daemon(home, config)
        self._sessions[hermes_home_key(home)] = {"session_id":session_id,"context":kwargs.get("agent_context","primary")}

    def create_native_store(self, **kwargs):
        from .native_store import RustMemoryStore
        home = get_hermes_home()
        config = self._config(home)
        scope = {"namespace":str(config.get("namespace","hermes")) + ":" + profile_identity(home),
                 "access_scope":"private", "agent_id":config.get("agent_id")}
        return RustMemoryStore(endpoint=endpoint_options(config["endpoint"]), scope=scope, home=home, **kwargs)

    def on_session_switch(self, new_session_id, **kwargs):
        self._sessions[hermes_home_key()]["session_id"] = new_session_id

    def _binding(self, session_id=""):
        home = get_hermes_home()
        state = self._sessions.get(hermes_home_key(home))
        if state is None:
            raise RuntimeError("memd provider is not initialized for this profile")
        config = self._config(home)
        scope = {"namespace":str(config.get("namespace","hermes")) + ":" + profile_identity(home),
                 "access_scope":"private", "agent_id":config.get("agent_id")}
        return home,state,config,scope,session_id or state["session_id"]

    def ingest_memory(self, record, *, session_id="", message_uid=None, target="memory"):
        home,state,config,scope,session_id = self._binding(session_id)
        if state["context"] != "primary":
            raise ValueError("memory ingestion is disabled for non-primary agent contexts")
        if "id" in record:
            raise ValueError("the bridge allocates new memory IDs; use the trusted daemon administration interface for updates")
        # Use the same native mutations as background review, without resetting the
        # foreground native-memory counter (this is a distinct provider tool).
        from tools.memory_tool import get_builtin_memory_config, get_builtin_memory_store_flags
        from hermes_cli.config import load_config
        native_config = load_config()
        limits = get_builtin_memory_config(native_config)
        enabled, user_enabled = get_builtin_memory_store_flags(native_config)
        if target not in ("memory", "user"):
            raise ValueError("target must be memory or user")
        if not (user_enabled if target == "user" else enabled):
            raise ValueError(f"{target} memory is disabled in memory config")
        allowed = {"content", "category", "tags", "kind", "importance", "confidence", "expires_at", "metadata"}
        record = {k:v for k,v in record.items() if k in allowed}
        store = self.create_native_store(
            memory_char_limit=int(limits.get("memory_char_limit",2200)),
            user_char_limit=int(limits.get("user_char_limit",1375)),
            memory_enabled=enabled, user_profile_enabled=user_enabled)
        return store.remember(record, {"session_id":session_id, "write_origin":"explicit_remember",
                                      "source_ref":source_reference(home,session_id,message_uid)}, target=target)

    def recall(self, query, *, session_id="", token_budget=None, token_counter=None):
        _,_,config,scope,_ = self._binding(session_id)
        with MemdClient(**endpoint_options(config["endpoint"])) as client:
            hits = client.retrieve(query,k=min(int(config.get("top_k",10)),100),scope=scope,mode="hybrid")
            hits += client.retrieve(query,k=min(int(config.get("top_k",10)),100),scope={**scope,"namespace":scope["namespace"]+":user"},mode="hybrid")
        return ContextBuilder(token_counter=token_counter).build(hits, token_budget=int(token_budget or config.get("token_budget",1000)))

    def shutdown(self):
        self._sessions.pop(hermes_home_key(),None)

    def get_tool_schemas(self):
        return [{"name":"memd_memory","description":"Remember an explicitly selected semantic fact or episode; recall a budgeted context package; inspect original session provenance. Never send full transcripts as memories.",
                 "parameters":{"type":"object","properties":{
                     "action":{"type":"string","enum":["remember","recall","source","history"]},
                     "target":{"type":"string","enum":["memory","user"],"description":"For remember: user for persona/preferences, memory for environment/project facts (default). Use the same target as native memory to deduplicate."},
                     "record":{"type":"object","properties":{"content":{"type":"string"},"kind":{"type":"string","enum":["semantic","episodic"]},"tags":{"type":"array","items":{"type":"string"}}},"required":["content"]},
                     "query":{"type":"string"},"source_ref":{"type":"string"}},"required":["action"]}}]

    def handle_tool_call(self, tool_name, args, **kwargs):
        if tool_name != "memd_memory":
            raise ValueError("unknown memory tool")
        actions = {
            "remember":lambda: {"id":self.ingest_memory(args["record"], target=args.get("target") or "memory")},
            "recall":lambda: {"context":self.recall(args["query"]).text},
            "source":lambda: self.resolve_source(args["source_ref"]),
            "history":lambda: self.search_sessions(args["query"]),
        }
        if args.get("action") not in actions:
            raise ValueError("unknown memory action")
        return json.dumps(actions[args["action"]](),ensure_ascii=False)

    def _history_db(self):
        path = (get_hermes_home() / "state.db").resolve()
        db = sqlite3.connect(path.as_uri() + "?mode=ro",uri=True,timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db

    def search_sessions(self, query, k=20):
        if not isinstance(query,str) or not query.strip():
            return []
        with closing(self._history_db()) as db:
            # Return bounded excerpts, never persist or embed session-history content.
            rows = db.execute("SELECT session_id,message_uid,substr(content,1,400) AS excerpt FROM messages WHERE instr(lower(content),lower(?))>0 ORDER BY id DESC LIMIT ?",(query,min(max(int(k),0),100))).fetchall()
        home = get_hermes_home()
        return [{**dict(row),"source_ref":source_reference(home,row["session_id"],row["message_uid"])} for row in rows]

    def resolve_source(self, reference):
        parts = urlsplit(reference)
        segments = parts.path.strip("/").split("/")
        if parts.scheme != "hermes" or parts.netloc != profile_identity(get_hermes_home()) or parts.query or parts.fragment or len(segments) not in (2,4) or segments[0] != "sessions":
            raise ValueError("source reference is outside the active profile")
        session = unquote(segments[1])
        with closing(self._history_db()) as db:
            if len(segments) == 2:
                row = db.execute("SELECT id,title FROM sessions WHERE id=?",(session,)).fetchone()
            else:
                if segments[2] != "messages":
                    raise ValueError("invalid message reference")
                row = db.execute("SELECT session_id,message_uid,substr(content,1,400) AS excerpt FROM messages WHERE message_uid=? ORDER BY (session_id=?) DESC,id DESC LIMIT 1",(unquote(segments[3]),session)).fetchone()
        return dict(row) if row else None
