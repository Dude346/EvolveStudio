"""Build lineage JSON for the D3 frontend from a parsed run.

Schema returned by build_lineage():
    {
      "best_id": "<id or null>",
      "nodes": [{"id", "score", "iter", "parent"}, ...]
    }

Edges are implicit from each node's "parent". The frontend uses
d3.stratify(), which requires exactly one root. If a run yields multiple
roots (e.g. island seeds with no parent), we inject a single synthetic
"__root__" node and reparent the real roots under it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from evolvestudio.visualizer.parse import parse_run
from evolvestudio.visualizer.report import _diff_text, _score_of

_SYNTH_ROOT = "__root__"


def build_lineage(output_dir: Path) -> dict:
    run = parse_run(output_dir)
    rows = run.trace_rows

    nodes: dict[str, dict] = {}

    # Children first (authoritative score/iter/parent).
    for r in rows:
        if r.child_id:
            nodes[r.child_id] = {
                "id": r.child_id,
                "score": _score_of(r.child_metrics),
                "iter": r.iteration,
                "parent": r.parent_id,
            }

    # Parents that never appear as a child are roots.
    for r in rows:
        if r.parent_id and r.parent_id not in nodes:
            nodes[r.parent_id] = {
                "id": r.parent_id,
                "score": _score_of(r.parent_metrics),
                "iter": None,
                "parent": None,
            }

    # Null out dangling parent references so stratify doesn't choke.
    for n in nodes.values():
        if n["parent"] and n["parent"] not in nodes:
            n["parent"] = None

    # Guarantee a single root.
    roots = [n for n in nodes.values() if not n["parent"]]
    if len(roots) > 1:
        nodes[_SYNTH_ROOT] = {
            "id": _SYNTH_ROOT,
            "score": None,
            "iter": None,
            "parent": None,
            "synthetic": True,
        }
        for r in roots:
            r["parent"] = _SYNTH_ROOT

    # Best id: prefer best/ metadata; fall back to highest-scoring node.
    best_id = (run.best_program_info or {}).get("id")
    if not best_id:
        scored = [
            (n["id"], n["score"])
            for n in nodes.values()
            if n["score"] is not None and n["id"] != _SYNTH_ROOT
        ]
        if scored:
            scored.sort(key=lambda t: t[1], reverse=True)
            best_id = scored[0][0]

    return {"best_id": best_id, "nodes": list(nodes.values())}


def build_node(output_dir: Path, node_id: str) -> Optional[dict]:
    """Return {id, score, iter, parent, code, diff, changes} or None."""
    run = parse_run(output_dir)

    # Node as a child (the row that created it) -> has code + diff vs parent.
    for r in run.trace_rows:
        if r.child_id == node_id:
            return {
                "id": node_id,
                "score": _score_of(r.child_metrics),
                "iter": r.iteration,
                "parent": r.parent_id,
                "code": r.child_code or "",
                "diff": _diff_text(r.parent_code, r.child_code, r.code_diff, 400),
                "changes": r.child_changes_description or "",
            }

    # Node only as a parent (root / initial program) -> code, no diff.
    for r in run.trace_rows:
        if r.parent_id == node_id:
            return {
                "id": node_id,
                "score": _score_of(r.parent_metrics),
                "iter": None,
                "parent": None,
                "code": r.parent_code or "",
                "diff": "",
                "changes": "",
            }

    return None
