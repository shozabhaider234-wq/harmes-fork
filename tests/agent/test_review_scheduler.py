"""Durable review scheduling contracts; no network or paid models."""
import contextvars
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.review_jobs import LEASE_SECONDS, ReviewJobs
from agent.review_scheduler import ReviewScheduler, retryable_error, runtime_scope, scheduling_mode


@pytest.fixture
def jobs(tmp_path):
    clock = [1000.0]
    queue = ReviewJobs(tmp_path, now=lambda: clock[0], jitter=lambda low, high: high)
    queue.clock = clock
    return queue


def enqueue(jobs, *, session="s", config=None, scope="runtime", content="hello"):
    return jobs.enqueue(session, scope, [{"role": "user", "content": content}], True, False,
                        {"provider": "fake", "model": "fake", "api_key": "must-not-persist"},
                        config or {}, scheduling_mode(config or {}))


def test_immutable_snapshot_dedup_and_no_credentials(jobs):
    first = enqueue(jobs)
    assert enqueue(jobs) == first
    assert enqueue(jobs, session="other") != first
    job = jobs.get(first, snapshot=True)
    assert json.loads(job["snapshot"]) == [{"role": "user", "content": "hello"}]
    assert "must-not-persist" not in jobs.path.read_bytes().decode(errors="ignore")
    assert jobs.count() == 2


def test_atomic_claim_and_scope_limit(jobs):
    first = enqueue(jobs)
    second = enqueue(jobs, session="other")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(jobs.claim, ["worker-a", "worker-b"]))
    assert sum(value is not None for value in claims) == 1
    assert next(value for value in claims if value) == first
    assert jobs.get(second)["attempts"] == 0


def test_configured_concurrency_still_serializes_one_session(jobs):
    first = enqueue(jobs, config={"max_concurrency": 2})
    same_session = enqueue(jobs, content="another snapshot", config={"max_concurrency": 2})
    second = enqueue(jobs, session="other", config={"max_concurrency": 2})
    assert jobs.claim("one") == first
    assert jobs.claim("two") == second
    assert jobs.claim("three") is None
    assert jobs.get(same_session)["attempts"] == 0


def test_retry_success_and_exhaustion(jobs):
    first = enqueue(jobs)
    for attempt in range(1, 5):
        assert jobs.claim("worker") == first
        assert jobs.get(first)["attempts"] == attempt
        jobs.finish(first, "worker", error="timeout", transient=True)
        job = jobs.get(first)
        assert job["status"] == ("retry-pending" if attempt < 4 else "permanently-failed")
        assert jobs.claim("worker") is None
        jobs.clock[0] = job["next_at"]
    assert jobs.count() == 0
    second = enqueue(jobs, content="next")
    assert jobs.claim("worker") == second
    jobs.finish(second, "worker", error="timeout", transient=True)
    jobs.clock[0] = jobs.get(second)["next_at"]
    assert jobs.claim("worker") == second
    jobs.finish(second, "worker", usage={"input_tokens": 20})
    assert jobs.get(second)["status"] == "completed"
    assert json.loads(jobs.get(second)["usage"])["input_tokens"] == 20


def test_retry_policy_and_permanent_errors(jobs):
    first = enqueue(jobs, config={"max_retries": 0})
    jobs.claim("worker")
    jobs.finish(first, "worker", error="timeout", transient=True)
    assert jobs.get(first)["status"] == "permanently-failed"
    second = enqueue(jobs, content="second")
    jobs.claim("worker")
    jobs.finish(second, "worker", error="invalid_configuration", transient=False)
    assert jobs.get(second)["status"] == "permanently-failed"
    assert retryable_error(TimeoutError())
    assert retryable_error(ConnectionError())
    assert not retryable_error(ValueError("invalid configuration"))


def test_restart_and_stale_running_recovery(jobs):
    first = enqueue(jobs)
    restarted = ReviewJobs(jobs.home, now=jobs.now, jitter=jobs.jitter)
    assert restarted.claim("old-process") == first
    assert jobs.claim("new-process") is None
    jobs.clock[0] += LEASE_SECONDS + 1
    assert restarted.claim("new-process") is None  # recovered with backoff
    assert jobs.get(first)["status"] == "retry-pending"
    jobs.clock[0] = jobs.get(first)["next_at"]
    assert restarted.claim("new-process") == first
    assert not restarted.owned(first, "old-process")
    restarted.finish(first, "old-process")  # late completion cannot overwrite the new claim
    assert jobs.get(first)["status"] == "running"
    restarted.finish(first, "new-process")
    assert jobs.get(first)["status"] == "completed"


def test_foreground_leases_and_alternative_runtime(jobs):
    first = enqueue(jobs)
    jobs.foreground("foreground", ["runtime"])
    assert jobs.claim("review") is None
    alternative = enqueue(jobs, session="alt", scope="alternative", config={"scheduling_mode": "concurrent"})
    assert jobs.claim("review") == alternative
    jobs.finish(alternative, "review")
    jobs.clock[0] += LEASE_SECONDS + 1
    assert jobs.claim("review") == first  # crashed foreground's lease also recovers


class FakeAgent:
    def __init__(self, provider="cloud", *, base_url="https://api.example.test/v1"):
        self.provider, self.model, self.session_id = provider, "fake/model", "session"
        self._session_db = None
        self.api_key = "secret"
        self.base_url = base_url

    def _current_main_runtime(self):
        return {"provider": self.provider, "model": self.model, "api_key": self.api_key,
                "base_url": self.base_url}


@pytest.fixture
def scheduler(monkeypatch):
    scheduler = ReviewScheduler()
    # Drive real claims and the worker using tick(), with no dispatcher timing races.
    monkeypatch.setattr(scheduler, "_ensure_thread", lambda: None)
    yield scheduler
    scheduler.close()


@pytest.mark.parametrize("provider,url", [
    ("cloud", "https://api.example.test/v1"),
    ("oauth", "https://oauth.example.test/v1"),
    ("custom", "http://127.0.0.1:9000/v1"),
])
def test_foreground_preemption_preserves_job(scheduler, monkeypatch, provider, url):
    import agent.background_review as review
    agent = FakeAgent(provider, base_url=url)
    started, released = threading.Event(), threading.Event()
    runs = []

    def spawn(parent, snapshot, *, review_run, **kwargs):
        def target():
            runs.append(review_run)
            assert review_run.begin_request(parent)
            started.set()
            assert released.wait(5)
            if review_run.cancel_requested.is_set():
                review_run.error, review_run.transient = "preempted", True
        return target, "existing prompt"

    monkeypatch.setattr(review, "spawn_background_review_thread", spawn)
    monkeypatch.setattr(review, "load_background_review_settings", lambda: (True, {}))
    monkeypatch.setattr(review, "_interrupt_background_review", lambda parent: None)
    job_id = scheduler.enqueue(agent, [{"role": "user", "content": "fact"}], review_memory=True)
    scheduler.tick()
    assert started.wait(5)
    token = scheduler.turn_started(agent)  # returns while the review is still blocked
    assert runs[0].cancel_requested.is_set()
    profile = token[0]
    future = scheduler._active[profile.jobs.key, job_id][0]
    released.set()
    future.result(timeout=5)
    assert profile.jobs.get(job_id)["status"] == "retry-pending"
    assert profile.jobs.get(job_id)["attempts"] == 1
    scheduler.turn_finished(token)


def test_explicit_concurrency_and_empty_queue(scheduler, monkeypatch):
    import agent.background_review as review
    agent = FakeAgent()
    started, release = threading.Event(), threading.Event()
    observed = []
    def spawn(parent, snapshot, *, review_run, **kwargs):
        def target():
            observed.append(review_run)
            started.set()
            assert release.wait(5)
        return target, "prompt"
    monkeypatch.setattr(review, "spawn_background_review_thread", spawn)
    monkeypatch.setattr(review, "load_background_review_settings", lambda: (True, {}))
    assert scheduler.tick() is None
    token = scheduler.turn_started(agent)
    job_id = scheduler.enqueue(agent, [], task_cfg={"scheduling_mode": "concurrent"})
    scheduler.tick()
    assert started.wait(5)
    assert not observed[0].cancel_requested.is_set()
    future = scheduler._active[token[0].jobs.key, job_id][0]
    release.set()
    future.result(timeout=5)
    assert token[0].jobs.get(job_id)["status"] == "completed"  # saves nothing
    scheduler.turn_finished(token)
    assert scheduler.tick() is None  # no empty-queue polling


def test_profiles_context_and_sessions_are_isolated(scheduler, tmp_path, monkeypatch):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    marker = contextvars.ContextVar("review_test_profile", default="unbound")
    profiles = []
    for name in ("A", "B", "A"):
        home_token = set_hermes_home_override(tmp_path / name)
        mark_token = marker.set(name)
        try:
            agent = FakeAgent()
            job_id = scheduler.enqueue(agent, [], review_memory=True)
            profiles.append((scheduler._profile(agent), job_id))
        finally:
            marker.reset(mark_token)
            reset_hermes_home_override(home_token)
    assert profiles[0][0] is profiles[2][0]
    assert profiles[0][1] == profiles[2][1]
    assert profiles[0][0].jobs.path != profiles[1][0].jobs.path
    assert profiles[0][0].context.run(marker.get) == "A"
    assert profiles[1][0].context.run(marker.get) == "B"


def test_restart_rehydrates_provider_through_existing_resolver(scheduler, monkeypatch):
    import agent.background_review as review
    import hermes_cli.runtime_provider as providers
    old = FakeAgent("oauth")
    old.session_id = "old-session"
    job_id = scheduler.enqueue(old, [{"role": "user", "content": "durable"}], review_memory=True)
    profile = scheduler._profile(old)
    profile.agents.clear()
    current = FakeAgent("custom")
    current.session_id = "new-session"
    current.background_review_callback = lambda text: None
    current.ephemeral_system_prompt = "private new-session instructions"
    current.prefill_messages = [{"role": "assistant", "content": "private prefill"}]
    profile.fallback = current
    resolved = []
    def resolve(**kwargs):
        resolved.append(kwargs)
        return {"provider": "oauth", "model": "fake/model", "api_key": "refreshed-token"}
    monkeypatch.setattr(providers, "resolve_runtime_provider", resolve)
    parent = scheduler._parent(profile, profile.jobs.get(job_id, snapshot=True))
    assert parent.session_id == "old-session"
    assert parent._current_main_runtime()["api_key"] == "refreshed-token"
    assert resolved == [{"requested": "oauth", "target_model": "fake/model"}]
    assert current.session_id == "new-session"
    assert parent.background_review_callback is None
    assert callable(current.background_review_callback)
    assert parent.ephemeral_system_prompt is None and parent.prefill_messages == []
    assert current.ephemeral_system_prompt == "private new-session instructions"


def test_worker_transient_failure_then_success(scheduler, monkeypatch):
    import agent.background_review as review
    agent = FakeAgent()
    count = []
    def spawn(parent, snapshot, **kwargs):
        def target():
            count.append(snapshot)
            if len(count) == 1:
                raise TimeoutError("temporary transport failure")
        return target, "unchanged prompt"
    monkeypatch.setattr(review, "spawn_background_review_thread", spawn)
    monkeypatch.setattr(review, "load_background_review_settings", lambda: (True, {}))
    job_id = scheduler.enqueue(agent, [{"role": "user", "content": "immutable"}])
    profile = scheduler._profile(agent)
    now = [1000.0]
    profile.jobs.now = lambda: now[0]
    # Enqueue uses wall clock; advance the deterministic clock to the persisted deadline.
    now[0] = profile.jobs.get(job_id)["next_at"]
    from agent.background_review import _BackgroundReviewRun
    for attempt in range(2):
        assert profile.jobs.claim(scheduler.owner) == job_id
        scheduler._execute(profile, profile.jobs.get(job_id, snapshot=True), _BackgroundReviewRun())
        now[0] = profile.jobs.get(job_id)["next_at"]
    assert profile.jobs.get(job_id)["status"] == "completed"
    assert profile.jobs.get(job_id)["attempts"] == 2
    assert count[0] == count[1]


def test_broken_runtime_is_persisted_and_fails_visibly(scheduler, monkeypatch):
    import agent.background_review as review
    agent = FakeAgent()
    def broken_runtime():
        raise ValueError("invalid provider configuration")
    agent._current_main_runtime = broken_runtime
    job_id = scheduler.enqueue(agent, [])
    profile = scheduler._profile(agent)
    assert profile.jobs.get(job_id)["status"] == "pending"
    monkeypatch.setattr(review, "load_background_review_settings", lambda: (True, {}))
    def spawn(parent, *args, **kwargs):
        parent._current_main_runtime()
    monkeypatch.setattr(review, "spawn_background_review_thread", spawn)
    assert profile.jobs.claim(scheduler.owner) == job_id
    scheduler._execute(profile, profile.jobs.get(job_id, snapshot=True), review._BackgroundReviewRun())
    assert profile.jobs.get(job_id)["status"] == "permanently-failed"
    assert profile.jobs.get(job_id)["error"] == "ValueError"


@pytest.mark.parametrize("result,error,retry", [
    ({"failed": True, "failure_reason": "rate_limit", "failure_retryable": True}, "rate_limit", True),
    ({"failed": True, "failure_reason": "auth", "failure_retryable": False}, "auth", False),
    ({"completed": True, "final_response": "Nothing worth saving"}, None, False),
])
def test_existing_reviewer_reports_outcome(monkeypatch, result, error, retry):
    import agent.background_review as review
    agent = FakeAgent()
    agent.memory_notifications = "on"
    monkeypatch.setattr(review, "_parent_can_emit_tool_calls", lambda agent: True)
    monkeypatch.setattr(review, "_set_thread_approval_callback", lambda callback: None)
    def fork(parent, snapshot, prompt, config, run, state, *args):
        state.result = result
        state.review_usage = {"input_tokens": 42}
    monkeypatch.setattr(review, "_run_review_fork", fork)
    run = review._BackgroundReviewRun()
    review._run_review_in_thread(agent, [], "existing prompt", review_run=run)
    assert run.error == error
    assert run.transient == retry
    assert run.usage["input_tokens"] == 42


def test_runtime_scope_does_not_depend_on_authentication():
    agent = FakeAgent()
    original = runtime_scope(agent, {})
    agent.api_key = "refreshed OAuth or API credential"
    assert runtime_scope(agent, {}) == original
    assert scheduling_mode({}) == "idle"
    assert scheduling_mode({"scheduling_mode": "concurrent"}) == "concurrent"


def test_profile_secrets_and_config_follow_jobs_under_multiplex(scheduler, tmp_path, monkeypatch):
    from agent import secret_scope as secrets
    from hermes_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override
    import agent.background_review as review
    seen = []
    def spawn(parent, snapshot, **kwargs):
        def target():
            seen.append((get_hermes_home().name, secrets.get_secret("REVIEW_TEST_KEY")))
        return target, "prompt"
    monkeypatch.setattr(review, "spawn_background_review_thread", spawn)
    secrets.set_multiplex_active(True)
    try:
        for name in ("A", "B", "A"):
            home = tmp_path / name
            home.mkdir(exist_ok=True)
            (home / "config.yaml").write_text("auxiliary:\n  background_review:\n    enabled: true\n")
            (home / ".env").write_text(f"REVIEW_TEST_KEY={name}\n")
            home_token = set_hermes_home_override(home)
            secret_token = secrets.set_secret_scope({"REVIEW_TEST_KEY": name}, profile_home=str(home))
            try:
                agent = FakeAgent()
                job_id = scheduler.enqueue(agent, [{"role": "user", "content": str(len(seen))}])
                profile = scheduler._profile(agent)
                assert profile.jobs.claim(scheduler.owner) == job_id
            finally:
                secrets.reset_secret_scope(secret_token)
                reset_hermes_home_override(home_token)
            from agent.background_review import _BackgroundReviewRun
            profile.context.copy().run(scheduler._execute, profile,
                                       profile.jobs.get(job_id, snapshot=True), _BackgroundReviewRun())
            assert profile.jobs.get(job_id)["status"] == "completed"
    finally:
        secrets.set_multiplex_active(False)
    assert seen == [("A", "A"), ("B", "B"), ("A", "A")]


@pytest.mark.parametrize("alternative", [False, True])
def test_restart_uses_durable_profile_credentials_not_resumed_session(tmp_path, monkeypatch, alternative):
    """Real config, named-provider resolver and secret scopes across A -> B -> A."""
    from agent import background_review as review, secret_scope as secrets
    from hermes_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override

    original, restarted = ReviewScheduler(), ReviewScheduler()
    for scheduler in (original, restarted):
        monkeypatch.setattr(scheduler, "_ensure_thread", lambda: None)
    captured = []
    expected = []
    def spawn(parent, snapshot, *, task_cfg, **kwargs):
        def target():
            runtime = review._resolve_review_runtime(parent, task_cfg)
            captured.append((get_hermes_home().name, runtime["api_key"], runtime["base_url"],
                             runtime["model"], runtime["routed"]))
        return target, "unchanged review prompt"
    monkeypatch.setattr(review, "spawn_background_review_thread", spawn)
    secrets.set_multiplex_active(True)
    try:
        for index, name in enumerate(("A", "B", "A")):
            home = tmp_path / name
            home.mkdir(exist_ok=True)
            endpoint = f"http://127.0.0.1:{9100 + ord(name)}/v1"
            review_endpoint = endpoint.replace("/v1", "/review/v1")
            config = {
                "model": {"provider": "custom:main", "default": "fake/model"},
                "custom_providers": [
                    {"name": "main", "base_url": endpoint, "key_env": "MAIN_REVIEW_KEY", "model": "fake/model"},
                    {"name": "reviewer", "base_url": review_endpoint, "key_env": "AUX_REVIEW_KEY", "model": "other/model"},
                ],
            }
            (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
            (home / ".env").write_text("MAIN_REVIEW_KEY=old-key\nAUX_REVIEW_KEY=old-aux-key\n")
            home_token = set_hermes_home_override(home)
            secret_token = secrets.set_secret_scope({"MAIN_REVIEW_KEY": "captured-stale-key"}, profile_home=str(home))
            try:
                agent = FakeAgent("custom", base_url=endpoint)
                agent.requested_provider = "custom:main"
                messages = [{"role": "user", "content": f"immutable snapshot {index}"}]
                task_config = {"provider": "custom:reviewer", "model": "other/model"} if alternative else {}
                job_id = original.enqueue(agent, messages, task_cfg=task_config)
                # A resumed foreground agent has the same session ID/model but unrelated,
                # session-only credentials and endpoint. Recovery must not inherit them.
                resumed = FakeAgent("custom", base_url="https://wrong.example.test/v1")
                resumed.api_key = "unrelated-session-token"
                profile = restarted._profile(resumed)
                token = f"rotated-{name}-{index}"
                (home / ".env").write_text(f"MAIN_REVIEW_KEY={token}\nAUX_REVIEW_KEY=aux-{token}\n")
                assert profile.jobs.claim(restarted.owner) == job_id
                job = profile.jobs.get(job_id, snapshot=True)
                assert json.loads(job["route"])["provider"] == "custom:main"
            finally:
                secrets.reset_secret_scope(secret_token)
                reset_hermes_home_override(home_token)
            profile.context.copy().run(restarted._execute, profile, job, review._BackgroundReviewRun())
            assert profile.jobs.get(job_id)["status"] == "completed"
            expected.append((name, f"aux-{token}" if alternative else token,
                             review_endpoint if alternative else endpoint,
                             "other/model" if alternative else "fake/model", alternative))
            contents = profile.jobs.path.read_bytes()
            for credential in (token, "captured-stale-key", "unrelated-session-token", "old-key", "old-aux-key"):
                assert credential.encode() not in contents
        assert captured == expected
    finally:
        secrets.set_multiplex_active(False)
        original.close()
        restarted.close()


def test_job_deduplication_ignores_runtime_process_identity(jobs):
    args = ("session", "scope", [], True, False)
    first = jobs.enqueue(*args, {"provider": "custom:main", "model": "model", "runtime_owner": "before-restart"}, {}, "idle")
    second = jobs.enqueue(*args, {"provider": "custom:main", "model": "model", "runtime_owner": "after-restart"}, {}, "idle")
    assert first == second
    assert json.loads(jobs.get(second, snapshot=True)["route"])["runtime_owner"] == "before-restart"
