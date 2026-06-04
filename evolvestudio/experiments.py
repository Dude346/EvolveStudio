"""Shared, Streamlit-free helpers for experiments.

Single source of truth for: where experiments live on disk, how to save one
from pasted text, how to list/read them, how to build the OpenEvolve argv,
and how to render the bundled demos. Imported by both the API server
(`evolvestudio/server`) and the Streamlit GUI / CLI.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from evolvestudio.compiler.euler import (
    EULER_1,
    compile_problem as compile_euler_problem,
)
from evolvestudio.compiler.unit_tests import (
    EDIT_DISTANCE_DEMO,
    SORT_DEMO,
    compile_problem as compile_unit_test_problem,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOT = PROJECT_ROOT / "generated_experiments"
OPENEVOLVE_SCRIPT = PROJECT_ROOT / "third_party" / "openevolve" / "openevolve-run.py"
DEFAULT_PY = "/opt/anaconda3/envs/OpenEvolve/bin/python"

# Demo registry: kind -> (problem spec, compiler fn)
DEMOS = {
    "bubble_sort": (SORT_DEMO, compile_unit_test_problem),
    "edit_distance": (EDIT_DISTANCE_DEMO, compile_unit_test_problem),
    "euler": (EULER_1, compile_euler_problem),
}


def slugify(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "experiment"


def list_experiments() -> list[Path]:
    if not GENERATED_ROOT.is_dir():
        return []
    return sorted(
        (p for p in GENERATED_ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def experiment_summaries() -> list[dict]:
    """[{slug, title, statement}] for every experiment, newest first."""
    out = []
    for exp_dir in list_experiments():
        meta = _read_metadata(exp_dir)
        out.append(
            {
                "slug": exp_dir.name,
                "title": meta.get("title", exp_dir.name),
                "statement": meta.get("statement", ""),
            }
        )
    return out


def list_runs(exp_dir: Path) -> list[Path]:
    if not exp_dir.is_dir():
        return []
    return sorted(
        (p for p in exp_dir.iterdir() if p.is_dir() and p.name.startswith("run_")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def build_argv(
    exp_dir: Path,
    iterations: Optional[int],
    output_dir: Path,
    python_exe: str,
    target_score: Optional[float] = None,
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
    if target_score is not None:
        # OpenEvolve stops as soon as a candidate's combined_score >= target.
        argv += ["--target-score", str(target_score)]
    return argv


def default_output_dir(exp_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return exp_dir / f"run_{ts}"


def save_experiment_from_text(
    title: str,
    statement: str,
    initial: str,
    evaluator: str,
    config: str,
    slug: Optional[str] = None,
) -> Path:
    """Write pasted file contents directly. Returns the experiment dir."""
    slug = slugify(slug or title)
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


def set_experiment_model(slug: str, model: str) -> bool:
    """Rewrite the `primary_model:` line in an experiment's config.yaml.

    Used to set the evolution-loop model just before a run. Returns True if a
    line was rewritten.
    """
    cfg = GENERATED_ROOT / slug / "config.yaml"
    if not cfg.is_file() or not model:
        return False
    out, replaced = [], False
    for line in cfg.read_text().splitlines(keepends=True):
        if not replaced and line.lstrip().startswith("primary_model:"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f'{indent}primary_model: "{model}"\n')
            replaced = True
        else:
            out.append(line)
    if replaced:
        cfg.write_text("".join(out))
    return replaced


def get_experiment_model(slug: str) -> Optional[str]:
    """Read the `primary_model:` from an experiment's config.yaml (or None)."""
    cfg = GENERATED_ROOT / slug / "config.yaml"
    if not cfg.is_file():
        return None
    for line in cfg.read_text().splitlines():
        if line.lstrip().startswith("primary_model:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _read_metadata(exp_dir: Path) -> dict:
    p = exp_dir / "metadata.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _safe_read(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def read_experiment(slug: str) -> Optional[dict]:
    """Return {slug, title, statement, files:{initial_program, evaluator, config}}.

    None if the experiment dir does not exist.
    """
    exp_dir = GENERATED_ROOT / slug
    if not exp_dir.is_dir():
        return None
    meta = _read_metadata(exp_dir)
    return {
        "slug": slug,
        "title": meta.get("title", slug),
        "statement": meta.get("statement", ""),
        "path": str(exp_dir),
        "files": {
            "initial_program": _safe_read(exp_dir / "initial_program.py"),
            "evaluator": _safe_read(exp_dir / "evaluator.py"),
            "config": _safe_read(exp_dir / "config.yaml"),
        },
    }


def demo_files(kind: str) -> dict:
    """Render a bundled demo to text without persisting it.

    Returns {title, statement, initial_program, evaluator, config}.
    """
    if kind not in DEMOS:
        raise ValueError(f"unknown demo kind: {kind!r}")
    problem, compile_fn = DEMOS[kind]
    with tempfile.TemporaryDirectory() as td:
        exp_dir = compile_fn(problem, Path(td))
        return {
            "title": problem.title,
            "statement": problem.statement,
            "initial_program": (exp_dir / "initial_program.py").read_text(),
            "evaluator": (exp_dir / "evaluator.py").read_text(),
            "config": (exp_dir / "config.yaml").read_text(),
        }
