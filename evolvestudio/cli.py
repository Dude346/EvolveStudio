"""Command-line entry point for EvolveStudio.

Subcommands:
    generate-euler-demo   Materialize the hardcoded Project Euler #1 demo.
    review <exp_dir>      Print the metadata + generated files for review.
    evaluate <exp_dir>    Run evaluator.py on initial_program.py in a
                          subprocess (shell=False) with a timeout. Requires
                          a Python interpreter where `openevolve` is
                          importable (see --python).
    run-openevolve <exp_dir>
                          Prepare (and optionally execute) the OpenEvolve
                          CLI invocation for an experiment. Dry-run by
                          default; pass --execute to actually launch.
    view <output_dir>     Defensively parse and render the result of an
                          OpenEvolve run.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import IO, Optional

from evolvestudio.compiler.euler import EULER_1, _default_output_root, compile_problem
from evolvestudio.compiler.unit_tests import (
    SORT_DEMO,
    compile_problem as compile_unit_test_problem,
)


# ---------------------------------------------------------------------------
# generate-euler-demo
# ---------------------------------------------------------------------------


def cmd_generate_euler_demo(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root) if args.output_root else _default_output_root()
    exp_dir = compile_problem(EULER_1, output_root)
    print(f"Wrote experiment to: {exp_dir}")
    return 0


# ---------------------------------------------------------------------------
# generate-sort-demo
# ---------------------------------------------------------------------------


def cmd_generate_sort_demo(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root) if args.output_root else _default_output_root()
    exp_dir = compile_unit_test_problem(SORT_DEMO, output_root)
    print(f"Wrote experiment to: {exp_dir}")
    return 0


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _system_message_from_config(config_text: str) -> Optional[str]:
    for line in config_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("system_message:"):
            return stripped[len("system_message:"):].strip().strip('"')
    return None


def cmd_review(args: argparse.Namespace) -> int:
    exp_dir = Path(args.experiment_dir).resolve()
    if not exp_dir.is_dir():
        print(f"Error: not a directory: {exp_dir}", file=sys.stderr)
        return 2

    metadata_path = exp_dir / "metadata.json"
    config_path = exp_dir / "config.yaml"
    initial_path = exp_dir / "initial_program.py"
    evaluator_path = exp_dir / "evaluator.py"
    readme_path = exp_dir / "README.md"

    print(f"=== Experiment: {exp_dir.name} ===")
    print(f"Path: {exp_dir}\n")

    if metadata_path.exists():
        meta = json.loads(metadata_path.read_text())
        print("Metadata:")
        print(f"  Title:           {meta.get('title')}")
        print(f"  Slug:            {meta.get('slug')}")
        print(f"  Compiler:        {meta.get('compiler')} v{meta.get('compiler_version')}")
        print(f"  Expected answer: {meta.get('expected_answer')}")
        print(f"  Generated at:    {meta.get('generated_at')}")
        print(f"  Statement: {meta.get('statement')}\n")
    else:
        print("(no metadata.json found)\n")

    files = [
        ("initial_program.py", initial_path),
        ("evaluator.py", evaluator_path),
        ("config.yaml", config_path),
        ("metadata.json", metadata_path),
        ("README.md", readme_path),
    ]
    print("Files:")
    for name, path in files:
        if path.exists():
            lines = sum(1 for _ in path.read_text().splitlines())
            print(f"  {name:<22} {lines:>4} lines   {path.stat().st_size:>6} bytes")
        else:
            print(f"  {name:<22}   (missing)")
    print()

    if config_path.exists():
        sysmsg = _system_message_from_config(config_path.read_text())
        if sysmsg:
            print("Config prompt.system_message:")
            print(f"  {sysmsg}\n")

    if initial_path.exists():
        print("--- initial_program.py ---")
        print(_read_text(initial_path))

    if evaluator_path.exists():
        print("--- evaluator.py ---")
        print(_read_text(evaluator_path))

    return 0


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


_EVALUATE_BOOTSTRAP = r"""
import importlib.util
import json
import sys
import traceback

evaluator_path = sys.argv[1]
program_path = sys.argv[2]

try:
    spec = importlib.util.spec_from_file_location("evolvestudio_evaluator", evaluator_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
except Exception:
    print(json.dumps({"_error": "failed to import evaluator", "_traceback": traceback.format_exc()}))
    sys.exit(11)

try:
    result = module.evaluate(program_path)
except Exception:
    print(json.dumps({"_error": "evaluate() raised", "_traceback": traceback.format_exc()}))
    sys.exit(12)

def _coerce_artifact(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, bytes):
        return f"<bytes len={len(v)}>"
    return str(v)

if hasattr(result, "metrics") and hasattr(result, "artifacts"):
    payload = {
        "metrics": dict(result.metrics),
        "artifacts": {k: _coerce_artifact(v) for k, v in dict(result.artifacts).items()},
    }
else:
    try:
        payload = {"metrics": dict(result), "artifacts": {}}
    except Exception:
        payload = {"_error": "evaluate() returned unrecognized type", "_repr": repr(result)}

print(json.dumps(payload, indent=2, default=str))
"""


def cmd_evaluate(args: argparse.Namespace) -> int:
    exp_dir = Path(args.experiment_dir).resolve()
    evaluator_path = exp_dir / "evaluator.py"
    program_path = exp_dir / "initial_program.py"

    for label, path in (("evaluator.py", evaluator_path), ("initial_program.py", program_path)):
        if not path.is_file():
            print(f"Error: {label} not found at {path}", file=sys.stderr)
            return 2

    python_exe = args.python or sys.executable
    timeout_s = args.timeout

    print(f"Running evaluator (timeout: {timeout_s}s, shell=False)")
    print(f"  python:    {python_exe}")
    print(f"  evaluator: {evaluator_path}")
    print(f"  program:   {program_path}\n")

    argv = [
        python_exe,
        "-c",
        _EVALUATE_BOOTSTRAP,
        str(evaluator_path),
        str(program_path),
    ]

    try:
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        print(f"Subprocess timed out after {timeout_s}s.", file=sys.stderr)
        return 124

    if completed.stdout:
        print("--- stdout ---")
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print("--- stderr ---", file=sys.stderr)
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)

    print(f"\nExit code: {completed.returncode}")
    return completed.returncode


# ---------------------------------------------------------------------------
# run-openevolve
# ---------------------------------------------------------------------------


def _openevolve_run_script() -> Path:
    """Path to third_party/openevolve/openevolve-run.py (project-rooted)."""
    project_root = Path(__file__).resolve().parents[1]
    return project_root / "third_party" / "openevolve" / "openevolve-run.py"


def cmd_run_openevolve(args: argparse.Namespace) -> int:
    exp_dir = Path(args.experiment_dir).resolve()
    initial_path = exp_dir / "initial_program.py"
    evaluator_path = exp_dir / "evaluator.py"
    config_path = exp_dir / "config.yaml"

    for label, path in (
        ("initial_program.py", initial_path),
        ("evaluator.py", evaluator_path),
        ("config.yaml", config_path),
    ):
        if not path.is_file():
            print(f"Error: {label} not found at {path}", file=sys.stderr)
            return 2

    openevolve_script = _openevolve_run_script()
    if not openevolve_script.is_file():
        print(
            f"Error: openevolve-run.py not found at {openevolve_script}.\n"
            "Make sure third_party/openevolve has been cloned.",
            file=sys.stderr,
        )
        return 2

    if args.output:
        output_dir = Path(args.output).resolve()
    else:
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        output_dir = exp_dir / f"run_{ts}"

    python_exe = args.python or sys.executable

    argv = [
        python_exe,
        str(openevolve_script),
        str(initial_path),
        str(evaluator_path),
        "--config",
        str(config_path),
        "--output",
        str(output_dir),
    ]
    if args.iterations is not None:
        argv += ["--iterations", str(args.iterations)]

    dry_run = not args.execute

    print("=== run-openevolve ===")
    print(f"Experiment: {exp_dir}")
    print(f"Output:     {output_dir}")
    print(f"Mode:       {'DRY-RUN (print only)' if dry_run else 'EXECUTE'}\n")
    print("Command:")
    print(f"  {shlex.join(argv)}\n")

    if dry_run:
        print("Prerequisites before --execute:")
        print("  - `ollama serve` must be running on localhost:11434.")
        print("  - `ollama pull gpt-oss:20b` must have completed.")
        print(
            "  - Pass `--python /opt/anaconda3/envs/OpenEvolve/bin/python` so the "
            "interpreter has `openevolve` installed."
        )
        print("\nRe-run with `--execute` to actually start the evolution.")
        return 0

    print("Executing OpenEvolve (this will block until it finishes)...\n")
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    print(f"Teeing stdout -> {stdout_path}")
    print(f"Teeing stderr -> {stderr_path}\n")
    with stdout_path.open("w") as f_out, stderr_path.open("w") as f_err:
        return _run_and_tee(argv, f_out, f_err)


def _pump(pipe: IO[str], file_obj: IO[str], mirror: IO[str]) -> None:
    """Forward every line on `pipe` to both `file_obj` and `mirror`."""
    try:
        for line in iter(pipe.readline, ""):
            file_obj.write(line)
            file_obj.flush()
            mirror.write(line)
            mirror.flush()
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _run_and_tee(argv: list[str], stdout_file: IO[str], stderr_file: IO[str]) -> int:
    """Run argv with shell=False, mirror stdout/stderr to both files and terminal."""
    proc = subprocess.Popen(
        argv,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    threads = [
        threading.Thread(target=_pump, args=(proc.stdout, stdout_file, sys.stdout)),
        threading.Thread(target=_pump, args=(proc.stderr, stderr_file, sys.stderr)),
    ]
    for t in threads:
        t.daemon = True
        t.start()
    rc = proc.wait()
    for t in threads:
        t.join(timeout=5)
    return rc


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------


def cmd_view(args: argparse.Namespace) -> int:
    from evolvestudio.visualizer.report import render_run

    output_dir = Path(args.output_dir)
    print(
        render_run(
            output_dir,
            top_n=args.top,
            max_diff_lines=args.max_diff_lines,
            max_log_lines=args.max_log_lines,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolvestudio", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser(
        "generate-euler-demo",
        help="Materialize the hardcoded Project Euler #1 demo experiment.",
    )
    p_gen.add_argument(
        "--output-root",
        default=None,
        help="Directory to write the experiment under (default: ./generated_experiments).",
    )
    p_gen.set_defaults(func=cmd_generate_euler_demo)

    p_sort = sub.add_parser(
        "generate-sort-demo",
        help="Materialize the bubble-sort + unit-tests demo experiment.",
    )
    p_sort.add_argument(
        "--output-root",
        default=None,
        help="Directory to write the experiment under (default: ./generated_experiments).",
    )
    p_sort.set_defaults(func=cmd_generate_sort_demo)

    p_review = sub.add_parser(
        "review",
        help="Print metadata + generated files for an experiment directory.",
    )
    p_review.add_argument("experiment_dir", help="Path to an experiment directory.")
    p_review.set_defaults(func=cmd_review)

    p_eval = sub.add_parser(
        "evaluate",
        help="Run evaluator.py on initial_program.py in a subprocess (shell=False).",
    )
    p_eval.add_argument("experiment_dir", help="Path to an experiment directory.")
    p_eval.add_argument(
        "--python",
        default=None,
        help="Python interpreter to invoke (default: current sys.executable). "
        "Must have `openevolve` importable.",
    )
    p_eval.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Subprocess timeout in seconds (default: 60).",
    )
    p_eval.set_defaults(func=cmd_evaluate)

    p_run = sub.add_parser(
        "run-openevolve",
        help="Prepare (or execute) the OpenEvolve invocation for an experiment.",
    )
    p_run.add_argument("experiment_dir", help="Path to an experiment directory.")
    p_run.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Override config's max_iterations.",
    )
    p_run.add_argument(
        "--output",
        default=None,
        help="Output directory (default: <experiment_dir>/run_<timestamp>).",
    )
    p_run.add_argument(
        "--python",
        default=None,
        help="Python interpreter to invoke (default: current sys.executable). "
        "Must have `openevolve` importable.",
    )
    mode = p_run.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command only (this is the default behavior).",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute the OpenEvolve command.",
    )
    p_run.set_defaults(func=cmd_run_openevolve)

    p_view = sub.add_parser(
        "view",
        help="Defensively parse and render an OpenEvolve run output directory.",
    )
    p_view.add_argument("output_dir", help="Path to an OpenEvolve run output directory.")
    p_view.add_argument(
        "--top",
        type=int,
        default=5,
        help="Number of top candidates to list and diff (default: 5).",
    )
    p_view.add_argument(
        "--max-diff-lines",
        type=int,
        default=40,
        help="Max diff lines per candidate (default: 40).",
    )
    p_view.add_argument(
        "--max-log-lines",
        type=int,
        default=40,
        help="Max log tail lines per log file (default: 40).",
    )
    p_view.set_defaults(func=cmd_view)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
