"""Compiler for "implement a function, check it against unit tests" problems.

The generated experiment:
    - starts from a naive implementation of some target function,
    - is scored by the fraction of unit tests it passes,
    - applies a per-test timeout so slow implementations naturally fail
      large-input tests (creates implicit speed pressure),
    - exposes the function to OpenEvolve via EVOLVE-BLOCK markers.

Test case format (each entry):
    {"args": [<positional args, listed>], "expected": <expected return value>}

For a one-arg function `sort_list(lst)`, "args" wraps the single arg in a
one-element list:
    {"args": [[3, 1, 2]], "expected": [1, 2, 3]}

For a two-arg function `add(a, b)`:
    {"args": [2, 3], "expected": 5}

The compiler writes five files into ``<output_root>/<slug>/``:
    initial_program.py   Naive baseline with EVOLVE-BLOCK markers.
    evaluator.py         Defines ``evaluate(program_path)`` per OpenEvolve.
    config.yaml          Copy of the Ollama template with prompt adapted.
    metadata.json        Machine-readable manifest of the experiment.
    README.md            Human-readable summary + how-to-run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, List, Optional

_COMPILER_NAME = "unit_tests"
_COMPILER_VERSION = "0.0.1"

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_CONFIG_TEMPLATE = _TEMPLATES_DIR / "openevolve_ollama_gpt_oss_20b.yaml"


@dataclass
class UnitTestProblem:
    """A "implement a function, check it against tests" problem spec.

    Attributes:
        slug: Filesystem-safe id, used as the experiment directory name.
        title: One-line human title.
        statement: Natural-language problem statement.
        function_name: Name of the function the candidate must define.
        function_signature: Full `def ...:` line (without trailing colon? include it.).
            Example: ``"def sort_list(lst):"``.
        test_cases: List of ``{"args": [...], "expected": ...}`` dicts.
        baseline_code: Body of the function (no `def` line). Will be indented
            by 4 spaces when written to the file.
        imports: Optional import statements to add at the top of the file.
        per_test_timeout: Seconds before a single test is marked failed.
    """

    slug: str
    title: str
    statement: str
    function_name: str
    function_signature: str
    test_cases: List[dict]
    baseline_code: str
    imports: str = ""
    per_test_timeout: float = 2.0


_INITIAL_PROGRAM_TEMPLATE = Template(
    '''"""Initial program for ${slug}.

Problem: ${title}

OpenEvolve will rewrite the region between EVOLVE-BLOCK-START and
EVOLVE-BLOCK-END. The function `${function_name}` is the target.
"""
${imports_block}

# EVOLVE-BLOCK-START
${function_signature}
${baseline_body}
# EVOLVE-BLOCK-END
'''
)


_EVALUATOR_TEMPLATE = Template(
    '''"""Evaluator for ${slug}.

Loads the candidate, calls `${function_name}` on each test case under a
per-test wall-clock timeout, and scores by fraction of tests passed.

Timeout is enforced with a daemon thread + join(timeout): this works in
ANY thread (OpenEvolve evaluates candidates in worker threads, where
signal-based timeouts raise "signal only works in main thread"). On
timeout the worker thread is abandoned (it's a daemon, so it can't block
process exit) and the test is marked failed.
"""

import importlib.util
import threading
import time
import traceback

from openevolve.evaluation_result import EvaluationResult

FUNCTION_NAME = ${function_name_repr}
PER_TEST_TIMEOUT = ${per_test_timeout_repr}
TEST_CASES = ${test_cases_repr}


class _CandidateTimeout(Exception):
    pass


def _load_program(program_path):
    spec = importlib.util.spec_from_file_location("candidate_program", program_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_with_timeout(fn, args, timeout_s):
    """Run `fn(*args)` with a wall-clock budget. Thread-safe (no signals)."""
    box = {}

    def _target():
        try:
            box["value"] = fn(*args)
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(float(timeout_s))
    if t.is_alive():
        raise _CandidateTimeout()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def evaluate(program_path):
    total = len(TEST_CASES)
    try:
        module = _load_program(program_path)
    except Exception as e:
        return EvaluationResult(
            metrics={
                "combined_score": 0.0,
                "correctness": 0.0,
                "passed": 0.0,
                "total": float(total),
            },
            artifacts={
                "stage": "import",
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )

    fn = getattr(module, FUNCTION_NAME, None)
    if fn is None:
        return EvaluationResult(
            metrics={
                "combined_score": 0.0,
                "correctness": 0.0,
                "passed": 0.0,
                "total": float(total),
            },
            artifacts={"error": f"candidate program defines no `${function_name}` function"},
        )

    passed = 0
    first_failure = None
    t0 = time.perf_counter()
    for i, case in enumerate(TEST_CASES):
        args = case["args"]
        expected = case["expected"]
        try:
            got = _run_with_timeout(fn, args, PER_TEST_TIMEOUT)
        except _CandidateTimeout:
            if first_failure is None:
                first_failure = f"test #{i}: timed out after {PER_TEST_TIMEOUT}s on args={args!r}"
            continue
        except Exception as e:
            if first_failure is None:
                first_failure = f"test #{i}: raised {type(e).__name__}: {e} on args={args!r}"
            continue
        if got == expected:
            passed += 1
        elif first_failure is None:
            first_failure = f"test #{i}: expected={expected!r} got={got!r} (args={args!r})"
    runtime_s = time.perf_counter() - t0
    correctness = (passed / total) if total else 0.0

    artifacts = {
        "passed_summary": f"{passed}/{total}",
        "runtime_seconds": f"{runtime_s:.6f}",
    }
    if first_failure:
        artifacts["first_failure"] = first_failure

    return EvaluationResult(
        metrics={
            "combined_score": correctness,
            "correctness": correctness,
            "passed": float(passed),
            "total": float(total),
            "runtime_seconds": runtime_s,
        },
        artifacts=artifacts,
    )
'''
)


_README_TEMPLATE = Template(
    """# ${title}

Generated by EvolveStudio (compiler: `${compiler}` v${compiler_version}).

## Problem

${statement}

**Target function:** `${function_name}`
**Test cases:** ${num_tests}
**Per-test timeout:** ${per_test_timeout}s

## Scoring

`combined_score = correctness = passed_tests / total_tests`

A test fails if the candidate returns the wrong value, raises, or exceeds
the per-test timeout. The total wall time across all tests is reported as
`runtime_seconds` for informational purposes (it's not in the score, but
the timeout creates implicit speed pressure on the large-input tests).

## Files

| File | Purpose |
|------|---------|
| `initial_program.py` | Naive baseline implementation. Region between `EVOLVE-BLOCK-START` / `EVOLVE-BLOCK-END` is what OpenEvolve will rewrite. |
| `evaluator.py` | Defines `evaluate(program_path)` — loads candidate, runs each test, returns fraction passed. |
| `config.yaml` | OpenEvolve config (local Ollama `gpt-oss:20b`, demo-sized). |
| `metadata.json` | Machine-readable manifest. |

## Running

```bash
conda run -n OpenEvolve python third_party/openevolve/openevolve-run.py \\
  ${exp_path}/initial_program.py \\
  ${exp_path}/evaluator.py \\
  --config ${exp_path}/config.yaml \\
  --output ${exp_path}/run1
```
"""
)


def _indent(text: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())


def _adapt_config(template_text: str, problem: UnitTestProblem) -> str:
    """Adapt the Ollama config template's system_message for this problem."""
    new_system = (
        f"You are an expert Python programmer. Implement the function "
        f"`{problem.function_name}` so it passes the unit tests in `evaluator.py`. "
        f"Task: {problem.title}. Improve correctness first; faster implementations "
        f"are also rewarded because slow ones time out on large inputs."
    )
    new_line = f'  system_message: "{new_system}"\n'
    out_lines = []
    replaced = False
    for line in template_text.splitlines(keepends=True):
        if not replaced and line.lstrip().startswith("system_message:"):
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(line)
    return "".join(out_lines)


def compile_problem(problem: UnitTestProblem, output_root: Path) -> Path:
    """Materialize the experiment files for ``problem`` under ``output_root/<slug>``."""
    exp_dir = output_root / problem.slug
    exp_dir.mkdir(parents=True, exist_ok=True)

    initial_program = _INITIAL_PROGRAM_TEMPLATE.substitute(
        slug=problem.slug,
        title=problem.title,
        function_name=problem.function_name,
        function_signature=problem.function_signature,
        baseline_body=_indent(problem.baseline_code, 4),
        imports_block=(problem.imports.rstrip() + "\n") if problem.imports.strip() else "",
    )
    (exp_dir / "initial_program.py").write_text(initial_program)

    evaluator = _EVALUATOR_TEMPLATE.substitute(
        slug=problem.slug,
        function_name=problem.function_name,
        function_name_repr=repr(problem.function_name),
        per_test_timeout_repr=repr(problem.per_test_timeout),
        test_cases_repr=repr(problem.test_cases),
    )
    (exp_dir / "evaluator.py").write_text(evaluator)

    config_text = _adapt_config(_CONFIG_TEMPLATE.read_text(), problem)
    (exp_dir / "config.yaml").write_text(config_text)

    metadata = {
        "slug": problem.slug,
        "compiler": _COMPILER_NAME,
        "compiler_version": _COMPILER_VERSION,
        "title": problem.title,
        "statement": problem.statement,
        "function_name": problem.function_name,
        "function_signature": problem.function_signature,
        "num_tests": len(problem.test_cases),
        "per_test_timeout": problem.per_test_timeout,
        "test_cases": problem.test_cases,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "config_template": _CONFIG_TEMPLATE.name,
        "files": {
            "initial_program": "initial_program.py",
            "evaluator": "evaluator.py",
            "config": "config.yaml",
        },
    }
    (exp_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    readme = _README_TEMPLATE.substitute(
        title=problem.title,
        compiler=_COMPILER_NAME,
        compiler_version=_COMPILER_VERSION,
        statement=problem.statement,
        function_name=problem.function_name,
        num_tests=len(problem.test_cases),
        per_test_timeout=problem.per_test_timeout,
        exp_path=str(exp_dir),
    )
    (exp_dir / "README.md").write_text(readme)

    return exp_dir


# ---------------------------------------------------------------------------
# Hardcoded demo: sort a list of ints.
# ---------------------------------------------------------------------------

EDIT_DISTANCE_DEMO = UnitTestProblem(
    slug="edit_distance",
    title="Edit distance (Levenshtein)",
    statement=(
        "Implement edit_distance(s1, s2): given two strings, return the minimum number of "
        "single-character edits (insertions, deletions, or substitutions) required to "
        "transform s1 into s2. Also known as Levenshtein distance. The baseline is a naive "
        "exponential recursion that times out on long inputs — the goal is to evolve toward "
        "memoization or bottom-up dynamic programming."
    ),
    function_name="edit_distance",
    function_signature="def edit_distance(s1, s2):",
    test_cases=[
        # Easy cases — naive recursion handles instantly.
        {"args": ["", ""], "expected": 0},
        {"args": ["a", ""], "expected": 1},
        {"args": ["", "abc"], "expected": 3},
        {"args": ["abc", "abc"], "expected": 0},
        {"args": ["abc", "abd"], "expected": 1},
        {"args": ["kitten", "sitting"], "expected": 3},
        {"args": ["saturday", "sunday"], "expected": 3},
        {"args": ["hello", "world"], "expected": 4},
        {"args": ["intention", "execution"], "expected": 5},
        {"args": ["abcdefghij", "abcdefghijk"], "expected": 1},
        # Medium — still tractable for naive.
        {"args": ["aaaaaaaa", "bbbbbbbb"], "expected": 8},
        {"args": ["aaaaaaaaaaaa", "bbbbbbbbbbbb"], "expected": 12},
        # Stress — naive recursion times out (3^14 ≈ 4.8M calls, 3^16 ≈ 43M calls).
        {"args": ["aaaaaaaaaaaaaa", "bbbbbbbbbbbbbb"], "expected": 14},
        {"args": ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"], "expected": 16},
    ],
    baseline_code=(
        '"""Naive recursion. Correct but exponential.\n'
        'Evolve toward memoization or DP for speed.\n'
        'Note: the function calls itself by name; keep the name `edit_distance`."""\n'
        "if not s1:\n"
        "    return len(s2)\n"
        "if not s2:\n"
        "    return len(s1)\n"
        "if s1[0] == s2[0]:\n"
        "    return edit_distance(s1[1:], s2[1:])\n"
        "return 1 + min(\n"
        "    edit_distance(s1[1:], s2),     # delete from s1\n"
        "    edit_distance(s1, s2[1:]),     # insert into s1\n"
        "    edit_distance(s1[1:], s2[1:]), # substitute\n"
        ")\n"
    ),
    per_test_timeout=2.0,
)


SORT_DEMO = UnitTestProblem(
    slug="sort_integer_list",
    title="Sort a list of integers (ascending)",
    statement=(
        "Implement sort_list(lst): given a list of integers, return a new list with the same "
        "values in ascending order. The function must not mutate the input. Empty lists and "
        "lists with duplicates must be handled correctly."
    ),
    function_name="sort_list",
    function_signature="def sort_list(lst):",
    test_cases=[
        {"args": [[]], "expected": []},
        {"args": [[5]], "expected": [5]},
        {"args": [[1, 2, 3]], "expected": [1, 2, 3]},
        {"args": [[3, 1, 2]], "expected": [1, 2, 3]},
        {"args": [[5, 2, 8, 1, 9, 3]], "expected": [1, 2, 3, 5, 8, 9]},
        {"args": [[1, 1, 1]], "expected": [1, 1, 1]},
        {"args": [[10, -1, 0, -5]], "expected": [-5, -1, 0, 10]},
        {
            "args": [[20, 13, 7, 18, 1, 4, 16, 11, 9, 3, 15, 12, 8, 19, 6, 5, 17, 14, 2, 10]],
            "expected": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
        },
    ],
    baseline_code=(
        '"""Naive bubble sort. Correct but O(n^2). Evolve for speed."""\n'
        "out = list(lst)\n"
        "n = len(out)\n"
        "for i in range(n):\n"
        "    for j in range(n - i - 1):\n"
        "        if out[j] > out[j + 1]:\n"
        "            out[j], out[j + 1] = out[j + 1], out[j]\n"
        "return out\n"
    ),
    per_test_timeout=2.0,
)


def _default_output_root() -> Path:
    return Path(__file__).resolve().parents[2] / "generated_experiments"


def main() -> None:
    """Materialize the hardcoded bubble-sort demo into ``generated_experiments/``."""
    exp_dir = compile_problem(SORT_DEMO, _default_output_root())
    print(f"Wrote experiment to: {exp_dir}")


if __name__ == "__main__":
    main()
