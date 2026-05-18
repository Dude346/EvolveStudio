"""Natural-language -> OpenEvolve experiment compiler.

Takes a user-supplied problem description and emits the trio of files
OpenEvolve needs:
    - initial_program.py  (with `# EVOLVE-BLOCK-START / END` markers)
    - evaluator.py        (defines `evaluate(program_path) -> dict[str, float]`)
    - config.yaml         (LLM, database, evaluator settings)
plus metadata.json and README.md alongside them.

Output is written under `generated_experiments/<experiment_id>/`.
"""

from evolvestudio.compiler.euler import EulerProblem, compile_problem
from evolvestudio.compiler.unit_tests import (
    UnitTestProblem,
    compile_problem as compile_unit_test_problem,
)

__all__ = [
    "EulerProblem",
    "compile_problem",
    "UnitTestProblem",
    "compile_unit_test_problem",
]
