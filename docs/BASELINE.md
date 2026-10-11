# Hermes baseline

Fork commit: f42f579cf8bac4918ac9599bece71618afadd846 (2026-09-30).

Local tag: baseline-hermes.

Untouched upstream checkout: E:\AI_Project_Sem3\hermes-baseline (relative path ../hermes-baseline).

Verification: git -C ../hermes-baseline log -1 reports f42f579cf8bac4918ac9599bece71618afadd846, matching the fork baseline.

## Pre-cleanup validation

- App startup: hermes chat launched in an isolated temporary home and reached the first-run setup prompt. Setup was declined because no provider/API key is configured; the process exited with status 1.
- Tests: scripts/run_tests.sh exited with status 1 at 75.4% (32,830 of approximately 43,522 cases), after reporting 457 failed cases. The runner produced no final summary. Several 300-second file timeouts and Windows-specific failures were reported, so this is an incomplete baseline and cannot reliably support a before/after regression comparison.

## Pinned baseline metadata

- Date recorded: 2026-10-11.
- Python: 3.12.14 (Codex bundled Python used for the build check).
- OS: Microsoft Windows 10.0.26200.
- Model: TBD.
- Temperature: TBD.
- Baseline test failures: 457. docs/baseline_failures.txt does not exist; the original run details and limitations are recorded above.
