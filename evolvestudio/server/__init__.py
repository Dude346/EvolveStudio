"""FastAPI backend for EvolveStudio.

Thin HTTP wrappers over existing logic:
  - experiments  -> evolvestudio.experiments
  - run launch/stop/status -> evolvestudio.runner.process
  - lineage / node -> evolvestudio.visualizer.parse (via server.lineage)

Serves the static frontend in ../../frontend at "/".
Run: uvicorn evolvestudio.server.main:app --reload --port 8501
"""
