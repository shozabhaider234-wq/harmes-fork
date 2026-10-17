# B2 Decision Log

**Project:** Shared-Memory, Self-Evaluating Agent Harness  
**Team:** B2  
**Owner:** Muhammad Ali Arshad  
**Last updated:** 2026-10-17

## D1 — Artifact Selection

**Status:** Decided

**Decision:** B2 will evolve a retrieval/extraction policy configuration rather than skill files.

**Artifact location:** `evolve/artifacts/policy-vN.yaml`

**Validation schema:** `interfaces/memory_policy.v1.schema.json`

**Reason:** Policy configuration allows B2 to experiment with retrieval and extraction behavior and evaluate the trade-off between token usage and memory recall.

**Dependencies:** Team A must confirm the supported policy parameters, their types, valid ranges, defaults, and how the memory service loads or reloads the configuration.

## D2 — Benchmark and Scoring Contract

**Status:** Mostly agreed; remaining details require confirmation.

**B1 responsibilities:** Define the benchmark tasks, runner, and scoring contract.

**Confirmed details from B1:**
- The `outcome` field uses the values `passed`, `failed`, `error`, and `timeout`.
- The benchmark runner supports `--out` to specify an output path.
- The two baselines are unmodified Hermes and `policy-v0.yaml`.
- B1 is adding memory-related benchmark tasks.

**B2 responsibilities:** Implement a compatible score-report representation, create a mock scorer and tests, and evaluate candidate policies against the agreed benchmark contract.

**Still to clarify:** Required fields, the exact `failure_reason` rule, crash and timeout handling, token-counting semantics, JSONL output details, fixture format, and aggregate pass-rate reporting. Update this section as B1 confirms each detail.

## D3 — Team A Memory-Service Contract

**Status:** Awaiting confirmation.

**Action:** Questions have been sent to Team A regarding the memory-policy interface.

**Questions to resolve:**
- Which policy parameters are supported, and what are their types, valid ranges, and defaults?
- Which policy changes are safe to vary independently?
- How is the policy configuration located, loaded, and reloaded?
- What happens when a configuration is invalid?
- How are empty memory and persistence handled?
- Is a stub or mock memory service available, and what does it support?
- Do the benchmark tasks exercise the memory service?

**Date sent:** [17-10-26.]

Record Team A's answers here as they arrive.

## D4 — Lineage Storage

**Status:** Open.

**Decision required:** Choose JSON Lines (JSONL) or SQLite for storing candidate-policy lineage and evaluation results.

**Evaluation criteria:** Simplicity, traceability, reproducibility, query requirements, and ease of testing.

Record the selected option and rationale after the team agrees.

## D5 — Git and Repository Hygiene

**Status:** Working rule.

- Work on the assigned B2 feature branch.
- Keep changes focused and reviewable.
- Do not commit virtual environments, benchmark run outputs, or other generated artifacts.
- Stage only the files intended for each commit.
- Coordinate shared interface changes with Team A and B1.

## D6 — Acceptance Rules

**Status:** Pending final contract confirmation.

A candidate policy should be evaluated using the agreed benchmark and scoring contract. Acceptance criteria must account for task pass rates and the regression guard, with thresholds finalized against the team's agreed baseline and requirements.

## D7 — Stopping Rule

**Status:** Open.

Define when policy experimentation should stop, based on the agreed evaluation budget, improvement criteria, and regression requirements. Avoid claiming a policy is better without reproducible benchmark evidence.

## D8 — Held-Out Evaluation

**Status:** Working principle.

Keep held-out tasks separate from policy development and tuning. Use the held-out split for evaluation rather than repeatedly tuning policies against it.

## D9 — Repository Layout

**Status:** Agreed working layout.

B2 work is organized under the repository root, including:

- `evolve/` — policy evolution and scoring support.
- `evolve/artifacts/` — versioned policy artifacts.
- `interfaces/` — shared schemas and contracts.
- `tests/evolve/` — B2 tests.
- `tests/evolve/fixtures/` — mock benchmark fixtures.
- `docs/` — decisions and research findings.

Confirm any shared directory or interface changes with the relevant teammate.

## D10 — Tests

**Status:** Initial test setup complete.

The initial smoke and packaging tests have been reported as passing.

Further tests are needed for the score-report contract, mock scorer, policy validation, lineage recording, and regression guard as these components are implemented.

## Verification Checklist

**Status: Not yet checked.**

- [ ] Confirm the repository branch and working-tree state.
- [ ] Verify the policy artifact and schema paths.
- [ ] Confirm the benchmark contract with B1.
- [ ] Confirm memory-service integration details with Team A.
- [ ] Verify the required tests and record their results.
- [ ] Review the held-out evaluation procedure.
