# Benchmark Readiness Report — infrastructure only

No benchmark task, smoke test, Harmes model request, or scorer was run in this phase. The baseline remains Harmes commit `f42f579cf8bac4918ac9599bece71618afadd846`, provider `openai-codex`, model `gpt-6-luna`, Harmes-owned OAuth, no OpenAI API key. All new code is outside Harmes core under `benchmarks/`.

| Benchmark | Data | Dependencies | Isolation | Scorer | Harmes adapter | Ready? |
|---|---|---|---|---|---|---|
| LongCoT-Mini | Local official source at pinned commit | Verified in isolated Python 3.14 env | Fresh CLI process and per-task artifact directory | Official `run_eval.py` at same commit; `--no-fallback` | Implemented; schema conversion matches evaluator | **READY** (infrastructure only; no task run) |
| GAIA | Exact pinned repo accessible | Dataset libraries present in isolated env | Per-task workspace and attachment copy | Pinned official `question_scorer` from leaderboard Space | Implemented; syntax/help verified | **READY** (infrastructure only; no task run) |
| Terminal-Bench Lite | Official task source pinned; official task base image built | Harbor 0.24.0; Docker Desktop/WSL2 verified | Container creation works; Harbor↔original-Harmes boundary unverified | Per-task official `tests/test.sh` and pytest identified | Not implemented: safe Harbor↔Harmes execution bridge not yet verified | **BLOCKED** — adapter/confinement bridge |
| OOLONG-Pairs | Official pair files enumerated at pinned revision; matching TREC context unverified | `datasets` / `huggingface_hub` present in isolated env | Fresh CLI process possible | No canonical versioned scorer implementation found; no substitution made | Not implemented pending canonical evaluator | **BLOCKED** — scorer source and TREC context must be verified |
| OOLONG Yahoo | Official test split/revision pinned; one exact Yahoo 32K row staged | Dependencies and scorer import verified | Fresh CLI process and per-task artifact directory | Official `synth_process_response` at pinned OOLONG commit | Implemented; stage-only verified | **READY** (infrastructure only; no task run) |
| Aider Polyglot | Exercises pinned locally | Official image build attempted; stopped on Docker container apt I/O errors; retry deferred with C: at 9.47 GiB free | Docker works, but safe Harmes/container bridge unverified | Upstream language-specific tests identified | Not implemented until safe container bridge exists | **BLOCKED** — storage headroom and confinement design |

## LongCoT-Mini — READY for a later smoke

- Source/dataset: [LongHorizonReasoning/longcot](https://github.com/LongHorizonReasoning/longcot), commit `fb9649423f15f5b0091f8e988b100596cac592ca`; official task data is the repository's `src/data/<domain>/easy.json` subset. The selected later record is `logic/easy/BlocksWorld_easy_1`.
- Official input: the record's full `prompt` string, passed unchanged. Official output format: `solution = ...`; the prompt specifies the result shape. Official response JSONL fields are `question_id`, `domain`, `difficulty`, `successful`, `response_text`.
- Official evaluator/verifier: same pinned `run_eval.py` and `longcot.verify`. It consumes that JSONL schema and writes official result JSON. No scorer code was changed. `--no-fallback` prevents optional Gemini fallback calls.
- RDKit remains necessary because the official verifier imports the chemistry verifier eagerly, including for non-chemistry tasks. The isolated `benchmarks/envs/longcot` Python 3.14 environment imports the required modules and the official evaluator CLI help succeeds.
- Adapter: [longcot.py](/E:/AI_Project_Sem3/harmes-fork/benchmarks/adapters/longcot.py). It verifies both checkout commits, launches a fresh Harmes CLI process with explicit provider/model, saves the unmodified Harmes JSONL stream/stderr/prompt, maps the final text into the evaluator schema, then runs the pinned official scorer. It requires `--execute`, so help/inspection does not load a task.
- Later exact smoke command (not run):

  ```powershell
  .\benchmarks\envs\longcot\Scripts\python.exe .\benchmarks\adapters\longcot.py --source "C:\Users\PMYLS\AppData\Local\Temp\harmes-bench-sources\longcot" --domain logic --difficulty easy --question-id BlocksWorld_easy_1 --execute
  ```

- The adapter internally invokes `.venv\Scripts\hermes.exe chat --query-file ... --format stream-json --provider openai-codex --model gpt-6-luna`, then the official `run_eval.py ... --no-fallback`. Raw artifacts go under `benchmarks/artifacts/longcot/BlocksWorld_easy_1/`.

## GAIA — READY for a later one-task smoke

- Official source/revision: [Hugging Face `gaia-benchmark/GAIA`](https://huggingface.co/datasets/gaia-benchmark/GAIA), exact revision `682dd723ee1e1697e00360edccf2366dc8418dd9`.
- Official CLI identity remains `Shozab234`. Exact-revision dry-run at `682dd723ee1e1697e00360edccf2366dc8418dd9` now enumerates the repository files without access denial. Downloaded and read only the pinned `README.md`; no question, answer, or task attachment was downloaded or inspected. The card defines Parquet configs and fields and points to the official leaderboard.
- Official per-question validation scorer: `question_scorer` from [gaia-benchmark/leaderboard](https://huggingface.co/spaces/gaia-benchmark/leaderboard), pinned Space revision `1349a17979f0aca0ee9c46cd7ec26eb2fb41102e`. The adapter imports this exact function locally; it does not use the leaderboard submission/upload flow.
- Adapter: [gaia.py](/E:/AI_Project_Sem3/harmes-fork/benchmarks/adapters/gaia.py). It requires an explicit task ID and `--execute`, loads only the pinned `2023_level1` validation task, copies that task's attachment (if any) into a task workspace, invokes the shared fresh Harmes CLI transport, and then calls the pinned official per-question scorer. Python compilation and `--help` succeeded; no task row, attachment, Harmes session, or scorer was loaded/run during these checks.
- No admin privilege is required for access. Later invocation shape (choose a validation task ID only when authorized to run the smoke): `python benchmarks/adapters/gaia.py --task-id <task_id> --execute` from the isolated benchmark Python environment.

## Terminal-Bench Lite — BLOCKED

- Official source/revision: [laude-institute/terminal-bench-lite](https://github.com/laude-institute/terminal-bench-lite), commit `4e2740540609ed469e33f37464cefeb8c970155e`. Task folders use Harbor task format, task-defined container environment, and official task tests (`tests/test.sh` / pytest). Harbor 0.24.0 is installed in `benchmarks/envs/harbor`; version/help work. No Harbor job or task was started.
- The pinned `hello-world` task metadata sets version 1.0, agent/verifier timeout 900 seconds, image `python:3.13-slim-bookworm`, 1 CPU, 2 GiB memory, 10 GiB storage; the official verifier installs `pytest==8.4.1` and writes Harbor's reward file. These are read-only task-definition checks, not task execution.
- WSL reports default version 2; Ubuntu is registered, and Docker Desktop's `docker-desktop` WSL distro is running. Docker Desktop 4.94.0 / Engine 29.8.2 is operational. `docker run --rm hello-world` successfully pulled, created, ran, and removed the disposable test container. Harbor 0.24.0 help and agent listing work; no Harbor job was run.
- Built the `hello-world` task environment using the official task Dockerfile from the pinned checkout, tagged `tbench-lite-hello-world:4e274054`. No task instruction or verifier was run. The remaining blocker is a verified Harbor adapter that executes the original Harmes agent while routing its filesystem/terminal work into the task container without moving OAuth credentials into it.
- Previous required administrator setup is complete; no further admin action currently identified.
- The built-in Harbor Hermes agent is not acceptable: it installs a different/current Hermes and expects separate provider/API configuration. A host-side Harmes process has not been proven confined to Harbor's task container, so no adapter is claimed ready.

## OOLONG-Pairs — BLOCKED

- Official data repo/revision: [MIT OASYS `mit-oasys/oolong-pairs`](https://huggingface.co/datasets/mit-oasys/oolong-pairs), `d1e1522b86ac0c169bbc890b0471408aaa29e8fa`. Exact official HF CLI dry-run lists only `.gitattributes`, `README.md`, the context-length-specific gold JSON files, `pairs_summary.parquet`, and `questions.json`; it contains no `run_all.py`.
- The card specifies matching each question to the same-length `oolongbench/oolong-synth` validation `trec_coarse` context. Use `context_window_text` (no labels) followed by the question; labels were used only to build the gold answers. Prediction format is sorted unique `(id1, id2)` pairs, `id1 < id2`, one per line. It points to `run_all.py` for exact prompt and pair-matching metric.
- The pinned public authors' RLM repository checkout (`alexzhang13/rlm`, commit `d04208afbad29ca675ab13478c40ee8bebc84bfe`) also has no OOLONG-Pairs scorer/runner. The paper reports F1 results, but that does not verify an executable official scorer or its exact normalization. I did not use a community evaluator or invent a formula.
- Required action: obtain the benchmark authors' canonical `run_all.py` source/revision or an official scorer release, then verify the referenced `trec_coarse` context exists at the pinned OOLONG-Synth revision `f0d59eaf0febf130664cfceb710436c8e3216b2b`. No admin privilege or gated account is identified.

## OOLONG Yahoo — BLOCKED pending staged-row verification

- Official data: [oolongbench/oolong-synth](https://huggingface.co/datasets/oolongbench/oolong-synth), revision `f0d59eaf0febf130664cfceb710436c8e3216b2b`, `test` split, `dataset == "yahoo"`. Official Yahoo Topics data only; no other Yahoo set is used. HF metadata-only listing succeeds; the test split spans 41 Parquet shards (about 20.1 GB uncompressed), so the full split was not downloaded.
- Official construction is exactly `context_window_text + "\n" + question`, with the complete unlabeled context. The OOLONG evaluation repository is [abertsch72/oolong](https://github.com/abertsch72/oolong), commit `0bb7eabe839218fee7fe8d007f41cfc2fd3ae24c`; scorer is `src/eval/eval_helpers.py::synth_process_response`. It performs official answer parsing, exact/normalized comparison phrases, date equality, and numeric partial credit `0.75 ** abs(gold - prediction)`.
- The declared requirements (`transformers`, `datasets`, `tiktoken`, `litellm`, `jsonlines`, `python-dateutil`, `anthropic`, `google-genai`) are installed in `benchmarks/envs/oolong`; imports including the official evaluator succeed with pinned `src/eval` on `PYTHONPATH`.
- Adapter: [oolong_yahoo.py](/E:/AI_Project_Sem3/harmes-fork/benchmarks/adapters/oolong_yahoo.py). It pins both source revisions, downloads only the specified pinned test shard, selects the exact row, passes the complete unlabeled context/question to a fresh Harmes process, and calls the official scorer on the returned text. `--stage-only` stages without Harmes/scoring; `--execute` is explicit.
- Earlier full streaming row selection was interrupted after about 1.36 GB. A bounded metadata scan across the exact revision found Yahoo 32K row `515050033` in `data/test-00021-of-00041.parquet`; the scan exposed no question/context/answer contents. Stage-only preparation then wrote a 72,073-byte prompt and manifest at `benchmarks/data/oolong-yahoo/yahoo-32768-515050033/`; the manifest records the exact dataset revision and prompt hash, and confirms no reference answer is present. Adapter `py_compile` and `--help` succeeded. No Harmes session or scorer ran. The exact command for a later one-task smoke is:

  ```powershell
  .\benchmarks\envs\oolong\Scripts\python.exe .\benchmarks\adapters\oolong_yahoo.py --source "C:\Users\PMYLS\AppData\Local\Temp\harmes-bench-sources\oolong" --context-len 32768 --row-id 515050033 --shard-filename data/test-00021-of-00041.parquet --staged-prompt .\benchmarks\data\oolong-yahoo\yahoo-32768-515050033\prompt.txt --execute
  ```

## Aider Polyglot — BLOCKED

- Official revisions: [Aider](https://github.com/Aider-AI/aider) `5dc9490bb35f9729ef2c95d00a19ccd30c26339c`; [Polyglot exercises](https://github.com/Aider-AI/polyglot-benchmark) `7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f`.
- Supported languages and pinned harness test commands: Python `pytest`; Rust `cargo test -- --include-ignored`; Go `go test ./...`; JavaScript `/aider/benchmark/npm-test.sh`; C++ `/aider/benchmark/cpp-test.sh`; Java `./gradlew test`. The official image is built from `benchmark/Dockerfile` (`buildpack-deps:jammy`, Python 3.11, Go 1.21.5, Rust, Node 20, OpenJDK 21 plus C++/Java test tooling); the official `docker.sh` uses a 12-GB container and mounts source/benchmark directories. The image build did not complete, so `aider-benchmark` is not available yet.
- Do not invoke Aider's `benchmark.py` solving loop or official `docker.sh` as-is: it injects `OPENAI_API_KEY` and runs Aider as the agent. Use only task data and upstream tests in any future bridge.
- Docker has recovered. Read-only verification: Docker Engine `29.8.2`; `docker system df` responds; `docker run --rm hello-world` pulled/created/ran/removed a disposable container. Docker reports 3 images (214.8 MB) and 1.366 GB build cache; host free space is C: 9.47 GiB and E: 23.63 GiB. The official Aider image build has not been retried because the previous apt writes failed with I/O errors when C: was exhausted, and another build could consume the remaining space. No Aider command, exercise, or verifier ran. The safe original-Harmes/container bridge remains unverified.

## Adapter checks and repository state

- GAIA and OOLONG Yahoo adapter `py_compile` and `--help` succeeded when launched with the approved process permission. OOLONG `--stage-only` also succeeded; no Harmes session or scorer was invoked. These are preparation checks only.
- LongCoT and OOLONG environments are separate from Harmes `.venv`; Harbor 0.24.0 is separate. The previous `Access is denied` is the Codex process sandbox: benchmark Python and a fresh venv launch under permitted process execution. ACLs are sufficient; Defender is active with no observed block.
- `git status` shows the fixed HEAD and only `?? benchmarks/`. Tracked `git diff` is empty. Harmes source/configuration is unchanged. Infrastructure actions created Docker image `tbench-lite-hello-world:4e274054`, downloaded GAIA's pinned README and one small OOLONG parquet shard to the Hugging Face cache, and attempted (but did not complete) Aider's official Docker image build. Docker is operational; preserve the VHDX before any repair/reset. No benchmark task or scorer has been run.
