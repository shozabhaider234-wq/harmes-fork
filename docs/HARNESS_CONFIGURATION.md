# Harness configuration status

## Rust memory

The Hermes memory provider is selected through the normal Hermes `memory.provider`
configuration after installing the local plugin. Daemon endpoint, namespace, store path,
model/runtime paths, and retrieval settings are profile-local in `<HERMES_HOME>/memd.json`.
Follow the setup and rollback steps in [memd/INTEGRATION.md](../memd/INTEGRATION.md); do not
point the daemon at Hermes `state.db`.

## Learning component

Learning is opt-in and profile-local. Keep the default `none` to leave capture and guidance
disabled. To enable Prime-inspired capture, label experiences, and manual review:

```yaml
learning:
  provider: prime
  prime:
    enabled: true
    trigger: manual
    minimum_experiences: 3
    experience_threshold: 5
    scope: global
    validation_tasks:
      - task_id: phrase-check
        instructions: Return the phrase "HARMES_VALIDATION_OK" exactly.
        checks:
          - type: contains
            value: HARMES_VALIDATION_OK
```

Prime writes to `<HERMES_HOME>/learning/prime.json`; no credentials are stored there.
Completed durable primary Hermes turns are captured with their task/trajectory/tool
references, final response, execution status, and guidance version. Outcomes remain unknown
until a user explicitly labels them with `/memd-learn label <experience-id> success|failure`.
`/memd-learn review` generates candidates from explicitly labeled experiences. It executes each
configured validation task through an isolated Hermes child using the current session's model,
provider, and bounded tool permissions, once without candidate guidance and once with it.
Objective checks stay local and are not sent to the child or reviewer. Only no-regression,
strict-improvement candidates activate; run evidence records resolved provider/model, execution
state, checks, and response hashes. `/memd-learn list`, `/memd-learn decisions`, `/memd-learn status`,
`/memd-learn guidance`, `/memd-learn rollback <version|off>`, and `/memd-learn deactivate` inspect and
control active guidance. Prompt sections are frozen when a session starts and remain
byte-stable throughout that session; newly accepted guidance applies to later sessions.

`trigger` supports `disabled`, `manual`, `after_objective`, and `after_n_experiences`.
Automatic modes queue one review per persisted trigger claim and require explicit known outcomes;
ordinary conversation completion and `unknown` outcomes never trigger learning. `after_n_experiences`
uses `experience_threshold`. Automatic learning remains off unless a mode is configured.

Operational commands:

```text
hermes chat -q "Run a normal agent task"
hermes chat -q "Use the configured Rust memory provider for this task"
/memd-learn status
/memd-learn list
/memd-learn label <experience-id> success|failure|unknown
/memd-learn review
/memd-learn decisions
/memd-learn guidance
/memd-learn rollback <version|off>
/memd-learn deactivate
```

For a threshold-triggered review, use `after_n_experiences` and set
`experience_threshold`. `after_objective` queues a review after an explicit known outcome.
Unmarked conversations do not become successful experiences. Review requires fixed
`validation_tasks` with local objective checks; expected values are used only by the local
checker and are withheld from both the reviewer and validation agent.

To use the current Hermes profile and auth, initialize the provider through the normal
Hermes/plugin runtime and construct the integrated harness from its plugin context:

```python
from memd.clients.python.task_harness import TaskHarness

harness = TaskHarness.from_plugin_context(ctx)
record = harness.run_hermes(
    {"task_id": "controlled-task", "instructions": "Inspect the task input.", "scope": "global"},
    agent_kwargs={"provider": "openai-codex", "model": "gpt-6-luna"},
    evaluate=objective_evaluator,
)
```

The `run_hermes` path creates a fresh `AIAgent`, freezes active guidance at construction,
records an experience even when objective outcome is unknown, and suppresses background
review for that harness-owned task. The earlier lower-level `TaskHarness(provider)` callback
form remains for controlled executor integrations; it does not by itself bind automatic
review routing.

## Working E: runtime commands

The isolated E: profile is authenticated with Hermes-owned Codex OAuth and configured for
`openai-codex` / `gpt-6-luna`. These process-local settings select its E: runtime and source
checkout:

```powershell
$runtime = 'E:\AI_Project_Sem3\harmes-fork\memd\.tmp-build\harness-runtime-e'
$env:HERMES_HOME = Join-Path $runtime 'profiles\harmes-harness'
$env:HERMES_RUNTIME_DIR = Join-Path $runtime 'tools'
$env:TEMP = Join-Path $runtime 'temp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $runtime 'uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'uv-python'
$env:PIP_CACHE_DIR = Join-Path $runtime 'pip-cache'
$env:PYTHONPATH = 'E:\AI_Project_Sem3\harmes-fork'
$hermes = Join-Path $runtime 'installs\da256e215b88f355\environments\8a58c307afe149acaea69cedc75d5437\venv\Scripts\hermes.exe'
& $hermes -p harmes-harness auth status openai-codex
& $hermes --help
& $hermes --version
& $hermes -p harmes-harness -m gpt-6-luna -z 'Reply with exactly HARMES_LIVE_OK.'
```

The last command is a one-shot real Hermes task. `memory.provider: hermes-memd` enables Rust
memory for ordinary sessions; use a new session to verify cross-session recall. Prime tasks
use the same runtime command after `learning.provider: prime` is configured. Do not paste or
copy OAuth token material between profiles.

For manual review and inspection, start an interactive Hermes session:

```powershell
& $hermes -p harmes-harness -m gpt-6-luna
```

Then use these at the Hermes prompt:

```text
/memd-learn status
/memd-learn list
/memd-learn label <experience-id> success|failure|unknown
/memd-learn review
/memd-learn decisions
/memd-learn guidance
/memd-learn rollback <version|off>
/memd-learn deactivate
```

`/memd-learn` avoids a name collision: `/learn` is Hermes's built-in skill-learning
command. Automatic review remains off with `trigger: manual`. To enable an explicit known
outcome trigger or threshold batch:

```powershell
& $hermes -p harmes-harness config set learning.provider prime
& $hermes -p harmes-harness config set learning.prime.enabled true
& $hermes -p harmes-harness config set learning.prime.trigger after_objective
# Or use after_n_experiences and set its threshold:
& $hermes -p harmes-harness config set learning.prime.trigger after_n_experiences
& $hermes -p harmes-harness config set learning.prime.experience_threshold 5
& $hermes -p harmes-harness config get learning --json
```

The provider-neutral `TaskHarness` API remains available for controlled executor integrations:

```python
from memd.clients.python.prime_learning import create_learning_provider
from memd.clients.python.task_harness import TaskHarness

provider = create_learning_provider("prime", hermes_home / "learning" / "prime.json")
harness = TaskHarness(provider)

# Execute a fresh real Hermes AIAgent instance using normal profile config/auth.
record = harness.run_hermes(
    {"task_id": "controlled-task", "instructions": "Inspect the task input.", "scope": "global"},
    agent_kwargs={"provider": "openai-codex", "model": "gpt-6-luna"},
    evaluate=lambda _task, result: "expected phrase" in result["final_response"],
)
```

Unknown provider names and malformed settings are reported by normal Hermes config
validation. Manual review makes an auxiliary model call using the configured host auth.
Automatic triggers are opt-in. See [PRIME_LEARNING.md](../memd/PRIME_LEARNING.md).
