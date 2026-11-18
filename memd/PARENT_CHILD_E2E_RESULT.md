# Live verification result — November 17, 2026

The user's normal Windows PowerShell execution verified the complete live
parent–child flow through the supported finite-session inline delegation path.
The six model stages passed. The overall harness subsequently exited with a
cleanup exception; this is not a clean overall harness exit. Background
notification delivery and a subsequent parent turn remain unverified.

| Stage | Result |
|---|---|
| Parent launched | Pass |
| Delegation invoked | Pass |
| Child memory recall | Pass |
| Child completed | Pass |
| Result delivered to parent | Pass |
| Parent used retrieved memory | Pass |

## Evidence

Evidence directory:
`memd/.tmp-parent-child-e2e/05296a1513c344bf896e804e8eaf3179/`.
The saved `events.jsonl` was inspected directly after the user supplied console output.

- Provider/model, both agents: `openai-codex` / `gpt-6-luna`; wire mode `codex_responses`.
- Parent session: `20261117_180227_3bcd79`.
- Child session: `20261117_180233_4d2de5`.
- Delegation: `deleg_6e38ef63`, task index `0`.
- Parent tool call: `call_fa938ed4aa494cec927cb9218919f1d7`, `delegate_task`.
- Child tool call: `call_e19a713dce6e4a279f13578b289f37c1`,
  `memd_memory({"action":"recall","query":"receipt lookup 05296a1513c3"})`.
- Synthetic record: `mem_b7db7ca7d1724c90b0317ce744eedbec`.
- Child retrieval: retrieved `1`, selected `1`, context `124` tokens, budget `1000`.
- Budget basis: `usage_anchored_live_estimate`; token counter
  `hermes_rough_estimate_padded` (an estimate, not an exact model tokenizer count).
- Child completion: `completed: true`, two API calls, `4.16` seconds.
- Parent tool history contains a real `delegate_task` result with
  `status: completed` and the child's exact receipt in `summary`.

Parent final answer:

> “MEMD_PARENT_CHILD_E2E_05296a1513c344bf896e804e8eaf3179” — this receipt came from the delegated memory recall.

The exact receipt was absent from both initial task prompts and the parent's
system prefix. The parent's only recorded provider recall was the harness's
out-of-conversation access preflight, not a model-issued memory tool call.
The child exposed only `memd_memory`; its provider actions were `recall`,
`source`, and `history`. The primary retained `remember`. Both had one memory
provider tool registration.

## Delivery mode

The delegation result explicitly reports the finite-session synchronous fallback:
the completed result was included in the original tool return. Parent use was
verified in that same turn. No second parent turn or asynchronous notification
was observed, and neither is claimed. This run does not reproduce or diagnose a
long-lived session's possible background result-delivery issue.

## Cleanup and changes

The worker exited `0`. The outer harness deleted and verified both synthetic
namespace targets before reaching its temporary-directory removal. That removal
failed with `WinError 32` on the disposable profile's `state.db`.

The harness's SQLite backup used `with sqlite3.connect(...)`, which ends the
transaction but leaves the connection open. Both backup connections now use
`contextlib.closing`. Cleanup events are also written when directory removal
fails. This fix has not been rerun in the sandbox, whose Python process creation
remains blocked.

The leftover directory from this run is
`C:\Users\PMYLS\AppData\Local\Temp\hermes-memd-e2e-m_3lnrau`.
It can be removed after the outer Python process has exited. The foreground
test daemon must be stopped separately in its own terminal; the harness did not
start or stop the normal daemon.

Task additions/changes are limited to `memd/scripts/verify_parent_child_e2e.py`,
`memd/PARENT_CHILD_E2E.md`, and this result document. Existing production changes,
Team A/Team B systems, credentials, and benchmark artifacts were preserved.

## Tests and limitations

- User-executed receipt-checker self-check: passed. This validates checker logic
  using synthetic data; it is not a live model test.
- User-executed live verification: six stage checks passed, launcher exit `0`;
  outer harness cleanup failed.
- New focused/lifecycle regression suite execution: `0` passed, `0` failed,
  `0` skipped; not run. The original 17 focused passes are historical.
- Credential refresh was disabled. Existing Codex OAuth login was used; no
  authentication tokens appear in the collected report.
- Nested privilege escalation and concurrent retrieval were not re-exercised by
  this single-child run; their prior focused-test results remain historical.
