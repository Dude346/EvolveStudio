"""Run lifecycle wrappers for the API layer.

Thin re-exports of the Streamlit-free subprocess manager so the server's
route handlers import from one cohesive place.
"""

from __future__ import annotations

from evolvestudio.runner.process import (  # noqa: F401
    launch_run,
    resolve_output_dir,
    run_status,
    stop_run,
)

__all__ = ["launch_run", "stop_run", "run_status", "resolve_output_dir"]
