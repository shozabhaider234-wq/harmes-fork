# Prime-inspired learning

Prime-inspired procedural learning is opt-in and profile-local. This implementation
provides the `none` and `prime` learning-provider modes; NVIDIA AVO is not implemented.
The detailed API and store behavior are documented in
[`memd/PRIME_LEARNING.md`](../memd/PRIME_LEARNING.md).

## Runtime behavior

With `learning.provider: prime`, Hermes captures durable foreground turns once with
their task text, final response, execution status, trajectory/tool references, model
routing facts, and the guidance version frozen into that session. Outcomes remain
unknown until explicitly evaluated or labeled. A completed ordinary conversation is
not assumed to be successful.

The candidate generator uses the configured auxiliary model route. Candidate validation
executes the same fixed tasks through Hermes leaf subagents twice: baseline guidance,
then candidate guidance. Objective checks (`contains`, `not_contains`, `equals`) stay
local; they are not sent to the reviewer or task agent. Evidence records the actual
execution provider/model, run state, check outcomes, and response hashes. Activation
requires no regressions and strict objective improvement. Rejected and failed candidates
stay inactive. Accepted artifacts persist by version; later sessions receive their
frozen guidance, and rollback/deactivation preserves the history.

Automatic triggers are opt-in: `disabled`, `manual`, `after_objective`, and
`after_n_experiences`. Only explicit known outcomes qualify. Threshold triggers are
batched, durable trigger claims prevent duplicate jobs, and validation runs are excluded
from experience capture. Candidate generation never activates unvalidated guidance.

## Configuration and commands

```yaml
learning:
  provider: prime
  prime:
    enabled: true
    trigger: manual
    minimum_experiences: 3
    experience_threshold: 5
    scope: global
    validation_tasks: [] # configure fixed tasks with local objective checks before review
```

`none` is the default provider. When Prime is enabled, `manual` is the default trigger.
Use `after_objective` to queue after a known objective result, or
`after_n_experiences` with `experience_threshold` for batches.

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

## Verification status

The E: isolated profile is authenticated with Hermes-owned Codex OAuth and configured for
`openai-codex` / `gpt-6-luna`. Real Hermes task-harness runs persisted objectively evaluated
experiences. Prime called its configured auxiliary reviewer, which resolved to
`openai-codex` / `gpt-6-luna`; it legitimately returned no refinement for the available
experiences. No live candidate was produced, so no real-model baseline/candidate comparison
or live activation is claimed.

Deterministic Prime/task-harness tests cover capture, persistence, deduplication, automatic
trigger claims, candidate validation, activation, rejection, rollback, and error recording.
They establish successful activation and rollback on controlled fixtures, not model
improvement. The updated memd Python suite passed 46 tests with 2 Windows Unix-socket skips;
the 23 Prime tests are included in that count. The native background memory reviewer also
ran with a real model and persisted a synthetic fact retrievable in a fresh Hermes session.

The Prime slash command is `/memd-learn`; Hermes reserves `/learn` for its built-in skill
learning command. `_review` uses the configured auxiliary route and actual Hermes leaf
subagent executions for paired validation whenever the reviewer proposes a candidate.
Because the live reviewer produced no candidate, post-restart live guidance activation and
live rollback remain unverified. See [TEST_REPORT.md](TEST_REPORT.md) for receipts and the
remaining validation limit.

No memory or general benchmarks, efficiency optimization, or AVO learning was performed.
