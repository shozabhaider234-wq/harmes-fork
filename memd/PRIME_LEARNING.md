# Prime-inspired procedural learning

This is a small architectural adaptation, not a claim to reproduce Prime Agent.
Ordinary memories and transcripts remain in their existing stores. Learning data is
written separately to `<HERMES_HOME>/learning/prime.json` for the active profile.

## Runtime configuration

Learning is disabled by default. Set `learning.provider: prime` to enable turn capture,
manual labeling, review, and guidance injection. Trigger modes are `disabled`, `manual`,
`after_objective`, and `after_n_experiences`; automatic modes require explicit known labels,
and persistent trigger claims prevent duplicate jobs. `minimum_experiences` controls the
number of labeled experiences needed before review. `experience_threshold` controls the
after-N trigger. The normal memory-provider configuration must also load the memd plugin.

Hermes records a durable primary turn once using stable session/turn identifiers. Each
experience includes task text, final response, completion status, API call count, and
trajectory/tool references. Outcomes remain unknown until explicitly labeled with
`/memd-learn label <experience-id> success|failure|unknown`.

`/memd-learn list` shows recent experience IDs and labels. `/memd-learn review` generates a procedural
candidate through the host-managed auxiliary LLM route, then runs configured validation tasks
as actual Hermes leaf subagents, first with existing guidance and then with candidate guidance.
Objective checks are evaluated locally and are not included in task or reviewer input. Both
runs use the current session's resolved provider/model and inherited bounded tool access. The
learner accepts only when no task regresses and at least one objective check improves. Run
evidence stores provider/model, execution state, check results, and output hashes. Failed or
rejected candidates do not become active. `/memd-learn status` reports the
current active version, and `/memd-learn rollback <version|off>` changes activation without
deleting history.

Accepted guidance is rendered through Hermes's system-prompt plugin section and frozen
for each session. The recorded session-to-artifact mapping makes later trajectory capture
identify the version that the session actually received. New guidance applies to later
sessions, preserving prompt stability and caching.

## Provider API

`memd.clients.python.prime_learning` exports a provider boundary with `none` and `prime`
modes. It rejects unknown providers; AVO is not implemented. `PrimeLearner.observe()` is
idempotent for identical experience IDs and rejects conflicting reuse. Unknown outcomes
are stored as `null`; a natural-language final response alone is never treated as success.

`TaskHarness` remains available for controlled executor integrations. It passes accepted
guidance to an execution callback and records objective results plus trajectory, tool,
memory, model, and applied-artifact references without copying large transcripts.

`PrimeLearner.learn(scope, candidate_generator, fixtures, evaluator)` provides the same
candidate lifecycle to other executors. The Hermes integration executes both variants and
applies the fixture's local `contains`, `not_contains`, and `equals` checks. The store accepts
only when every task is non-regressing and the mean strictly improves. Evaluation occurs
outside the store lock; activation checks that the
baseline version did not change during evaluation. JSON writes use atomic replacement and
a cross-process lock. Rollback reactivates a prior version or disables guidance without
deleting history.

## Functional checks

From the repository root on the prepared Windows environment:

```powershell
rtk proxy memd/.tmp-build/python-tests/Scripts/python.exe -m unittest memd.tests.test_prime_learning -v
```

Fixtures cover experience deduplication, unknown outcomes, controlled acceptance and
rejection, one-shot automatic triggers, failure handling, persistence, rollback, provider
selection, concurrent writes, turn-hook capture, and frozen guidance. The deterministic
Hermes execution test uses a fake lifecycle service; it verifies baseline and candidate calls
at the Hermes execution boundary but is not a real-model validation receipt. These checks do
not measure memory retrieval or storage performance.
