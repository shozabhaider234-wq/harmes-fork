# Live parent/child memory receipt

Run from a normal Windows PowerShell terminal. The Codex sandbox denied execution
of `.memd-test-venv/Scripts/python.exe`; this procedure has **not** been executed
in that sandbox. No live success or regression-test result is claimed.

```powershell
Set-Location 'E:\AI_Project_Sem3\harmes-fork'
$testPython = 'E:\AI_Project_Sem3\harmes-fork\.memd-test-venv\Scripts\python.exe'
& $testPython memd/scripts/verify_parent_child_e2e.py --self-check
if ($LASTEXITCODE -ne 0) { throw 'Receipt checker self-check failed' }
& $testPython memd/scripts/verify_parent_child_e2e.py
if ($LASTEXITCODE -ne 0) { throw 'Live receipt failed; inspect the printed evidence directory' }
```

If the existing authenticated Hermes profile is elsewhere, supply its directory:

```powershell
& $testPython memd/scripts/verify_parent_child_e2e.py --source-home 'E:\path\to\existing-profile'
```

The default source home is the current `HERMES_HOME`, otherwise the user's
`.hermes` directory. `--endpoint pipe:hermes-memd` can override endpoint discovery.
The daemon must already be running; this harness never starts or stops it.
If the source profile has an existing `auth.json`, a disposable copy is used.
Otherwise the worker uses the repository's read-only Codex login reader for
`CODEX_HOME/auth.json` (or the user's `.codex/auth.json`) and its existing import
helper to populate only the disposable Hermes auth store. No login/setup is
performed, and no token values are logged. The original auth files are never
written. Credential resolution is read-only and OAuth refresh is blocked in the
worker: copied single-use refresh grants must not be consumed. A valid existing
access token is required throughout the check. An expired token stops the check
instead of rotating or replacing credentials.

### If the pipe is missing (WinError 2)

This means the configured pipe does not exist at connection time; the parent has
not launched. The harness now writes a blocked report and skips daemon cleanup
when no worker started, instead of masking this error with a second traceback.

To avoid touching the existing daemon/database, start the already-built binary
on a separate pipe and temporary database. In **PowerShell terminal 1**:

```powershell
Set-Location 'E:\AI_Project_Sem3\harmes-fork'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('memd-parent-child-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$savedTestPath = $env:PATH
try {
    $env:PATH = "$PWD\memd\msys2\mingw64\bin;$env:PATH"
    & .\memd\target-gnu\release\memd.exe --pipe memd-parent-child-e2e --store "$testRoot\memories.db" --model-dir "$PWD\memd\models\all-MiniLM-L6-v2" --ort-dylib "$PWD\memd\onnxruntime\onnxruntime-win-x64-1.31.1\lib\onnxruntime.dll" --model-revision model-v1
} finally {
    $env:PATH = $savedTestPath
}
```

Leave it running, then in **PowerShell terminal 2**:

```powershell
Set-Location 'E:\AI_Project_Sem3\harmes-fork'
& .\.memd-test-venv\Scripts\python.exe memd/scripts/verify_parent_child_e2e.py --endpoint pipe:memd-parent-child-e2e
```

After terminal 2 finishes, press Ctrl+C in terminal 1 to stop only that foreground
test daemon. Then in terminal 1 remove its exact database files and empty directory:

```powershell
Remove-Item -LiteralPath "$testRoot\memories.db", "$testRoot\memories.db-wal", "$testRoot\memories.db-shm" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $testRoot
```

The directory removal is nonrecursive; unexpected files are preserved. The pipe's
first-instance protection rejects a conflicting existing listener. These commands
do not rebuild dependencies or access the normal memory database.

## What is exercised

The harness calls the repository's supported `hermes_cli.oneshot.run_oneshot`
launcher with `openai-codex`, `gpt-6-luna`, and delegation/memory toolsets. It
installs the checkout's current directory plugin into a disposable test home,
selects `memory.provider: hermes-memd`, and enables `delegation.memd.enabled`.
Checkout context files and background memory review are disabled for this test.

In-memory observers call the real methods and record their returned values;
they do not mock the provider, daemon, model, delegation, or completion result.
They assert both agents' provider/model, unique `memd_memory` exposure, and the
child's read-only schema. The parent also must expose `delegate_task`.

After the parent's initial native-memory snapshot is loaded, a separate native
store writes one unique synthetic receipt through `provider.ingest_memory`.
An out-of-conversation primary recall verifies access. The prompt contains only
a lookup key; the exact receipt is never placed in a parent or child task prompt.
The parent must delegate the lookup and use the completed child's result.
Child recall counts, selection, token use, and budget are observed on the actual
`ContextPackage`; the model must also invoke the real memory tool.

Success requires a completed child answer, a matching completed delegation
result in the parent's tool history, and the receipt in the parent's completed
final answer. A parent model-issued recall invalidates success. Merely waiting
for a child or observing child recall cannot pass. Provider/model substitution
or any launcher error prevents overall success.

## Delivery semantics and limits

`tools/delegate_tool_dispatch.py::_resolve_async_wake_sid` deliberately returns
`None` when `HERMES_SINGLE_QUERY_SESSION=1`. Both supported finite launchers set
this marker. `_dispatch_background` then executes `_run_sync_with_note` and
returns the completed child result through the original tool call. This test
therefore verifies **normal finite-session inline result delivery**, including
a model-requested background call's supported fallback. No second turn is
necessary for that path.

It does **not** verify a long-lived CLI/gateway/TUI background completion,
notification ownership, or subsequent-turn race. The report explicitly sets
`background_subsequent_turn_verified: false`. For a previously observed hang in
a long-lived session, its actual session/runtime logs are still needed to
diagnose that separate path. Relevant code is
`tools/async_delegation.py`, `hermes_cli/cli_process_notifications.py`, and
`hermes_cli/cli_single_query.py`. Do not infer a race or fix core code merely
from an initial waiting response.

## Evidence and cleanup

Each run prints its evidence directory under `memd/.tmp-parent-child-e2e/<id>`:

- `report.json`: six stage results, IDs, real invocation/result payloads, final answer.
- `events.jsonl`: provider initialization/schema, actual tool recall, package
  metrics, visible turn/tool histories, and cleanup receipt. Hidden reasoning
  and credential/runtime objects are excluded.
- `launcher.txt`, isolated native sessions/logs/delegation traces when produced,
  and an SQLite backup of the isolated session DB when present.

The worker has a 360-second outer timeout and bounded agent iterations. On
timeout only the worker created by this script is terminated. No daemon is
auto-started. Cleanup inventories and deletes only the unique namespace bound
to the disposable profile hash (including its `:user` target), verifies both
are empty, and removes the disposable home/auth copy. Evidence is retained.
Cleanup failure raises and is recorded; inspect the cleanup receipt even when
the six model stages pass. No real memories, benchmark artifacts, learning
systems, or existing uncommitted implementation files are changed.

## Regression commands

No production fix accompanies this harness. To rerun the 17 focused tests and
the existing lifecycle/delivery tests using the required repository wrapper,
run the following from PowerShell with Git Bash installed:

```powershell
$env:HERMES_PYTHON = 'E:/AI_Project_Sem3/harmes-fork/.memd-test-venv/Scripts/python.exe'
& 'C:\Program Files\Git\bin\bash.exe' -c 'export PATH=/usr/bin:/bin:$PATH; bash scripts/run_tests.sh memd/tests/test_hermes_bridge.py tests/tools/test_delegate_memd_access.py tests/tools/test_memd_subagent_readonly.py -j 2'
if ($LASTEXITCODE -ne 0) { throw 'Focused regression tests failed' }
& 'C:\Program Files\Git\bin\bash.exe' -c 'export PATH=/usr/bin:/bin:$PATH; bash scripts/run_tests.sh tests/tools/test_async_delegation.py tests/tools/test_async_delegation_retirement.py tests/hermes_cli/test_cli_async_delegation_delivery.py tests/tui_gateway/test_delegation_session_lifecycle.py -j 2'
if ($LASTEXITCODE -ne 0) { throw 'Delegation lifecycle tests failed' }
```

Use the wrapper's actual passed/failed/skipped counts; the prior 17-pass receipt
is historical and is not a new execution result.
