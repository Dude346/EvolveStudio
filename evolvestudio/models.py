"""Shared data models for EvolveStudio.

Will hold the in-memory representation of:
    - a user-supplied problem spec (natural-language input + parsed fields),
    - a compiled experiment (paths to initial_program / evaluator / config),
    - a candidate program (code, metrics, parent id, generation),
    - an iteration's result (success / failure / artifacts).
"""
