# Hermes baseline

Fork commit: 42f579cf8bac4918ac9599bece71618afadd846 (2026-09-30).

Local tag: aseline-hermes.

Untouched upstream checkout: ../hermes-baseline, cloned from https://github.com/NousResearch/hermes-agent.git at the same commit.

## Pre-cleanup validation

- App startup: hermes chat launched in an isolated temporary home and reached the first-run setup prompt. Setup was declined because no provider/API key is configured; the process exited with status 1.
- Tests: scripts/run_tests.sh exited with status 1 at 75.4% (32,830 of approximately 43,522 cases), after reporting 457 failed cases. The runner produced no final summary. Several 300-second file timeouts and Windows-specific failures were reported, so this is an incomplete baseline and cannot reliably support a before/after regression comparison.
