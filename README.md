# EvolveStudio

A local workbench on top of [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) — evolutionary code generation powered entirely by your own LLM running on your own machine.

**Paste a problem in plain English** (a LeetCode prompt, a Project Euler question, a coding-interview problem). A local model reads it and **builds the test harness for you** — the starting program and the unit-test evaluator — then OpenEvolve iteratively rewrites the code over many generations until it passes. The lineage of every attempt, the chosen path of mutations, and per-candidate diffs are shown in a web app with a **clickable lineage tree** (click any candidate node to see its code and the diff against its parent).

> **Status:** working prototype, built as a CS 153 (Stanford) class project. macOS / Linux only. Default model: [`gpt-oss:20b`](https://ollama.com/library/gpt-oss) served by [Ollama](https://ollama.com/) — but you can switch to any installed Ollama model from a dropdown in the app.

The UI is a thin **FastAPI** backend serving a static **vanilla-JS + D3** frontend. (A legacy Streamlit GUI is kept alongside it.)

---

## What it does

EvolveStudio wires four things together:

1. **Harness generator** — a local LLM reads your natural-language problem and produces a *structured spec* (function name, signature, a naive baseline, and a set of test cases). The model never writes the evaluator or config directly (those must match OpenEvolve's contract exactly); it only produces the spec, which is then compiled deterministically. This is streamed to the UI so you watch it happen.
2. **Compiler** — turns that spec into the three input files OpenEvolve consumes: `initial_program.py` (with `# EVOLVE-BLOCK` markers), `evaluator.py` (runs each test under a timeout, scores `passed / total`), and `config.yaml`.
3. **Runner** — launches OpenEvolve as a detached subprocess against your local Ollama, stops early once a candidate hits the target score, and tees stdout/stderr to disk.
4. **Visualizer** — defensively parses OpenEvolve's run output (`evolution_trace.jsonl`, `best/`, `checkpoints/`) and serves it as JSON to a D3 frontend that renders a clickable lineage tree, per-candidate diffs, score progression, and log tails.

You pick which Ollama model drives both generation and the evolution loop from a dropdown in the top bar (auto-populated from your `ollama list`).

A few built-in demo problems are also available (via the CLI or the `/api/demos` endpoint) for quick testing:

| Demo | Difficulty | What can evolve |
|---|---|---|
| **Bubble sort** | One-shot — most LLMs solve in 1–3 iterations | O(n²) → faster algorithms |
| **Edit distance (Levenshtein)** | Real challenge — naive baseline scores ~79% (3 of 14 tests time out) | Exponential recursion → memoization / DP |
| **Project Euler #1** | Trivial correctness, room for optimization | Naive loop → closed-form arithmetic series |

---

## Requirements

- **OS:** macOS or Linux. The runner uses POSIX process groups (`os.killpg`, `start_new_session`) to launch/stop OpenEvolve.
- **Python 3.10+** (the OpenEvolve env in this project uses 3.11).
- **[Conda](https://docs.conda.io/)** (recommended — keeps the OpenEvolve install isolated). Any Python 3.10+ virtual env works too.
- **[Ollama](https://ollama.com/)** running locally with at least one model pulled. Defaults assume `gpt-oss:20b` (~13 GB).

---

## Quick start

```bash
# 1. Clone this repo
git clone https://github.com/<your-username>/<your-repo-name>.git evolvestudio
cd evolvestudio

# 2. Clone OpenEvolve into third_party/ (this directory is gitignored)
mkdir -p third_party
git clone https://github.com/algorithmicsuperintelligence/openevolve.git third_party/openevolve

# 3. Create + activate the conda env
conda create -n OpenEvolve python=3.11 -y
conda activate OpenEvolve

# 4. Install OpenEvolve (editable) + EvolveStudio deps
pip install -e third_party/openevolve
pip install -r requirements.txt

# 5. Make sure Ollama is serving and the model is pulled
ollama serve &              # in another terminal if not already running
ollama pull gpt-oss:20b     # ~13 GB

# 6. Launch the web app
uvicorn evolvestudio.server.main:app --port 8501
```

The app opens at `http://localhost:8501`. Then:

1. **Compose** — paste a problem in plain English and click **Generate harness**. The model writes the harness live; review (and edit) the generated files, then **Save & go to Run**.
2. **Run** — set max iterations and the target score (default 1.0 = stop as soon as all tests pass), then **Execute**. Status streams in the top bar.
3. **Results** — watch the lineage tree fill in live, colored by score, with the best path highlighted. Click any node to inspect its code and the diff against its parent.

Pick the model for both generation and evolution from the **dropdown in the top bar**; the ↻ button refreshes the list from Ollama.

> **Legacy Streamlit GUI** (still works, with built-in demo problems): `streamlit run evolvestudio/gui/app.py`

---

## How it works

```
Problem (English)   LLM + Compiler          OpenEvolve              Visualizer
─────────────────   ──────────────          ──────────              ──────────
"Two Sum: given                              Loops until target:     Parses
 an array…"      →  gpt-oss writes a    →     • Sample parent    →   evolution_trace.jsonl
                    spec → compiled to        • Prompt Ollama         + best/ + checkpoints/
                    3 files on disk           • Mutate code               ↓
                    (initial_program.py,      • Evaluate (tests)     Renders clickable
                     evaluator.py,            • Score & store        lineage tree, diffs,
                     config.yaml)             • Stop at target       scores, log tails
```

Each run produces a timestamped output directory under the experiment folder (e.g. `generated_experiments/edit_distance/run_20260517T184523/`) containing:
- `evolution_trace.jsonl` — one row per parent → child mutation (with code, diff, scores, prompts)
- `best/best_program.py` + `best/best_program_info.json` — the winner so far
- `checkpoints/checkpoint_N/` — snapshots at every Nth iteration
- `logs/*.log` — OpenEvolve's own logger output
- `stdout.log` / `stderr.log` — captured by the runner

---

## Project layout

```
.
├── evolvestudio/             # The workbench
│   ├── cli.py                # python -m evolvestudio.cli (lower-level, scriptable)
│   ├── harness_gen.py        # LLM problem → structured spec → compiled harness
│   ├── experiments.py        # Shared save/list/read/build-argv/demos/model-set (Streamlit-free)
│   ├── compiler/             # Problem spec → OpenEvolve input files
│   │   ├── euler.py          # Project Euler / single-answer template
│   │   └── unit_tests.py     # Function + unit-tests template (thread-based timeouts)
│   ├── runner/
│   │   └── process.py        # Detached subprocess launch/stop/status + run registry
│   ├── visualizer/
│   │   ├── parse.py          # Defensive run-output parser (never raises)
│   │   └── report.py         # Text renderer + shared helpers (score/diff/tail)
│   ├── server/               # FastAPI backend (thin wrappers over the above)
│   │   ├── main.py           # app + routes + static mount for ../../frontend
│   │   ├── runs.py           # run lifecycle re-exports
│   │   └── lineage.py        # parse.py → lineage / node JSON
│   ├── templates/            # OpenEvolve config templates (Ollama-targeted)
│   └── gui/
│       └── app.py            # Legacy Streamlit GUI
│
├── frontend/                 # Static SPA served by FastAPI at "/"
│   ├── index.html            # top chrome + model picker + Compose / Run / Results
│   ├── styles.css            # design tokens + dark theme
│   ├── app.js                # tab routing, fetch calls, streaming generation
│   └── lineage.js            # D3 lineage graph + inspector (the hero)
│
├── generated_experiments/    # Generated experiments + runs (gitignored)
└── third_party/openevolve/   # Cloned separately (gitignored — see Quick start)
```

### HTTP API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/models` | live list of installed Ollama chat models (for the picker) |
| POST | `/api/generate` | LLM problem → compiled harness (`{slug, spec, files}`) |
| POST | `/api/generate/stream` | same, streamed live as Server-Sent Events |
| GET | `/api/experiments` | list experiments |
| POST | `/api/experiments` | save an experiment from pasted/edited text |
| GET | `/api/experiments/{slug}` | read one experiment's files |
| GET | `/api/demos/{kind}` | render a bundled demo's files |
| POST | `/api/runs` | launch a detached run (`{slug, iterations, target_score, model}`) |
| POST | `/api/runs/{run_id}/stop` | SIGTERM the run's process group (SIGKILL after 5s) |
| GET | `/api/runs/{run_id}/status` | `{state, iters_done, best_score, log_tail, elapsed}` |
| GET | `/api/runs/{run_id}/lineage` | `{best_id, nodes:[{id,score,iter,parent}]}` |
| GET | `/api/runs/{run_id}/node/{node_id}` | `{id, score, iter, parent, code, diff, changes}` |

---

## CLI reference

A lower-level, scriptable path (no LLM generation — that lives in the web app). Useful for materializing the demo problems and smoke-testing evaluators.

```bash
python -m evolvestudio.cli --help
```

| Command | Purpose |
|---|---|
| `generate-euler-demo` | Materialize Project Euler #1 demo |
| `generate-sort-demo` | Materialize bubble-sort + unit-tests demo |
| `review <exp_dir>` | Print metadata + generated files for an experiment |
| `evaluate <exp_dir>` | Smoke-test the evaluator on the initial program (`shell=False`, timeout) |
| `run-openevolve <exp_dir>` | Prepare (or `--execute`) the OpenEvolve invocation |
| `view <output_dir>` | Render a finished run (summary, top candidates, diffs, logs) |

Every subcommand supports `--help`.

---

## Design notes

- **Everything is local.** The app makes no cloud calls. The only outbound traffic is to your local Ollama — during harness generation and during an Execute.
- **The LLM only writes a spec, not the harness.** Harness generation asks the model for a structured spec (function, baseline, test cases), then compiles it with the known-correct `unit_tests` compiler. JSON is extracted defensively (handles fences, reasoning preambles, and a Python-literal fallback) so a sloppy model response degrades to a clear error instead of a broken evaluator.
- **Early stopping.** Runs pass `--target-score` (default 1.0) so OpenEvolve halts the moment a candidate passes everything, instead of burning the full iteration budget.
- **Detached subprocess.** Execute uses `subprocess.Popen(..., start_new_session=True)` — closing the browser tab or restarting the server doesn't kill the run. A small on-disk registry lets status/stop/lineage keep working across server restarts.
- **Process-group stop.** Stop sends `SIGTERM` to the entire OpenEvolve process group (controller + parallel workers); `SIGKILL` after a 5-second grace period.
- **Thread-based per-test timeouts.** The unit-test evaluator runs each test in a daemon thread with `join(timeout)`. This works in any thread — OpenEvolve evaluates candidates in worker threads, where signal-based (`SIGALRM`) timeouts raise *"signal only works in main thread"* and would make every test fail.
- **Streaming-safe.** The SSE generation endpoint sends `Connection: close` so the browser doesn't reuse the streamed connection for the next request (which otherwise fails in Safari).
- **Defensive parser.** The visualizer reads run output assuming any of `best/`, `evolution_trace.jsonl`, `checkpoints/` may be missing or malformed. Malformed JSONL lines are silently skipped; missing files degrade gracefully without crashing.

---

## Roadmap

- [x] **LLM-driven scaffolding** — paste a problem statement, a local model generates the harness (baseline + test cases). Done.
- [x] **Model picker** — choose any installed Ollama model for generation and evolution, from the app. Done.
- [x] **Early stopping** — halt a run once a candidate hits the target score. Done.
- [x] **True click-on-node lineage navigation** — done in the FastAPI + D3 frontend (the Streamlit version couldn't, since `st.graphviz_chart` is a static SVG).
- [ ] **Separate models per role** — backend already supports it (the run takes a `model`); the UI currently uses one global pick.
- [ ] **Verify generated test cases** — a wrong expected value makes a correct solution look broken; auto-check or flag suspicious cases.
- [ ] More compiler templates (graph problems, regex matching, ML mini-benchmarks).
- [ ] Cross-platform runner (replace POSIX `os.killpg` / `start_new_session` so launch/stop works on Windows).
- [ ] Multi-language support (R, Rust — OpenEvolve already supports them; just needs new compiler templates).

---

## Acknowledgements

Built as a project for **CS 153 (Stanford)**.

Heavy lifting by:

- [**OpenEvolve**](https://github.com/algorithmicsuperintelligence/openevolve) — the evolutionary engine under the hood.
- [**Ollama**](https://ollama.com/) — local LLM serving with an OpenAI-compatible API.
- [**FastAPI**](https://fastapi.tiangolo.com/) + [**D3**](https://d3js.org/) — the web backend and the lineage visualization.
- [**Streamlit**](https://streamlit.io/) — the legacy GUI.
