"""Visualizer for candidate lineage, scores, diffs, and failures.

Reads OpenEvolve's run output (checkpoints + `evolution_trace.jsonl`)
defensively and renders a text report. Format assumptions live in
``parse.py``; rendering lives in ``report.py``.
"""

from evolvestudio.visualizer.parse import (
    Checkpoint,
    RunOutput,
    TraceRow,
    parse_run,
)
from evolvestudio.visualizer.report import render, render_run

__all__ = [
    "Checkpoint",
    "RunOutput",
    "TraceRow",
    "parse_run",
    "render",
    "render_run",
]
