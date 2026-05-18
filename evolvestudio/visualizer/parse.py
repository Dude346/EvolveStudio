"""Defensive parser for an OpenEvolve run output directory.

Loads whatever's present without assuming a fixed format. Missing or
malformed files are surfaced as warnings, never as exceptions.

Expected layout (any subset may exist):
    <output_dir>/
        evolution_trace.jsonl       (or .json / .jsonl.gz)
        best/
            best_program.<ext>
            best_program_info.json
        checkpoints/checkpoint_<N>/
            best_program.<ext>
            best_program_info.json
        logs/*.log                  (OpenEvolve's logger)
        stdout.log / stderr.log     (runner-supplied tee, if --execute used)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TraceRow:
    """A single row from evolution_trace.jsonl, plus the original dict."""

    iteration: Optional[int]
    parent_id: Optional[str]
    child_id: Optional[str]
    parent_metrics: Dict[str, Any]
    child_metrics: Dict[str, Any]
    code_diff: Optional[str]
    parent_code: Optional[str]
    child_code: Optional[str]
    child_changes_description: Optional[str]
    raw: Dict[str, Any]


@dataclass
class Checkpoint:
    iteration: int
    path: Path
    best_program_info: Optional[Dict[str, Any]]
    best_program_path: Optional[Path]


@dataclass
class RunOutput:
    output_dir: Path
    evolution_trace_path: Optional[Path] = None
    trace_rows: List[TraceRow] = field(default_factory=list)
    best_program_info: Optional[Dict[str, Any]] = None
    best_program_path: Optional[Path] = None
    best_program_code: Optional[str] = None
    checkpoints: List[Checkpoint] = field(default_factory=list)
    log_files: List[Path] = field(default_factory=list)
    stdout_log: Optional[Path] = None
    stderr_log: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)


def _safe_read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except Exception:
        return None


def _safe_read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _iter_jsonl(path: Path):
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def _to_row(d: Dict[str, Any]) -> TraceRow:
    if not isinstance(d, dict):
        d = {}
    return TraceRow(
        iteration=d.get("iteration"),
        parent_id=d.get("parent_id"),
        child_id=d.get("child_id"),
        parent_metrics=d.get("parent_metrics") or {},
        child_metrics=d.get("child_metrics") or {},
        code_diff=d.get("code_diff"),
        parent_code=d.get("parent_code"),
        child_code=d.get("child_code"),
        child_changes_description=d.get("child_changes_description"),
        raw=d,
    )


def _find_best_program_file(folder: Path) -> Optional[Path]:
    """Find best_program.<ext> in `folder`, ignoring best_program_info.json."""
    if not folder.is_dir():
        return None
    for p in sorted(folder.glob("best_program.*")):
        if p.name == "best_program_info.json":
            continue
        return p
    return None


def parse_run(output_dir: Path) -> RunOutput:
    """Parse an OpenEvolve output directory. Never raises."""
    output_dir = Path(output_dir).resolve()
    run = RunOutput(output_dir=output_dir)

    if not output_dir.is_dir():
        run.warnings.append(f"Output dir does not exist: {output_dir}")
        return run

    # ---- evolution_trace ---------------------------------------------------
    for name in ("evolution_trace.jsonl", "evolution_trace.json"):
        p = output_dir / name
        if p.exists():
            run.evolution_trace_path = p
            break
    gz = output_dir / "evolution_trace.jsonl.gz"
    if run.evolution_trace_path is None and gz.exists():
        run.evolution_trace_path = gz
        run.warnings.append("Found gzipped trace; parser does not decompress yet.")

    if run.evolution_trace_path and run.evolution_trace_path.suffix == ".jsonl":
        for d in _iter_jsonl(run.evolution_trace_path):
            run.trace_rows.append(_to_row(d))
    elif run.evolution_trace_path and run.evolution_trace_path.suffix == ".json":
        data = _safe_read_json(run.evolution_trace_path)
        rows = []
        if isinstance(data, dict) and isinstance(data.get("traces"), list):
            rows = data["traces"]
        elif isinstance(data, list):
            rows = data
        for d in rows:
            run.trace_rows.append(_to_row(d))

    # ---- best/ -------------------------------------------------------------
    best_dir = output_dir / "best"
    if best_dir.is_dir():
        info_path = best_dir / "best_program_info.json"
        if info_path.exists():
            info = _safe_read_json(info_path)
            if isinstance(info, dict):
                run.best_program_info = info
            else:
                run.warnings.append(f"Could not parse {info_path}")
        run.best_program_path = _find_best_program_file(best_dir)
        if run.best_program_path:
            run.best_program_code = _safe_read_text(run.best_program_path)

    # ---- checkpoints/ ------------------------------------------------------
    cp_root = output_dir / "checkpoints"
    if cp_root.is_dir():
        for cp_dir in sorted(cp_root.glob("checkpoint_*")):
            if not cp_dir.is_dir():
                continue
            try:
                iter_n = int(cp_dir.name.split("_", 1)[1])
            except (ValueError, IndexError):
                run.warnings.append(f"Skipping checkpoint with bad name: {cp_dir.name}")
                continue
            info = _safe_read_json(cp_dir / "best_program_info.json")
            run.checkpoints.append(
                Checkpoint(
                    iteration=iter_n,
                    path=cp_dir,
                    best_program_info=info if isinstance(info, dict) else None,
                    best_program_path=_find_best_program_file(cp_dir),
                )
            )

    # ---- logs/ -------------------------------------------------------------
    log_dir = output_dir / "logs"
    if log_dir.is_dir():
        run.log_files = sorted(log_dir.glob("*.log"))

    # ---- runner-supplied stdout/stderr ------------------------------------
    so = output_dir / "stdout.log"
    se = output_dir / "stderr.log"
    run.stdout_log = so if so.exists() else None
    run.stderr_log = se if se.exists() else None

    return run
