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

DETAIL_BUDGETS = {"brief": 300, "normal": 1000, "deep": 2000, "exhaustive": 4000}


def _resolve_budget(detail, configured, token_budget=None, safe_available_tokens=None,
                    context_capacity=None):
    if detail not in DETAIL_BUDGETS:
        raise ValueError(f"detail must be one of: {', '.join(DETAIL_BUDGETS)}")
    budgets = {**DETAIL_BUDGETS, **(configured.get("detail_budgets") or {})}
    available = int(safe_available_tokens if safe_available_tokens is not None
                    else configured.get("safe_available_tokens", 1000))
    reason = "fallback_without_prompt_occupancy"
    if isinstance(context_capacity, dict):
        values = [context_capacity.get(key) for key in (
            "context_window", "prompt_tokens", "assistant_output_tokens", "reserved_output_tokens",
        )]
        if all(isinstance(value, int) and value >= 0 for value in values):
            window, prompt, completion, reserve = values
            memory_calls = max(1, int(context_capacity.get("memory_call_count", 1)))
            other_calls = max(0, int(context_capacity.get("other_tool_call_count", 0)))
            margin = max(0, int(configured.get("safety_margin_tokens", 1024)))
            margin += max(0, int(configured.get("additional_safety_margin_tokens", 0)))
            wrappers = max(0, int(configured.get("tool_result_wrapper_tokens", 128))) * memory_calls
            other_results = max(0, int(configured.get("other_tool_result_reserve_tokens", 512))) * other_calls
            live_available = max(0, (window - prompt - completion - reserve - margin - wrappers - other_results) // memory_calls)
            # The configured safe_available_tokens is a fallback for unknown
            # prompt occupancy, not a permanent ceiling on a live calculation.
            available = live_available
            reason = "usage_anchored_live_estimate"
    hard_cap = int(configured.get("hard_token_cap", configured.get("token_budget", 1000)))
    if token_budget is not None:
        hard_cap = min(hard_cap, int(token_budget))
    if safe_available_tokens is not None:
        available = min(available, max(0, int(safe_available_tokens)))
        reason = "caller_available_tokens" if reason.startswith("fallback") else reason
    return max(0, min(int(budgets[detail]), available, hard_cap)), reason


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
            "token_budget": 4000,
            "safe_available_tokens": 1000,
            "detail_budgets": DETAIL_BUDGETS,
            "minimum_utility_per_token": 0.0,
            "safety_margin_tokens": 1024,
            "additional_safety_margin_tokens": 0,
            "tool_result_wrapper_tokens": 128,
            "other_tool_result_reserve_tokens": 512,
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
                             if key in {"endpoint", "namespace", "top_k", "token_budget", "store_path", "safe_available_tokens", "detail_budgets", "minimum_utility_per_token", "safety_margin_tokens", "additional_safety_margin_tokens", "tool_result_wrapper_tokens", "other_tool_result_reserve_tokens"}}
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
        self._sessions[hermes_home_key(home)] = {
            "session_id": session_id,
            "context": kwargs.get("agent_context", "primary"),
            "parent_session_id": kwargs.get("parent_session_id"),
            "authorized_source_refs": {},
        }

    def build_core_profile(self, *, token_budget=300, token_counter=None):
        """Compile the prompt profile from this profile's own Rust records."""
        _, _, config, scope, _ = self._binding()
        from .core_profile import build_core_profile
        with MemdClient(**endpoint_options(config["endpoint"])) as client:
            return build_core_profile(client, scope, token_budget=token_budget, token_counter=token_counter)

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

    def recall(self, query, *, session_id="", token_budget=None, token_counter=None, detail="normal",
               safe_available_tokens=None, context_capacity=None, token_counter_method=None,
               include_superseded=False):
        _,_,config,scope,_ = self._binding(session_id)
        with MemdClient(**endpoint_options(config["endpoint"])) as client:
            hits = client.retrieve(query,k=min(int(config.get("top_k",10)),100),scope=scope,mode="hybrid",include_superseded=include_superseded)
            hits += client.retrieve(query,k=min(int(config.get("top_k",10)),100),scope={**scope,"namespace":scope["namespace"]+":user"},mode="hybrid",include_superseded=include_superseded)
        effective, reason = _resolve_budget(detail, config, token_budget, safe_available_tokens, context_capacity)
        builder = ContextBuilder(token_counter=token_counter,
                                 minimum_utility_per_token=float(config.get("minimum_utility_per_token", 0.0)),
                                 token_counter_method=token_counter_method)
        if effective == 0:
            return builder.empty_package(len(hits), detail=detail, budget_reason=reason)
        package = builder.build(hits, token_budget=effective, detail=detail, budget_reason=reason,
                                include_superseded=include_superseded)
        if self._binding()[1]["context"] == "subagent":
            refs = (provenance.partition("@")[2] for provenance in package.provenance)
            self._remember_authorized_sources(ref for ref in refs if ref)
        return package

    def _remember_authorized_sources(self, references):
        state = self._binding()[1]
        authorized = state["authorized_source_refs"]
        for reference in references:
            authorized[reference] = None
            if len(authorized) > 500:
                # ponytail: cap retained provenance at 500 refs per child; raise only if long jobs need older links.
                del authorized[next(iter(authorized))]

    def shutdown(self):
        self._sessions.pop(hermes_home_key(),None)

    def get_tool_schemas(self):
        schema = {"name":"memd_memory","description":"Use memd_memory.recall to retrieve relevant historical memory when the answer depends on past preferences, project decisions, or facts absent from the current context. Do not recall for self-contained questions or when the compact core profile is sufficient. Use action=remember for an explicitly requested or otherwise justified project/event/episodic record that should persist as a searchable memory; use the ordinary memory tool for the curated durable profile (standing user preferences, environment facts, and conventions), and avoid duplicating the same fact in both stores. Use source/history only to inspect provenance or prior sessions. Never send full transcripts as memories.",
                 "parameters":{"type":"object","properties":{
                     "action":{"type":"string","enum":["remember","recall","source","history"]},
                     "target":{"type":"string","enum":["memory","user"],"description":"For remember: user for persona/preferences, memory for project/event/episodic facts (default). Prefer the ordinary memory tool for the curated standing profile; avoid duplicate writes."},
                     "record":{"type":"object","properties":{"content":{"type":"string"},"kind":{"type":"string","enum":["semantic","episodic"]},"tags":{"type":"array","items":{"type":"string"}}},"required":["content"]},
                     "query":{"type":"string"},"detail":{"type":"string","enum":["brief","normal","deep","exhaustive"],"description":"Optional recall detail; defaults to normal."},"include_superseded":{"type":"boolean","description":"Include explicitly superseded records for historical questions."},"source_ref":{"type":"string"}},"required":["action"]}}
        state = self._sessions.get(hermes_home_key())
        if state and state.get("context") == "subagent":
            schema["parameters"]["properties"]["action"]["enum"] = ["recall", "source", "history"]
            schema["parameters"]["properties"].pop("record")
            schema["parameters"]["properties"].pop("target")
        return [schema]

    def handle_tool_call(self, tool_name, args, **kwargs):
        if tool_name != "memd_memory":
            raise ValueError("unknown memory tool")
        _, state, _, _, _ = self._binding()
        action = args.get("action")
        if state.get("context") == "subagent" and action not in {"recall", "source", "history"}:
            raise ValueError("subagents have read-only memd access")
        actions = {
            "remember":lambda: {"id":self.ingest_memory(args["record"], target=args.get("target") or "memory")},
            "recall":lambda: {"context":self.recall(args["query"], detail=args.get("detail", "normal"),
                                                       token_counter=kwargs.get("token_counter"),
                                                       token_counter_method=kwargs.get("token_counter_method"),
                                                       context_capacity=kwargs.get("context_capacity"),
                                                       include_superseded=bool(args.get("include_superseded", False))).text},
            "source":lambda: self._resolve_authorized_source(args["source_ref"], state),
            "history":lambda: self.search_sessions(args["query"]),
        }
        if action not in actions:
            raise ValueError("unknown memory action")
        return json.dumps(actions[action](),ensure_ascii=False)

    def _resolve_authorized_source(self, reference, state):
        if state.get("context") == "subagent" and reference not in state["authorized_source_refs"]:
            raise ValueError("source reference was not authorized by this subagent's recall or history results")
        return self.resolve_source(reference)

    def _history_db(self):
        path = (get_hermes_home() / "state.db").resolve()
        db = sqlite3.connect(path.as_uri() + "?mode=ro",uri=True,timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db

    def search_sessions(self, query, k=20):
        if not isinstance(query,str) or not query.strip():
            return []
        _, state, _, _, _ = self._binding()
        parent_session_id = state.get("parent_session_id") if state.get("context") == "subagent" else None
        if state.get("context") == "subagent" and not parent_session_id:
            raise ValueError("subagent history scope is unavailable")
        with closing(self._history_db()) as db:
            # Return bounded excerpts, never persist or embed session-history content.
            if parent_session_id:
                rows = db.execute("SELECT session_id,message_uid,substr(content,1,400) AS excerpt FROM messages WHERE session_id=? AND instr(lower(content),lower(?))>0 ORDER BY id DESC LIMIT ?",(parent_session_id,query,min(max(int(k),0),100))).fetchall()
            else:
                rows = db.execute("SELECT session_id,message_uid,substr(content,1,400) AS excerpt FROM messages WHERE instr(lower(content),lower(?))>0 ORDER BY id DESC LIMIT ?",(query,min(max(int(k),0),100))).fetchall()
        home = get_hermes_home()
        results = [{**dict(row),"source_ref":source_reference(home,row["session_id"],row["message_uid"])} for row in rows]
        if parent_session_id:
            self._remember_authorized_sources(item["source_ref"] for item in results)
        return results

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
                row = db.execute("SELECT session_id,message_uid,substr(content,1,400) AS excerpt FROM messages WHERE message_uid=? AND session_id=? ORDER BY id DESC LIMIT 1",(unquote(segments[3]),session)).fetchone()
        return dict(row) if row else None
