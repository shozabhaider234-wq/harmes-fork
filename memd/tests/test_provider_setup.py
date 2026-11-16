"""Provider setup installs the local runtime and selects safe defaults."""
import json
from pathlib import Path

import pytest

from memd.clients.python.hermes_bridge import MemdMemoryProvider


@pytest.mark.platforms("windows")
def test_setup_installs_runtime_and_uses_defaults(tmp_path, monkeypatch):
    source = tmp_path / "checkout" / "memd"
    (source / "target-gnu" / "release").mkdir(parents=True)
    (source / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (source / "target-gnu" / "release" / "memd.exe").write_bytes(b"daemon")
    model = source / "models" / "all-MiniLM-L6-v2"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    runtime = source / "onnxruntime" / "onnxruntime-win-x64-1.31.1" / "lib"
    runtime.mkdir(parents=True)
    (runtime / "onnxruntime.dll").write_bytes(b"runtime")
    monkeypatch.chdir(source.parent)

    home = tmp_path / "profile"
    home.mkdir()
    (home / "memd.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"endpoint": "pipe:custom", "namespace": "existing", "top_k": 5,
                                      "store_path": str(tmp_path / "existing.db")}).encode()
    )
    config = {"memory": {"nudge_interval": 2}}
    saved = {}
    import hermes_cli.config as hermes_config
    monkeypatch.setattr(hermes_config, "save_config", lambda value: saved.update(value))
    monkeypatch.setattr(MemdMemoryProvider, "_build_daemon", staticmethod(lambda _source: source / "target-gnu" / "release" / "memd.exe"))
    MemdMemoryProvider().post_setup(home, config)

    stored = json.loads((home / "memd.json").read_text(encoding="utf-8"))
    assert stored["endpoint"] == "pipe:custom"
    assert stored["namespace"] == "existing"
    assert stored["top_k"] == 5
    assert stored["store_path"] == str(tmp_path / "existing.db")
    assert not (home / "memd.json").read_bytes().startswith(b"\xef\xbb\xbf")
    assert Path(stored["daemon_path"]).read_bytes() == b"daemon"
    assert Path(stored["model_dir"], "config.json").exists()
    assert Path(stored["ort_dylib"]).read_bytes() == b"runtime"
    assert config["memory"]["provider"] == "hermes-memd"
    assert config["memory"]["nudge_interval"] == 2
    assert saved["memory"]["provider"] == "hermes-memd"


@pytest.mark.platforms("windows")
def test_provider_starts_installed_daemon_when_pipe_is_down(tmp_path, monkeypatch):
    import memd.clients.python.hermes_bridge as bridge

    home = tmp_path / "profile"
    binary = home / "memd" / "memd.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"daemon")
    model = home / "memd" / "model"
    model.mkdir()
    calls = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def ping(self):
            if calls.count("offline") < 2:
                calls.append("offline")
                raise OSError("pipe is not listening")
            calls.append("online")
            return {"status": "ok"}

    monkeypatch.setattr(bridge, "MemdClient", Client)
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    config = {
        "endpoint": "pipe:hermes-memd",
        "daemon_path": str(binary),
        "model_dir": str(model),
        "store_path": str(home / "memd" / "memories.db"),
        "ort_dylib": str(home / "memd" / "lib" / "onnxruntime.dll"),
    }

    MemdMemoryProvider()._ensure_daemon(home, config)

    assert calls[0] == "offline"
    launch = next(call for call in calls if isinstance(call, tuple))
    assert launch[0][:3] == [str(binary), "--pipe", "hermes-memd"]
    assert calls[-1] == "online"
