"""Streamlit-free OpenEvolve subprocess manager.

Launches OpenEvolve as a detached process group (survives a closed browser
tab or a uvicorn --reload), stops it with SIGTERM -> SIGKILL, and reports
status by polling the output directory. A small JSON registry on disk maps
run_id -> {output_dir, pid, slug, started_at} so status/stop/lineage keep
working after the server process restarts.

This is the same launch/stop approach the old Streamlit app used, just
decoupled from session_state.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from evolvestudio.experiments import (
    GENERATED_ROOT,
    build_argv,
    default_output_dir,
    list_runs,
)
from evolvestudio.visualizer.parse import parse_run
from evolvestudio.visualizer.report import _score_of, _tail

_REGISTRY_PATH = GENERATED_ROOT / ".runs.json"

# Popen objects for runs launched by THIS process (lets us read exact
# returncodes). Runs from a prior process are tracked via pid liveness only.
_PROCS: dict[str, subprocess.Popen] = {}


# --------------------------------------------------------------------------
# Registry persistence
# --------------------------------------------------------------------------


def _load_registry() -> dict:
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(_REGISTRY_PATH.read_text())
    except Exception:
        return {}


def _save_registry(reg: dict) -> None:
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    tmp.replace(_REGISTRY_PATH)


def _register(run_id: str, entry: dict) -> None:
    reg = _load_registry()
    reg[run_id] = entry
    _save_registry(reg)


def _get_entry(run_id: str) -> Optional[dict]:
    return _load_registry().get(run_id)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


def _unique_run_id(output_dir: Path) -> str:
    base = output_dir.name
    reg = _load_registry()
    run_id = base
    n = 2
    while run_id in reg:
        run_id = f"{base}_{n}"
        n += 1
    return run_id


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def launch_run(
    slug: str,
    iterations: Optional[int],
    python_exe: str,
    output_dir: Optional[str] = None,
) -> dict:
    """Launch OpenEvolve for `slug`. Returns {run_id, pid, output_dir}."""
    exp_dir = GENERATED_ROOT / slug
    if not exp_dir.is_dir():
        raise FileNotFoundError(f"experiment not found: {slug}")

    out_dir = Path(output_dir) if output_dir else default_output_dir(exp_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    argv = build_argv(exp_dir, iterations, out_dir, python_exe)

    stdout_f = (out_dir / "stdout.log").open("w")
    stderr_f = (out_dir / "stderr.log").open("w")
    proc = subprocess.Popen(
        argv,
        stdout=stdout_f,
        stderr=stderr_f,
        shell=False,
        start_new_session=True,
        text=True,
    )

    run_id = _unique_run_id(out_dir)
    _PROCS[run_id] = proc
    _register(
        run_id,
        {
            "output_dir": str(out_dir),
            "pid": proc.pid,
            "slug": slug,
            "started_at": time.time(),
            "argv": argv,
        },
    )
    return {"run_id": run_id, "pid": proc.pid, "output_dir": str(out_dir)}


def stop_run(run_id: str, grace: float = 5.0) -> dict:
    """SIGTERM the process group, SIGKILL after `grace` seconds."""
    entry = _get_entry(run_id)
    if entry is None:
        return {"stopped": False, "detail": "unknown run_id"}
    pid = entry.get("pid")
    proc = _PROCS.get(run_id)

    if not _pid_alive(pid):
        return {"stopped": True, "detail": "already exited"}

    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    # Wait for grace period.
    deadline = time.time() + grace
    while time.time() < deadline:
        if not _pid_alive(pid):
            return {"stopped": True, "detail": "terminated (SIGTERM)"}
        time.sleep(0.1)

    # Still alive -> SIGKILL.
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    return {"stopped": True, "detail": "killed (SIGKILL after grace)"}


def _best_score(run) -> Optional[float]:
    if run.best_program_info:
        s = _score_of(run.best_program_info.get("metrics"))
        if s is not None:
            return s
    best = None
    for r in run.trace_rows:
        s = _score_of(r.child_metrics)
        if s is not None and (best is None or s > best):
            best = s
    return best


def run_status(run_id: str) -> dict:
    """Return {state, iters_done, best_score, log_tail, output_dir}.

    state in {"running", "done", "error", "unknown"}.
    """
    entry = _get_entry(run_id)
    if entry is None:
        return {
            "state": "unknown",
            "iters_done": 0,
            "best_score": None,
            "log_tail": "",
            "output_dir": None,
        }

    out_dir = Path(entry["output_dir"])
    pid = entry.get("pid")
    proc = _PROCS.get(run_id)

    # Determine liveness / final state.
    if proc is not None and proc.poll() is not None:
        rc = proc.returncode
        state = "done" if rc == 0 else "error"
    elif proc is not None:
        state = "running"
    else:
        # Launched by a prior process; fall back to pid liveness.
        state = "running" if _pid_alive(pid) else "done"

    run = parse_run(out_dir)
    iters_done = len(run.trace_rows)
    best = _best_score(run)

    log_tail = ""
    stdout_log = out_dir / "stdout.log"
    if stdout_log.exists():
        log_tail = _tail(stdout_log, 40)

    started_at = entry.get("started_at") or 0
    elapsed = (time.time() - started_at) if started_at else None

    return {
        "state": state,
        "iters_done": iters_done,
        "best_score": best,
        "log_tail": log_tail,
        "elapsed": elapsed,
        "output_dir": str(out_dir),
    }


def resolve_output_dir(run_id: str) -> Optional[Path]:
    """Map a run_id to its output directory (via registry)."""
    entry = _get_entry(run_id)
    if entry is None:
        return None
    return Path(entry["output_dir"])
