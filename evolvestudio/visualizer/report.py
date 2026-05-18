"""Text renderer for a parsed OpenEvolve run.

Stays defensive: every section is best-effort and degrades to a clear
"missing" / "no data" notice rather than crashing.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Dict, List, Optional

from evolvestudio.visualizer.parse import RunOutput, TraceRow, parse_run


_NON_FITNESS_METRICS = {"runtime_seconds", "complexity", "diversity"}


def _score_of(metrics: Optional[Dict[str, Any]]) -> Optional[float]:
    """Pick a single scalar from a metrics dict.

    Prefers ``combined_score``; falls back to averaging all numeric metrics
    other than known non-fitness ones (matches OpenEvolve's evaluator note).
    Returns ``None`` if no numeric value is available.
    """
    if not metrics:
        return None
    cs = metrics.get("combined_score")
    if isinstance(cs, (int, float)):
        return float(cs)
    nums = [
        float(v)
        for k, v in metrics.items()
        if k not in _NON_FITNESS_METRICS and isinstance(v, (int, float))
    ]
    return sum(nums) / len(nums) if nums else None


def _fmt_score(s: Optional[float]) -> str:
    return f"{s:.6f}" if s is not None else "n/a"


def _top_rows(rows: List[TraceRow], n: int) -> List[TraceRow]:
    scored = [(r, _score_of(r.child_metrics)) for r in rows]
    scored = [t for t in scored if t[1] is not None]
    scored.sort(key=lambda t: t[1], reverse=True)
    return [t[0] for t in scored[:n]]


def _diff_text(
    parent_code: Optional[str],
    child_code: Optional[str],
    code_diff: Optional[str],
    max_lines: int,
) -> str:
    if code_diff:
        lines = code_diff.splitlines()
    elif parent_code and child_code:
        lines = list(
            difflib.unified_diff(
                parent_code.splitlines(),
                child_code.splitlines(),
                fromfile="parent",
                tofile="child",
                lineterm="",
            )
        )
    else:
        return "  (no diff available)"
    if not lines:
        return "  (empty diff)"
    if len(lines) > max_lines:
        head = "\n".join(lines[:max_lines])
        return head + f"\n  ... ({len(lines) - max_lines} more lines)"
    return "\n".join(lines)


def _tail(path: Path, n_lines: int, max_bytes: int = 65536) -> str:
    """Read at most ``max_bytes`` of the tail of ``path``, return last ``n_lines``."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, 2)
                f.readline()
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        if n_lines and len(lines) > n_lines:
            lines = lines[-n_lines:]
        return "\n".join(lines)
    except Exception as e:
        return f"  (failed to read {path}: {e})"


def render(
    run: RunOutput,
    top_n: int = 5,
    max_diff_lines: int = 40,
    max_log_lines: int = 40,
) -> str:
    out: List[str] = []

    out.append(f"=== Run: {run.output_dir.name} ===")
    out.append(f"Path: {run.output_dir}")
    out.append("")

    if run.warnings:
        out.append("Warnings:")
        for w in run.warnings:
            out.append(f"  - {w}")
        out.append("")

    # --- Files found ---
    out.append("Files found:")
    out.append(f"  evolution_trace:   {run.evolution_trace_path or '(missing)'}")
    out.append(f"  trace rows:        {len(run.trace_rows)}")
    out.append(f"  best/:             {'yes' if (run.best_program_info or run.best_program_code) else '(missing)'}")
    out.append(f"  checkpoints:       {len(run.checkpoints)}")
    out.append(f"  logs/*.log:        {len(run.log_files)}")
    out.append(f"  stdout.log:        {'yes' if run.stdout_log else '(missing)'}")
    out.append(f"  stderr.log:        {'yes' if run.stderr_log else '(missing)'}")
    out.append("")

    # --- Best score ---
    if run.best_program_info:
        info = run.best_program_info
        score = _score_of(info.get("metrics"))
        out.append("Best program (from best/best_program_info.json):")
        out.append(f"  id:         {info.get('id')}")
        out.append(f"  generation: {info.get('generation')}")
        out.append(f"  iteration:  {info.get('iteration')}")
        out.append(f"  parent_id:  {info.get('parent_id')}")
        out.append(f"  score:      {_fmt_score(score)}")
        out.append(f"  metrics:    {info.get('metrics')}")
        out.append("")
    elif run.trace_rows:
        best_list = _top_rows(run.trace_rows, 1)
        if best_list:
            r = best_list[0]
            out.append("Best program (inferred from evolution_trace, best/ missing):")
            out.append(f"  child_id:   {r.child_id}")
            out.append(f"  iteration:  {r.iteration}")
            out.append(f"  score:      {_fmt_score(_score_of(r.child_metrics))}")
            out.append(f"  metrics:    {r.child_metrics}")
            out.append("")
    else:
        out.append("Best program: (no data)")
        out.append("")

    # --- Checkpoint progression ---
    if run.checkpoints:
        out.append("Checkpoint best-score progression:")
        for cp in run.checkpoints:
            info = cp.best_program_info or {}
            out.append(
                f"  iter {cp.iteration:>5}: score={_fmt_score(_score_of(info.get('metrics')))} "
                f"id={info.get('id')}"
            )
        out.append("")

    # --- Top candidates from trace ---
    if run.trace_rows:
        top = _top_rows(run.trace_rows, top_n)
        out.append(f"Top {len(top)} candidates (by combined_score / numeric-mean fallback):")
        for i, r in enumerate(top, 1):
            out.append(
                f"  [{i}] iter={r.iteration} id={r.child_id} parent={r.parent_id} "
                f"score={_fmt_score(_score_of(r.child_metrics))}"
            )
            out.append(f"      metrics: {r.child_metrics}")
            if r.child_changes_description:
                out.append(f"      change:  {r.child_changes_description}")
        out.append("")

        out.append(f"Diffs for top {len(top)} (truncated at {max_diff_lines} lines):")
        for i, r in enumerate(top, 1):
            out.append(f"--- candidate [{i}] iter={r.iteration} id={r.child_id} ---")
            out.append(_diff_text(r.parent_code, r.child_code, r.code_diff, max_diff_lines))
            out.append("")
    else:
        out.append("Candidate list: (no evolution_trace rows)")
        out.append("")

    # --- Logs ---
    if run.stdout_log:
        out.append(f"--- stdout.log (last {max_log_lines} lines) ---")
        out.append(_tail(run.stdout_log, max_log_lines))
        out.append("")
    if run.stderr_log:
        out.append(f"--- stderr.log (last {max_log_lines} lines) ---")
        out.append(_tail(run.stderr_log, max_log_lines))
        out.append("")
    for lp in run.log_files[:2]:
        out.append(f"--- logs/{lp.name} (last {max_log_lines} lines) ---")
        out.append(_tail(lp, max_log_lines))
        out.append("")

    return "\n".join(out)


def render_run(output_dir: Path, **kwargs: Any) -> str:
    """Parse + render in one call."""
    return render(parse_run(output_dir), **kwargs)
