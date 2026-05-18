"""Wraps OpenEvolve execution.

Invokes `openevolve.api.run_evolution` (or the `openevolve-run` CLI) on a
compiled experiment, streams progress, and exposes the resulting run
directory (checkpoints + `evolution_trace.jsonl`) to the visualizer.
"""
