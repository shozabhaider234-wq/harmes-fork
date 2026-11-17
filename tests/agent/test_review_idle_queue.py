from agent import review_idle_queue

def test_wrapper_cloud_fast_path_skips_runtime_resolution(monkeypatch):
    """No managed server on the machine -> the classifier answers from the
    TTL-cached netloc probe alone, without resolving the review runtime.
    Guards the cloud-only turn tail from growing new work."""
    from agent import review_idle_queue as riq

    resolved = {"count": 0}

    def _explode(agent, cfg):
        resolved["count"] += 1
        raise AssertionError("runtime resolution must not run")

    monkeypatch.setattr(
        "agent.auxiliary_client._managed_local_netloc", lambda: "")
    monkeypatch.setattr(
        "agent.background_review._resolve_review_runtime", _explode)
    assert riq.review_targets_managed_local(object(), {}) is False
    assert resolved["count"] == 0


