"""EvolveStudio Streamlit GUI — sparse, three-tab layout.

Launch (from the project root):
    streamlit run evolvestudio/gui/app.py

Layout:
    Sidebar:   experiment picker + live run status (auto-refresh).
    Compose:   demo-load buttons, title + statement, paste the three files.
    Run:       iterations / python / output, Execute, Stop.
    Results:   sub-tabs Summary / Lineage & code (tree + code side-by-side) / Logs.

The GUI does not initiate any network calls. The only outbound traffic
comes from OpenEvolve itself, and only when you press Execute.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make project root importable regardless of where streamlit was launched.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # noqa: E402

from evolvestudio.compiler.euler import (  # noqa: E402
    EULER_1,
    compile_problem as compile_euler_problem,
)
from evolvestudio.compiler.unit_tests import (  # noqa: E402
    EDIT_DISTANCE_DEMO,
    SORT_DEMO,
    compile_problem as compile_unit_test_problem,
)
from evolvestudio.visualizer.parse import parse_run  # noqa: E402
from evolvestudio.visualizer.report import _diff_text, _score_of, _tail  # noqa: E402

GENERATED_ROOT = _PROJECT_ROOT / "generated_experiments"
OPENEVOLVE_SCRIPT = _PROJECT_ROOT / "third_party" / "openevolve" / "openevolve-run.py"
DEFAULT_PY = "/opt/anaconda3/envs/OpenEvolve/bin/python"


# ============================================================================
# Helpers
# ============================================================================


def _slugify(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "experiment"


def _list_experiments() -> list[Path]:
    if not GENERATED_ROOT.is_dir():
        return []
    return sorted(
        (p for p in GENERATED_ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _list_runs(exp_dir: Path) -> list[Path]:
    if not exp_dir.is_dir():
        return []
    return sorted(
        (p for p in exp_dir.iterdir() if p.is_dir() and p.name.startswith("run_")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _build_argv(
    exp_dir: Path, iterations: Optional[int], output_dir: Path, python_exe: str
) -> list[str]:
    argv = [
        python_exe,
        str(OPENEVOLVE_SCRIPT),
        str(exp_dir / "initial_program.py"),
        str(exp_dir / "evaluator.py"),
        "--config",
        str(exp_dir / "config.yaml"),
        "--output",
        str(output_dir),
    ]
    if iterations is not None:
        argv += ["--iterations", str(iterations)]
    return argv


def _save_experiment_from_text(
    slug: str,
    title: str,
    statement: str,
    initial: str,
    evaluator: str,
    config: str,
) -> Path:
    """Write user-pasted file contents directly. Bypasses the structured compiler."""
    exp_dir = GENERATED_ROOT / slug
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "initial_program.py").write_text(initial)
    (exp_dir / "evaluator.py").write_text(evaluator)
    (exp_dir / "config.yaml").write_text(config)
    meta = {
        "slug": slug,
        "title": title,
        "statement": statement,
        "compiler": "gui_paste",
        "compiler_version": "0.0.1",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "files": {
            "initial_program": "initial_program.py",
            "evaluator": "evaluator.py",
            "config": "config.yaml",
        },
    }
    (exp_dir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    return exp_dir


def _demo_files(kind: str) -> dict:
    """Return {title, statement, initial, evaluator, config} for a demo.

    The demo is materialized into a tempdir solely to capture the rendered file
    contents — nothing persists.
    """
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        if kind == "bubble_sort":
            exp_dir = compile_unit_test_problem(SORT_DEMO, td_path)
            title, statement = SORT_DEMO.title, SORT_DEMO.statement
        elif kind == "edit_distance":
            exp_dir = compile_unit_test_problem(EDIT_DISTANCE_DEMO, td_path)
            title, statement = EDIT_DISTANCE_DEMO.title, EDIT_DISTANCE_DEMO.statement
        elif kind == "euler":
            exp_dir = compile_euler_problem(EULER_1, td_path)
            title, statement = EULER_1.title, EULER_1.statement
        else:
            raise ValueError(f"unknown demo kind: {kind}")
        return {
            "title": title,
            "statement": statement,
            "initial": (exp_dir / "initial_program.py").read_text(),
            "evaluator": (exp_dir / "evaluator.py").read_text(),
            "config": (exp_dir / "config.yaml").read_text(),
        }


# ============================================================================
# Session state + subprocess management
# ============================================================================


def _init_state() -> None:
    defaults = {
        "selected_slug": None,
        # Compose-tab text fields:
        "compose_title": "",
        "compose_statement": "",
        "compose_initial": "",
        "compose_evaluator": "",
        "compose_config": "",
        # Run-tab subprocess state:
        "run_proc": None,
        "run_output_dir": None,
        "run_started_at": None,
        "run_returncode": None,
        "run_stopped_by_user": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _load_demo_callback(kind: str) -> None:
    """on_click callback for the demo buttons."""
    d = _demo_files(kind)
    st.session_state["compose_title"] = d["title"]
    st.session_state["compose_statement"] = d["statement"]
    st.session_state["compose_initial"] = d["initial"]
    st.session_state["compose_evaluator"] = d["evaluator"]
    st.session_state["compose_config"] = d["config"]


def _is_running() -> bool:
    proc = st.session_state.get("run_proc")
    return proc is not None and proc.poll() is None


def _launch_run(argv: list[str], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    stdout_f = stdout_path.open("w")
    stderr_f = stderr_path.open("w")
    proc = subprocess.Popen(
        argv,
        stdout=stdout_f,
        stderr=stderr_f,
        shell=False,
        start_new_session=True,
        text=True,
    )
    st.session_state["run_proc"] = proc
    st.session_state["run_output_dir"] = str(output_dir)
    st.session_state["run_started_at"] = time.time()
    st.session_state["run_returncode"] = None
    st.session_state["run_stopped_by_user"] = False


def _stop_run(grace: float = 5.0) -> str:
    proc = st.session_state.get("run_proc")
    if proc is None or proc.poll() is not None:
        return "no active run"
    st.session_state["run_stopped_by_user"] = True
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        rc = proc.wait(timeout=grace)
        return f"terminated rc={rc}"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except Exception:
                pass
        return "killed (SIGKILL)"


# ============================================================================
# Lineage DOT graph
# ============================================================================


def _score_color(score: Optional[float]) -> str:
    if score is None:
        return "#e0e0e0"
    s = max(0.0, min(1.0, float(score)))
    if s < 0.5:
        t = s / 0.5
        r = int(231 + t * (243 - 231))
        g = int(76 + t * (196 - 76))
        b = int(60 + t * (15 - 60))
    else:
        t = (s - 0.5) / 0.5
        r = int(243 + t * (46 - 243))
        g = int(196 + t * (204 - 196))
        b = int(15 + t * (113 - 15))
    return f"#{r:02x}{g:02x}{b:02x}"


def _build_lineage_dot(rows, best_id: Optional[str] = None) -> Optional[str]:
    if not rows:
        return None
    nodes: dict[str, dict] = {}
    edges: list[tuple[str, str]] = []
    for r in rows:
        if r.child_id:
            nodes.setdefault(
                r.child_id, {"score": _score_of(r.child_metrics), "iter": r.iteration}
            )
        if r.parent_id and r.parent_id not in nodes:
            nodes[r.parent_id] = {"score": _score_of(r.parent_metrics), "iter": None}
        if r.parent_id and r.child_id:
            edges.append((r.parent_id, r.child_id))
    winning: set[str] = set()
    if best_id and best_id in nodes:
        parent_of: dict[str, str] = {
            r.child_id: r.parent_id for r in rows if r.child_id and r.parent_id
        }
        cur: Optional[str] = best_id
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            winning.add(cur)
            cur = parent_of.get(cur)
    lines = [
        "digraph G {",
        "  rankdir=TB;",
        '  bgcolor="transparent";',
        '  node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10];',
        '  edge [color="#888888"];',
    ]
    for nid, attrs in nodes.items():
        score = attrs["score"]
        fill = _score_color(score)
        parts = [nid[:8]]
        if attrs["iter"] is not None:
            parts.append(f"iter {attrs['iter']}")
        if score is not None:
            parts.append(f"score {score:.3f}")
        label = "\\n".join(parts)
        on_path = nid in winning
        penwidth = "3" if on_path else "1"
        border = "#1a1a1a" if on_path else "#666666"
        lines.append(
            f'  "{nid}" [label="{label}" fillcolor="{fill}" '
            f"penwidth={penwidth} color=\"{border}\"];"
        )
    for p, c in edges:
        on_path = p in winning and c in winning
        penwidth = "3" if on_path else "1"
        color = "#1a1a1a" if on_path else "#888888"
        lines.append(f'  "{p}" -> "{c}" [penwidth={penwidth} color="{color}"];')
    lines.append("}")
    return "\n".join(lines)


def _find_candidate_row(rows, target_id: str):
    """Return (row, position) where position is 'child' or 'parent'."""
    for r in rows:
        if r.child_id == target_id:
            return r, "child"
    for r in rows:
        if r.parent_id == target_id:
            return r, "parent"
    return None, None


# ============================================================================
# Sub-tab renderers
# ============================================================================


def _render_summary(run) -> None:
    if run.warnings:
        st.warning("\n".join(f"- {w}" for w in run.warnings))

    c1, c2, c3 = st.columns(3)
    c1.metric("iterations recorded", len(run.trace_rows))
    c2.metric("checkpoints", len(run.checkpoints))
    n_logs = len(run.log_files) + int(bool(run.stdout_log)) + int(bool(run.stderr_log))
    c3.metric("log files", n_logs)

    st.write("")

    if run.best_program_info:
        info = run.best_program_info
        score = _score_of(info.get("metrics"))
        st.markdown("#### Best program")
        bc = st.columns(4)
        bc[0].metric("score", f"{score:.4f}" if score is not None else "n/a")
        bc[1].metric("iter", str(info.get("iteration", "?")))
        bc[2].metric("gen", str(info.get("generation", "?")))
        bc[3].metric("id", str(info.get("id", "?"))[:10])
        if run.best_program_code:
            with st.expander("Source", expanded=False):
                st.code(run.best_program_code, language="python")
    else:
        st.caption("Best program will appear after the first checkpoint (every 5 iterations by default).")

    if run.checkpoints:
        st.markdown("#### Score over time")
        rows = [
            {
                "iter": cp.iteration,
                "score": _score_of((cp.best_program_info or {}).get("metrics")),
            }
            for cp in run.checkpoints
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_tree_and_code(run) -> None:
    if not run.trace_rows:
        st.info("No iterations yet — the tree appears as soon as OpenEvolve finishes its first iteration.")
        return

    best_id = (run.best_program_info or {}).get("id")
    if not best_id:
        scored = [(r.child_id, _score_of(r.child_metrics)) for r in run.trace_rows if r.child_id]
        scored = [(i, s) for i, s in scored if s is not None]
        if scored:
            scored.sort(key=lambda t: t[1], reverse=True)
            best_id = scored[0][0]

    # Collect all unique candidate IDs that appear in the trace.
    candidates: dict[str, dict] = {}
    for r in run.trace_rows:
        if r.child_id and r.child_id not in candidates:
            candidates[r.child_id] = {
                "id": r.child_id,
                "iter": r.iteration,
                "score": _score_of(r.child_metrics),
            }
        if r.parent_id and r.parent_id not in candidates:
            candidates[r.parent_id] = {
                "id": r.parent_id,
                "iter": None,
                "score": _score_of(r.parent_metrics),
            }

    left, right = st.columns([1, 1], gap="medium")

    with left:
        dot = _build_lineage_dot(run.trace_rows, best_id=best_id)
        if dot:
            st.graphviz_chart(dot, use_container_width=True)
            st.caption(
                "Color: score (red→green). Bold path: ancestry of the best program."
            )

    with right:
        st.markdown("##### Inspect a node")
        ordered = sorted(
            candidates.values(),
            key=lambda c: (c["score"] if c["score"] is not None else -1.0),
            reverse=True,
        )

        def _fmt(c: dict) -> str:
            it = f"iter {c['iter']:>3}" if c["iter"] is not None else " root   "
            sc = f"score {c['score']:.3f}" if c["score"] is not None else "score n/a  "
            return f"{c['id'][:10]} | {it} | {sc}"

        opts = [_fmt(c) for c in ordered]
        lookup = {_fmt(c): c["id"] for c in ordered}

        if not opts:
            st.caption("No candidates available yet.")
            return

        # Default to the best id.
        default_idx = 0
        if best_id:
            for i, c in enumerate(ordered):
                if c["id"] == best_id:
                    default_idx = i
                    break

        pick = st.selectbox(
            "Node",
            opts,
            index=default_idx,
            label_visibility="collapsed",
            help="Pick any node from the tree on the left. The code and diff update instantly below.",
        )
        target = lookup[pick]
        row, position = _find_candidate_row(run.trace_rows, target)
        if row is None:
            st.caption("(node not found in trace rows)")
            return

        if position == "child":
            score = _score_of(row.child_metrics)
            mc = st.columns(3)
            mc[0].metric("score", f"{score:.4f}" if score is not None else "n/a")
            mc[1].metric("iter", str(row.iteration))
            mc[2].metric("parent", (row.parent_id or "?")[:10])
            if row.child_changes_description:
                st.caption(f"_{row.child_changes_description}_")
            tab_code, tab_diff = st.tabs(["Code", "Diff vs parent"])
            with tab_code:
                st.code(row.child_code or "(no code stored in trace)", language="python")
            with tab_diff:
                st.code(
                    _diff_text(row.parent_code, row.child_code, row.code_diff, 80),
                    language="diff",
                )
        else:
            score = _score_of(row.parent_metrics)
            mc = st.columns(2)
            mc[0].metric("score", f"{score:.4f}" if score is not None else "n/a")
            mc[1].metric("role", "root (initial)")
            st.code(row.parent_code or "(no code stored in trace)", language="python")


def _render_logs(run) -> None:
    labels: list[str] = []
    paths: list[Path] = []
    if run.stdout_log:
        labels.append("stdout.log")
        paths.append(run.stdout_log)
    if run.stderr_log:
        labels.append("stderr.log")
        paths.append(run.stderr_log)
    for lp in run.log_files:
        labels.append(f"logs/{lp.name}")
        paths.append(lp)
    if not labels:
        st.info("No logs yet.")
        return
    n_lines = st.slider("Tail lines", 10, 1000, 100)
    tabs = st.tabs(labels)
    for t, p in zip(tabs, paths):
        with t:
            st.code(_tail(p, int(n_lines)) or "(empty)")


# ============================================================================
# App
# ============================================================================


st.set_page_config(
    page_title="EvolveStudio",
    layout="wide",
    initial_sidebar_state="expanded",
)
_init_state()

st.title("EvolveStudio")
st.caption("Local workbench for OpenEvolve. Compose → Run → Inspect. Everything is local; the only outbound traffic is the LLM call inside an Execute.")


# ---------- Sidebar ----------

with st.sidebar:
    st.markdown("### Experiment")
    experiments = _list_experiments()
    if experiments:
        slugs = [p.name for p in experiments]
        cur = st.session_state.get("selected_slug")
        if cur not in slugs:
            cur = slugs[0]
        sel = st.selectbox(
            "Choose",
            slugs,
            index=slugs.index(cur),
            label_visibility="collapsed",
            key="sidebar_exp_sel",
        )
        st.session_state["selected_slug"] = sel
    else:
        st.caption("(none yet — Compose one)")
        st.session_state["selected_slug"] = None

    st.divider()

    st.markdown("### Run status")

    @st.fragment(run_every="2s")
    def _sidebar_status() -> None:
        proc = st.session_state.get("run_proc")
        if proc is None:
            st.caption("Idle.")
            return
        poll = proc.poll()
        started = st.session_state.get("run_started_at") or time.time()
        elapsed = time.time() - started
        if poll is None:
            st.success(f"Running ({elapsed:.0f}s)")
            if st.button(
                "Stop",
                key="sidebar_stop",
                use_container_width=True,
                help="SIGTERM the OpenEvolve process group; SIGKILL after 5s.",
            ):
                _stop_run()
        else:
            stopped = st.session_state.get("run_stopped_by_user")
            if stopped:
                st.warning(f"Stopped after {elapsed:.0f}s")
            elif poll == 0:
                st.success(f"Done in {elapsed:.0f}s")
            else:
                st.error(f"Failed (rc={poll}, {elapsed:.0f}s)")
        odir = st.session_state.get("run_output_dir")
        if odir:
            st.caption(f"`{Path(odir).name}`")

    _sidebar_status()


# ---------- Tabs ----------

tab_compose, tab_run, tab_results = st.tabs(["Compose", "Run", "Results"])


# ---------- Compose ----------

with tab_compose:
    st.markdown("### Compose an experiment")
    st.caption("Type a problem, paste the three OpenEvolve input files, save.")

    st.markdown("##### Quick start")
    cs = st.columns(3)
    cs[0].button(
        "Bubble sort",
        on_click=_load_demo_callback,
        args=("bubble_sort",),
        use_container_width=True,
        help="Classic bubble sort + 8 unit tests. Easy demo — the LLM often one-shots it.",
    )
    cs[1].button(
        "Edit distance",
        on_click=_load_demo_callback,
        args=("edit_distance",),
        use_container_width=True,
        help="Naive exponential recursion that times out on long inputs. Must evolve to memoization or DP.",
    )
    cs[2].button(
        "Euler #1",
        on_click=_load_demo_callback,
        args=("euler",),
        use_container_width=True,
        help="Sum of multiples of 3 or 5 below 1000. Single-answer problem.",
    )

    st.divider()

    st.text_input(
        "Title",
        key="compose_title",
        placeholder="A one-line name for the problem",
    )
    st.text_area(
        "Problem statement",
        key="compose_statement",
        height=110,
        placeholder="What should the program do? (Plain English — feeds the LLM's system prompt.)",
    )

    derived_slug = _slugify(st.session_state.get("compose_title", ""))
    st.caption(f"Will save as: `generated_experiments/{derived_slug}/`")

    st.markdown("##### Files")
    f1, f2, f3 = st.tabs(["initial_program.py", "evaluator.py", "config.yaml"])
    with f1:
        st.text_area(
            "initial_program",
            key="compose_initial",
            height=380,
            label_visibility="collapsed",
            placeholder="# Paste your starting program here, with EVOLVE-BLOCK markers around the part to evolve.",
        )
    with f2:
        st.text_area(
            "evaluator",
            key="compose_evaluator",
            height=380,
            label_visibility="collapsed",
            placeholder="# Paste your evaluator here. Must define evaluate(program_path) returning a dict or EvaluationResult.",
        )
    with f3:
        st.text_area(
            "config",
            key="compose_config",
            height=380,
            label_visibility="collapsed",
            placeholder="# Paste your OpenEvolve config YAML here.",
        )

    st.write("")
    can_save = bool(
        derived_slug
        and st.session_state.get("compose_initial")
        and st.session_state.get("compose_evaluator")
        and st.session_state.get("compose_config")
    )
    if st.button(
        "Save experiment",
        type="primary",
        use_container_width=True,
        disabled=not can_save,
        help="Writes the three files + metadata.json under generated_experiments/<slug>/. No network calls.",
    ):
        try:
            exp_dir = _save_experiment_from_text(
                derived_slug,
                st.session_state["compose_title"],
                st.session_state["compose_statement"],
                st.session_state["compose_initial"],
                st.session_state["compose_evaluator"],
                st.session_state["compose_config"],
            )
            st.session_state["selected_slug"] = exp_dir.name
            st.success(f"Saved to `{exp_dir}`. Open the Run tab to launch.")
        except Exception as e:
            st.error(f"Save failed: {e}")


# ---------- Run ----------

with tab_run:
    slug = st.session_state.get("selected_slug")
    if not slug:
        st.info("Compose an experiment first.")
    else:
        exp_dir = GENERATED_ROOT / slug
        st.markdown(f"### Run `{slug}`")
        st.caption(f"`{exp_dir}`")

        c1, c2 = st.columns(2)
        with c1:
            iterations = st.number_input(
                "Iterations",
                min_value=1,
                max_value=10_000,
                value=10,
                step=1,
                help="How many evolution rounds. Each = one LLM call + one evaluation.",
            )
        with c2:
            python_exe = st.text_input(
                "Python interpreter",
                value=DEFAULT_PY,
                help="Must have `openevolve` installed.",
            )

        default_out = exp_dir / f"run_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        output_override = st.text_input(
            "Output directory (blank = timestamped default)",
            value="",
            help="Where OpenEvolve writes evolution_trace.jsonl / best/ / checkpoints/.",
        )
        output_dir = Path(output_override) if output_override.strip() else default_out

        if not OPENEVOLVE_SCRIPT.exists():
            st.error(f"openevolve-run.py not found at {OPENEVOLVE_SCRIPT}")
        else:
            argv = _build_argv(exp_dir, int(iterations), output_dir, python_exe)

            with st.expander("Command preview (the literal shell command)"):
                st.code(shlex.join(argv), language="bash")

            running = _is_running()
            c_exec, c_stop = st.columns(2)
            if c_exec.button(
                "Execute OpenEvolve",
                type="primary",
                disabled=running,
                use_container_width=True,
                help="Launches a detached subprocess. Survives if you close this tab.",
            ):
                try:
                    _launch_run(argv, output_dir)
                    st.success(f"Launched. PID = {st.session_state['run_proc'].pid}")
                except Exception as e:
                    st.error(f"Launch failed: {e}")
            if c_stop.button(
                "Stop",
                disabled=not running,
                use_container_width=True,
                help="SIGTERM the process group; SIGKILL after 5s.",
            ):
                _stop_run()
                st.warning("Stop sent")

            st.caption(
                "Status updates in the sidebar (every 2s). Results auto-populate as files appear."
            )
            st.caption(
                "Prereqs: `ollama serve` running on :11434, `gpt-oss:20b` pulled."
            )


# ---------- Results ----------

with tab_results:
    slug = st.session_state.get("selected_slug")
    if not slug:
        st.info("Pick or compose an experiment first.")
    else:
        exp_dir = GENERATED_ROOT / slug
        runs = _list_runs(exp_dir)

        if not runs:
            st.info("No runs yet for this experiment.")
        else:
            run_names = [r.name for r in runs]
            default_idx = 0
            current = st.session_state.get("run_output_dir")
            if current:
                cn = Path(current).name
                if cn in run_names:
                    default_idx = run_names.index(cn)

            c_pick, c_auto = st.columns([3, 1])
            picked = c_pick.selectbox(
                "Run",
                run_names,
                index=default_idx,
                help="Most recent run first. Each Execute creates a fresh run_<timestamp>/ folder.",
            )
            auto = c_auto.checkbox(
                "Auto-refresh",
                value=True,
                help="Re-parses files every 3s while a run is in progress.",
            )
            chosen = exp_dir / picked
            refresh = "3s" if (auto and _is_running()) else None

            @st.fragment(run_every=refresh)
            def _results_fragment() -> None:
                run = parse_run(chosen)
                sub_summary, sub_tree, sub_logs = st.tabs(
                    ["Summary", "Lineage & code", "Logs"]
                )
                with sub_summary:
                    _render_summary(run)
                with sub_tree:
                    _render_tree_and_code(run)
                with sub_logs:
                    _render_logs(run)

            _results_fragment()
