# EvolveStudio

A local workbench on top of [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) — evolutionary code generation powered by your own LLM running on your own machine.

Type a problem in plain English, paste a starting program plus a unit-test evaluator, and watch a local LLM iteratively rewrite the code over many generations. The lineage of every attempt, the chosen path of mutations, and per-candidate diffs are visualized in a web app — with a **clickable lineage tree** (click any candidate node to see its code and the diff against its parent).

> **Status:** working prototype, built as a CS 153 (Stanford) class project. macOS / Linux only. Default target: [`gpt-oss:20b`](https://ollama.com/library/gpt-oss) served by [Ollama](https://ollama.com/).

The UI is a thin **FastAPI** backend serving a static **vanilla-JS + D3** frontend. (A legacy Streamlit GUI is kept alongside it.)

---

## What it does

EvolveStudio wires three things together:

1. **Compiler** — turns a problem spec (title, statement, starting code, test cases) into the three input files OpenEvolve consumes: `initial_program.py`, `evaluator.py`, `config.yaml`.
2. **Runner** — launches OpenEvolve as a detached subprocess against your local Ollama, tees stdout/stderr to disk so you can review the run after the fact.
3. **Visualizer** — defensively parses OpenEvolve's run output (`evolution_trace.jsonl`, `best/`, `checkpoints/`) and serves it as JSON to a D3 frontend that renders a clickable lineage tree, per-candidate diffs, score progression, and log tails.

Two demo problems ship out of the box:

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

The app opens at `http://localhost:8501`. In the **Compose** tab, click one of the *Quick start* chips (Bubble sort / Edit distance / Euler #1) to pre-fill everything, then **Save experiment**, switch to the **Run** tab, click **Execute OpenEvolve**, and watch the **Results** tab — the lineage tree fills in live, colored by score, with the best path highlighted. Click any node to inspect its code and diff.

> **Legacy Streamlit GUI** (still works): `streamlit run evolvestudio/gui/app.py`

---

## How it works

```
User input              Compiler                OpenEvolve              Visualizer
───────────────         ────────                ──────────              ──────────
Problem statement                                                                       
+ starting code      →  3 text files       →   Loops:                   Parses
+ unit tests            on disk                  • Sample parent        evolution_trace.jsonl
                        (initial_program.py,     • Prompt Ollama          + best/ + checkpoints/
                         evaluator.py,           • Mutate code               ↓
                         config.yaml)            • Evaluate              Renders lineage tree,
                                                 • Score & store         candidate diffs,
                                                                         log tails
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
│   ├── cli.py                # python -m evolvestudio.cli (six subcommands)
│   ├── experiments.py        # Shared save/list/read/build-argv/demos (Streamlit-free)
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
│   ├── index.html            # top chrome + Compose / Run / Results screens
│   ├── styles.css            # design tokens + dark theme
│   ├── app.js                # tab routing, fetch calls, Compose/Run/Results
│   └── lineage.js            # D3 lineage graph + inspector (the hero)
│
├── generated_experiments/    # Compiled experiments + runs (gitignored, regenerate via demos)
└── third_party/openevolve/   # Cloned separately (gitignored — see Quick start)
```

### HTTP API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/experiments` | list experiments |
| POST | `/api/experiments` | save an experiment from pasted text |
| GET | `/api/experiments/{slug}` | read one experiment's files |
| GET | `/api/demos/{kind}` | render a bundled demo's files |
| POST | `/api/runs` | launch a detached OpenEvolve run |
| POST | `/api/runs/{run_id}/stop` | SIGTERM the run's process group (SIGKILL after 5s) |
| GET | `/api/runs/{run_id}/status` | `{state, iters_done, best_score, log_tail, elapsed}` |
| GET | `/api/runs/{run_id}/lineage` | `{best_id, nodes:[{id,score,iter,parent}]}` |
| GET | `/api/runs/{run_id}/node/{node_id}` | `{id, score, iter, parent, code, diff, changes}` |

---

## CLI reference

Same operations as the GUI, scriptable.

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

- **No network calls without your action.** The GUI is purely local. The only outbound traffic is whatever OpenEvolve sends to Ollama (also local) during an Execute.
- **Detached subprocess.** Execute uses `subprocess.Popen(..., start_new_session=True)` — closing the browser tab or restarting Streamlit doesn't kill the run.
- **Process-group stop.** The Stop button sends `SIGTERM` to the entire OpenEvolve process group (controller + parallel workers); `SIGKILL` after a 5-second grace period.
- **Thread-based per-test timeouts.** The unit-test evaluator runs each test in a daemon thread with `join(timeout)`. This works in any thread — OpenEvolve evaluates candidates in worker threads, where signal-based (`SIGALRM`) timeouts raise *"signal only works in main thread"* and would make every test fail. On timeout the worker thread is abandoned (daemon, so it can't block process exit).
- **Defensive parser.** The visualizer reads run output assuming any of `best/`, `evolution_trace.jsonl`, `checkpoints/` may be missing or malformed. Malformed JSONL lines are silently skipped; missing files render as "(missing)" without crashing.

---

## Roadmap

- [ ] **LLM-driven scaffolding** — type a problem statement, have Ollama auto-generate the starting program and unit tests. Today you paste both manually.
- [ ] More compiler templates (graph problems, regex matching, ML mini-benchmarks).
- [x] **True click-on-node lineage navigation** — done in the FastAPI + D3 frontend (the Streamlit version couldn't, since `st.graphviz_chart` is a static SVG).
- [ ] Cross-platform runner (replace POSIX `os.killpg` / `start_new_session` so launch/stop works on Windows).
- [ ] Multi-language support (R, Rust — OpenEvolve already supports them; just needs new compiler templates).

---

## Acknowledgements

Built as a project for **CS 153 (Stanford)**.

Heavy lifting by:

- [**OpenEvolve**](https://github.com/algorithmicsuperintelligence/openevolve) — the evolutionary engine under the hood.
- [**Ollama**](https://ollama.com/) — local LLM serving with an OpenAI-compatible API.
- [**Streamlit**](https://streamlit.io/) — the GUI.
