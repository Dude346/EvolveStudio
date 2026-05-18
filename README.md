# EvolveStudio

A local workbench on top of [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) — evolutionary code generation powered by your own LLM running on your own machine.

Type a problem in plain English, paste a starting program plus a unit-test evaluator, and watch a local LLM iteratively rewrite the code over many generations. The lineage of every attempt, the chosen path of mutations, and per-candidate diffs are visualized in a Streamlit GUI.

> **Status:** working prototype, built as a CS 153 (Stanford) class project. macOS / Linux only. Default target: [`gpt-oss:20b`](https://ollama.com/library/gpt-oss) served by [Ollama](https://ollama.com/).

---

## What it does

EvolveStudio wires three things together:

1. **Compiler** — turns a problem spec (title, statement, starting code, test cases) into the three input files OpenEvolve consumes: `initial_program.py`, `evaluator.py`, `config.yaml`.
2. **Runner** — launches OpenEvolve as a detached subprocess against your local Ollama, tees stdout/stderr to disk so you can review the run after the fact.
3. **Visualizer** — defensively parses OpenEvolve's run output (`evolution_trace.jsonl`, `best/`, `checkpoints/`) and renders a lineage tree, per-candidate diffs, score progression, and log tails in a Streamlit GUI.

Two demo problems ship out of the box:

| Demo | Difficulty | What can evolve |
|---|---|---|
| **Bubble sort** | One-shot — most LLMs solve in 1–3 iterations | O(n²) → faster algorithms |
| **Edit distance (Levenshtein)** | Real challenge — naive baseline scores ~79% (3 of 14 tests time out) | Exponential recursion → memoization / DP |
| **Project Euler #1** | Trivial correctness, room for optimization | Naive loop → closed-form arithmetic series |

---

## Requirements

- **OS:** macOS or Linux. The unit-test evaluator uses `SIGALRM` for per-test timeouts, which is Unix-only.
- **Python 3.10+** (the OpenEvolve env in this project uses 3.11).
- **[Conda](https://docs.conda.io/)** (recommended — keeps the OpenEvolve install isolated).
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

# 4. Install OpenEvolve (editable) + Streamlit
pip install -e third_party/openevolve
pip install streamlit

# 5. Make sure Ollama is serving and the model is pulled
ollama serve &              # in another terminal if not already running
ollama pull gpt-oss:20b     # ~13 GB

# 6. Launch the GUI
streamlit run evolvestudio/gui/app.py
```

The GUI opens at `http://localhost:8501`. In the **Compose** tab, click one of the *Quick start* buttons (Bubble sort / Edit distance / Euler #1) to pre-fill everything, then **Save experiment**, switch to the **Run** tab, and click **Execute OpenEvolve**.

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
│   ├── compiler/             # Problem spec → OpenEvolve input files
│   │   ├── euler.py          # Project Euler / single-answer template
│   │   └── unit_tests.py     # Function + unit-tests template (SIGALRM timeouts)
│   ├── runner/               # OpenEvolve subprocess wrapper (impl currently in cli.py)
│   ├── visualizer/
│   │   ├── parse.py          # Defensive run-output parser (never raises)
│   │   └── report.py         # Text renderer + helpers used by GUI
│   ├── templates/            # OpenEvolve config templates (Ollama-targeted)
│   └── gui/
│       └── app.py            # Streamlit GUI (3 tabs: Compose / Run / Results)
│
├── generated_experiments/    # Compiled experiments + runs (gitignored, regenerate via demos)
└── third_party/openevolve/   # Cloned separately (gitignored — see Quick start)
```

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
- **Signal-based per-test timeouts.** The unit-test evaluator uses `SIGALRM` to actually interrupt slow candidates. (`ThreadPoolExecutor` can't kill runaway threads in Python; the timeout would have been a lie.)
- **Defensive parser.** The visualizer reads run output assuming any of `best/`, `evolution_trace.jsonl`, `checkpoints/` may be missing or malformed. Malformed JSONL lines are silently skipped; missing files render as "(missing)" without crashing.

---

## Roadmap

- [ ] **LLM-driven scaffolding** — type a problem statement, have Ollama auto-generate the starting program and unit tests. Today you paste both manually.
- [ ] More compiler templates (graph problems, regex matching, ML mini-benchmarks).
- [ ] **True click-on-node lineage navigation.** Right now `st.graphviz_chart` renders a static SVG; clicks can't reach Python. The dropdown-next-to-tree workaround is the practical alternative.
- [ ] Cross-platform timeouts (replace `signal.SIGALRM` so it works on Windows).
- [ ] Multi-language support (R, Rust — OpenEvolve already supports them; just needs new compiler templates).

---

## Acknowledgements

Built as a project for **CS 153 (Stanford)**.

Heavy lifting by:

- [**OpenEvolve**](https://github.com/algorithmicsuperintelligence/openevolve) — the evolutionary engine under the hood.
- [**Ollama**](https://ollama.com/) — local LLM serving with an OpenAI-compatible API.
- [**Streamlit**](https://streamlit.io/) — the GUI.
