"""One bounded, event-driven dispatcher for automatic reviews of every provider.

Each profile is registered from an owning foreground context. On restart agent
initialization reattaches its profile's queue, including sessions no longer open.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import logging
import threading
import time
import uuid
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from agent.review_jobs import ReviewJobs

logger = logging.getLogger(__name__)
WORKERS = 8


def runtime_scope(agent, config=None):
    from agent.background_review import _resolve_review_runtime

    try:
        runtime = _resolve_review_runtime(agent, config)
        url = urlsplit(str(runtime.get("base_url") or ""))
        # Models on one endpoint can share a GPU or account quota. Never key on credentials.
        endpoint = f"{url.scheme}://{url.hostname}:{url.port}" if url.hostname else f"provider:{runtime.get('provider')}"
    except Exception as error:
        # A broken route must produce a durable, diagnosable failed job, rather than
        # fail before enqueue. Execution will report/classify the actual runtime error.
        logger.warning("Review runtime scope unresolved: %s", type(error).__name__)
        endpoint = f"unresolved:{agent.provider}"
    return hashlib.sha256(endpoint.encode()).hexdigest()


def scheduling_mode(config):
    mode = str(config.get("scheduling_mode", "auto")).lower()
    if mode == "auto":
        # Hermes has no universal provider concurrency-capability contract. Unknown
        # capacity is treated conservatively; explicit concurrent opts in for any runtime.
        return "concurrent" if config.get("defer") == "never" else "idle"
    return mode if mode in {"idle", "concurrent"} else "idle"


def _provider_identity(agent):
    if agent.provider != "custom":
        return agent.provider
    from hermes_cli.runtime_provider import canonical_custom_identity
    return canonical_custom_identity(base_url=getattr(agent, "base_url", None), model=agent.model,
                                     config_provider=getattr(agent, "requested_provider", None)) or agent.provider


@dataclass
class _Profile:
    jobs: ReviewJobs
    context: contextvars.Context
    agents: weakref.WeakValueDictionary = field(default_factory=weakref.WeakValueDictionary)
    foreground: dict = field(default_factory=dict)
    fallback: object = None


class ReviewScheduler:
    def __init__(self):
        self.owner = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._profiles = {}
        self._active = {}
        self._thread = None
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="memory-review")
        self._closed = False

    def _profile(self, agent):
        from hermes_constants import get_hermes_home, hermes_home_key
        from pathlib import Path

        db = getattr(agent, "_session_db", None)
        home = Path(db.db_path).parent if db is not None else Path(get_hermes_home())
        key = hermes_home_key(home)
        with self._lock:
            if key not in self._profiles:
                self._profiles[key] = _Profile(ReviewJobs(home), contextvars.copy_context())
            profile = self._profiles[key]
            profile.agents[str(agent.session_id)] = agent
            profile.fallback = agent
        return profile

    def _ensure_thread(self):
        with self._lock:
            if self._thread is None and not self._closed:
                self._thread = threading.Thread(target=self._loop, daemon=True, name="memory-review-scheduler")
                self._thread.start()
        self._wake.set()

    def attach(self, agent):
        """Recover an existing profile queue at startup without creating work on fresh installs."""
        from hermes_constants import get_hermes_home
        if (get_hermes_home() / "review_queue.sqlite3").is_file():
            profile = self._profile(agent)
            if profile.jobs.count():
                self._ensure_thread()

    def enqueue(self, agent, messages_snapshot, review_memory=False, review_skills=False, task_cfg=None, **_):
        config = task_cfg or {}
        profile = self._profile(agent)
        job_id = profile.jobs.enqueue(
            str(agent.session_id), runtime_scope(agent, config), messages_snapshot,
            review_memory, review_skills, {"provider": _provider_identity(agent), "model": agent.model,
                "review_provider": config.get("provider", "auto"), "review_model": config.get("model", ""),
                "runtime_owner": self.owner},
            config, scheduling_mode(config))
        self._ensure_thread()
        return job_id

    def turn_started(self, agent):
        if getattr(agent, "_turn_origin", None):
            return None
        profile = self._profile(agent)
        # Foreground competes on the MAIN runtime, even when review uses another model.
        scope = runtime_scope(agent, {})
        with self._lock:
            profile.foreground[scope] = profile.foreground.get(scope, 0) + 1
            profile.jobs.foreground(self.owner, profile.foreground)
            for (key, job_id), (_, run, job_scope, mode) in self._active.items():
                if key == profile.jobs.key and mode != "concurrent" and not run.request_done.is_set():
                    review_agent = run.cancel()
                    if review_agent is not None:
                        from agent.background_review import _interrupt_background_review
                        _interrupt_background_review(review_agent)
                    profile.jobs.event("preempted", job_id)
        self._ensure_thread()
        return profile, scope

    def turn_finished(self, token):
        if token is None:
            return
        profile, scope = token
        with self._lock:
            count = profile.foreground.get(scope, 0) - 1
            if count > 0:
                profile.foreground[scope] = count
            else:
                profile.foreground.pop(scope, None)
            profile.jobs.foreground(self.owner, profile.foreground)
        self._wake.set()

    def _loop(self):
        while not self._closed:
            self._wake.clear()
            try:
                timeout = self.tick()
            except Exception:
                logger.exception("Memory review dispatch failed")
                timeout = 5
            self._wake.wait(timeout)

    def tick(self):
        """Heartbeat active leases; sleep indefinitely when there is no queued/active work."""
        timeout = None
        with self._lock:
            for key, profile in self._profiles.items():
                active = any(k[0] == key for k in self._active)
                if active:
                    profile.jobs.heartbeat(self.owner, [job_id for (home, job_id) in self._active if home == key])
                if profile.foreground:
                    profile.jobs.foreground(self.owner, profile.foreground)
                while len(self._active) < WORKERS:
                    job_id = profile.jobs.claim(self.owner)
                    if job_id is None:
                        break
                    from agent.background_review import _BackgroundReviewRun
                    job = profile.jobs.get(job_id, snapshot=True)
                    run = _BackgroundReviewRun()
                    self._active[key, job_id] = (None, run, job["runtime_scope"], job["mode"])
                    future = self._pool.submit(profile.context.copy().run, self._execute, profile, job, run)
                    self._active[key, job_id] = (future, run, job["runtime_scope"], job["mode"])
                next_at = profile.jobs.next_wake()
                if next_at is not None or active or profile.foreground:
                    delay = min(5, next_at - time.time()) if next_at and next_at > time.time() else 5
                    timeout = delay if timeout is None else min(timeout, delay)
        return timeout

    def _parent(self, profile, job):
        parent = profile.agents.get(job["session_id"])
        route = json.loads(job["route"])
        if (route.get("runtime_owner") == self.owner and parent is not None
                and (_provider_identity(parent), parent.model) == (route.get("provider"), route.get("model"))):
            return copy.copy(parent)
        source = parent or profile.fallback
        if source is None:
            raise RuntimeError("Review recovery requires a live profile runtime")
        # Rehydrate runtime through Hermes, including OAuth refresh and custom endpoints.
        from hermes_cli.runtime_provider import resolve_runtime_provider
        provider = route.get("provider")
        if provider == "custom":
            # Compatibility with jobs written before durable custom identities were stored.
            from hermes_cli.runtime_provider import canonical_custom_identity
            provider = canonical_custom_identity(model=route.get("model")) or provider
        runtime = resolve_runtime_provider(requested=provider, target_model=route.get("model"))
        parent = copy.copy(source)
        parent.provider, parent.model = runtime["provider"], runtime.get("model") or route["model"]
        parent.session_id = job["session_id"]
        parent._cached_system_prompt = None  # old session's cache does not survive a restart
        parent.client = None  # the source session's client may belong to a different provider
        parent.reasoning_config = None
        parent.ephemeral_system_prompt = None
        parent.prefill_messages = []
        parent.acp_command, parent.acp_args = runtime.get("command"), runtime.get("args") or []
        parent._current_main_runtime = lambda: runtime
        parent._credential_pool = runtime.get("credential_pool")
        parent.request_overrides = runtime.get("request_overrides") or {}
        if source.session_id != job["session_id"]:
            # Recovery can borrow another session's runtime, never its reply destination.
            parent.background_review_callback = None
            parent._safe_print = lambda *args: None
            parent._emit_auxiliary_failure = lambda *args: None
        return parent

    def _execute(self, profile, job, run):
        from agent.background_review import load_background_review_settings, spawn_background_review_thread

        started = time.monotonic()
        scopes = ExitStack()
        try:
            from hermes_constants import set_hermes_home_override, reset_hermes_home_override
            from agent.secret_scope import build_profile_secret_scope, set_secret_scope, reset_secret_scope
            from hermes_cli.env_loader import hydrate_profile_secret_sources
            from tools.terminal_scope import install_and_reset_profile_terminal_scope

            home = profile.jobs.home
            scopes.callback(reset_hermes_home_override, set_hermes_home_override(home))
            hydrate_profile_secret_sources(home)
            scopes.callback(reset_secret_scope, set_secret_scope(build_profile_secret_scope(home), profile_home=str(home)))
            scopes.enter_context(install_and_reset_profile_terminal_scope(home))
            enabled, config = load_background_review_settings()
            if not enabled:
                raise ValueError("Automatic reviews disabled in profile configuration")
            route = json.loads(job["route"])
            config = {**config, "provider": route.get("review_provider", "auto"),
                      "model": route.get("review_model", "")}
            parent = self._parent(profile, job)
            if job["mode"] == "idle":
                from agent.review_idle_queue import review_targets_managed_local, _managed_server_idle
                if review_targets_managed_local(parent, config) and not _managed_server_idle():
                    profile.jobs.defer(job["id"], self.owner)
                    return
            # This parent is a shallow, private carrier for the existing review fork. The live
            # parent's cancellation slots/children must not be overwritten by concurrent work.
            parent._background_review_run = None
            parent._background_review_agent = None
            parent._active_children = []
            parent._scheduled_review_job = (profile.jobs, job["id"], self.owner)
            target, _ = spawn_background_review_thread(
                parent, json.loads(job["snapshot"]), review_memory=bool(job["review_memory"]),
                review_skills=bool(job["review_skills"]), task_cfg=config, review_run=run)
            profile.jobs.event("started", job["id"], attempt=job["attempts"])
            target()
            profile.jobs.finish(job["id"], self.owner, error=run.error,
                                transient=run.transient or run.cancel_requested.is_set(),
                                usage=run.usage, duration=time.monotonic() - started)
        except Exception as error:
            profile.jobs.finish(job["id"], self.owner, error=type(error).__name__,
                                transient=retryable_error(error), duration=time.monotonic() - started)
        finally:
            try:
                scopes.close()
            finally:
                with self._lock:
                    self._active.pop((profile.jobs.key, job["id"]), None)
                self._wake.set()

    def close(self):
        self._closed = True
        self._wake.set()
        self._pool.shutdown(wait=True)
        if self._thread:
            self._thread.join(timeout=2)


def retryable_error(error):
    from agent.error_classifier import classify_api_error
    classified = classify_api_error(error)
    return classified.retryable and classified.reason.value != "unknown"


SCHEDULER = ReviewScheduler()
