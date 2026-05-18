"""Compiler for Project Euler / numeric-programming style problems.

A Project Euler problem reduces to "compute a single number." The generated
experiment:
    - starts from a deliberately naive `solve()` implementation,
    - scores candidates by comparing `solve()`'s return value against a
      known expected answer (correctness) plus runtime,
    - exposes the function to OpenEvolve via EVOLVE-BLOCK markers.

The compiler writes five files into ``<output_root>/<slug>/``:
    initial_program.py   Naive baseline with EVOLVE-BLOCK markers.
    evaluator.py         Defines ``evaluate(program_path)`` per OpenEvolve.
    config.yaml          Copy of the Ollama template with prompt adapted.
    metadata.json        Machine-readable manifest of the experiment.
    README.md            Human-readable summary + how-to-run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Optional, Union

_COMPILER_NAME = "euler"
_COMPILER_VERSION = "0.0.1"

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_CONFIG_TEMPLATE = _TEMPLATES_DIR / "openevolve_ollama_gpt_oss_20b.yaml"


@dataclass
class EulerProblem:
    """A Project Euler-style problem spec.

    Attributes:
        slug: Filesystem-safe id, used as the experiment directory name.
        title: One-line human title.
        statement: Natural-language problem statement.
        expected_answer: Known numeric answer (if any). Used by the evaluator
            to score correctness. ``None`` means correctness can't be scored
            directly — not supported by this first compiler template.
        baseline_code: Body of the naive ``solve()`` to drop inside the
            EVOLVE-BLOCK. Should return a number.
    """

    slug: str
    title: str
    statement: str
    expected_answer: Optional[Union[int, float]]
    baseline_code: str


_INITIAL_PROGRAM_TEMPLATE = Template(
    '''"""Initial program for ${slug}.

Problem: ${title}

OpenEvolve will rewrite the region between EVOLVE-BLOCK-START and
EVOLVE-BLOCK-END. Everything outside that region is left untouched.
"""


# EVOLVE-BLOCK-START
def solve():
    """Naive baseline. Evolve this for better correctness/efficiency."""
${baseline_body}
# EVOLVE-BLOCK-END


if __name__ == "__main__":
    print(solve())
'''
)


_EVALUATOR_TEMPLATE = Template(
    '''"""Evaluator for ${slug}.

Loads the candidate program and calls ``solve()`` with a timeout. Scores
candidates on correctness (matches the known expected answer) and reports
runtime as an additional metric.
"""

import importlib.util
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from openevolve.evaluation_result import EvaluationResult

EXPECTED_ANSWER = ${expected_answer_repr}
SOLVE_TIMEOUT_SECONDS = 10.0


def _load_program(program_path):
    spec = importlib.util.spec_from_file_location("candidate_program", program_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_with_timeout(fn, timeout_s):
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(fn)
        return future.result(timeout=timeout_s)


def _score_correctness(answer):
    """1.0 for exact match, smooth fallback so the LLM sees it is close."""
    if answer == EXPECTED_ANSWER:
        return 1.0
    try:
        return 1.0 / (1.0 + abs(float(answer) - float(EXPECTED_ANSWER)))
    except (TypeError, ValueError):
        return 0.0


def evaluate(program_path):
    try:
        module = _load_program(program_path)
    except Exception as e:
        return EvaluationResult(
            metrics={"combined_score": 0.0, "correctness": 0.0},
            artifacts={
                "stage": "import",
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )

    if not hasattr(module, "solve"):
        return EvaluationResult(
            metrics={"combined_score": 0.0, "correctness": 0.0},
            artifacts={"error": "candidate program defines no `solve` function"},
        )

    try:
        t0 = time.perf_counter()
        answer = _run_with_timeout(module.solve, SOLVE_TIMEOUT_SECONDS)
        runtime_s = time.perf_counter() - t0
    except FuturesTimeout:
        return EvaluationResult(
            metrics={"combined_score": 0.0, "correctness": 0.0},
            artifacts={"stage": "execute", "error": "solve() timed out"},
        )
    except Exception as e:
        return EvaluationResult(
            metrics={"combined_score": 0.0, "correctness": 0.0},
            artifacts={
                "stage": "execute",
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
        )

    correctness = _score_correctness(answer)
    return EvaluationResult(
        metrics={
            "combined_score": correctness,
            "correctness": correctness,
            "runtime_seconds": runtime_s,
        },
        artifacts={
            "answer": str(answer),
            "expected": str(EXPECTED_ANSWER),
        },
    )
'''
)


_README_TEMPLATE = Template(
    """# ${title}

Generated by EvolveStudio (compiler: `${compiler}` v${compiler_version}).

## Problem

${statement}

**Expected answer:** `${expected_answer}`

## Files

| File | Purpose |
|------|---------|
| `initial_program.py` | Naive baseline. Region between `EVOLVE-BLOCK-START` / `EVOLVE-BLOCK-END` is what OpenEvolve will rewrite. |
| `evaluator.py` | Defines `evaluate(program_path)` — loads the candidate, calls `solve()`, scores correctness vs the expected answer. |
| `config.yaml` | OpenEvolve config (local Ollama `gpt-oss:20b`, demo-sized). |
| `metadata.json` | Machine-readable manifest. |

## Running (once OpenEvolve is installed)

```bash
conda run -n OpenEvolve python third_party/openevolve/openevolve-run.py \\
  ${exp_path}/initial_program.py \\
  ${exp_path}/evaluator.py \\
  --config ${exp_path}/config.yaml \\
  --output ${exp_path}/run1
```

Make sure `ollama serve` is running and `ollama pull gpt-oss:20b` has finished first.
"""
)


def _indent(text: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())


def _adapt_config(template_text: str, problem: EulerProblem) -> str:
    """Adapt the Ollama config template for a specific problem.

    Only swaps the ``prompt.system_message`` line. Everything else is left
    untouched so the template stays the source of truth for evolution knobs.
    """
    new_system = (
        f"You are an expert Python programmer optimizing a Project Euler-style "
        f"numeric program. Task: {problem.title}. Improve the `solve()` function "
        f"for correctness and efficiency; it must return the single integer answer."
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


def compile_problem(problem: EulerProblem, output_root: Path) -> Path:
    """Materialize the experiment files for ``problem`` under ``output_root/<slug>``.

    Returns the experiment directory path.
    """
    exp_dir = output_root / problem.slug
    exp_dir.mkdir(parents=True, exist_ok=True)

    initial_program = _INITIAL_PROGRAM_TEMPLATE.substitute(
        slug=problem.slug,
        title=problem.title,
        baseline_body=_indent(problem.baseline_code, 4),
    )
    (exp_dir / "initial_program.py").write_text(initial_program)

    evaluator = _EVALUATOR_TEMPLATE.substitute(
        slug=problem.slug,
        expected_answer_repr=repr(problem.expected_answer),
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
        "expected_answer": problem.expected_answer,
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
        expected_answer=problem.expected_answer,
        exp_path=str(exp_dir),
    )
    (exp_dir / "README.md").write_text(readme)

    return exp_dir


# ---------------------------------------------------------------------------
# Hardcoded demo problem: Project Euler #1.
# ---------------------------------------------------------------------------

EULER_1 = EulerProblem(
    slug="euler_001_multiples_3_5",
    title="Sum of multiples of 3 or 5 below 1000",
    statement=(
        "If we list all the natural numbers below 10 that are multiples of 3 or 5, "
        "we get 3, 5, 6 and 9. The sum of these multiples is 23. "
        "Find the sum of all the multiples of 3 or 5 below 1000."
    ),
    expected_answer=233168,
    baseline_code=(
        "total = 0\n"
        "for n in range(1000):\n"
        "    if n % 3 == 0 or n % 5 == 0:\n"
        "        total += n\n"
        "return total\n"
    ),
)


def _default_output_root() -> Path:
    return Path(__file__).resolve().parents[2] / "generated_experiments"


def main() -> None:
    """Materialize the hardcoded Euler #1 demo into ``generated_experiments/``."""
    exp_dir = compile_problem(EULER_1, _default_output_root())
    print(f"Wrote experiment to: {exp_dir}")


if __name__ == "__main__":
    main()
