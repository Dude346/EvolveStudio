"""Minimal local Streamlit GUI for EvolveStudio.

Run with the OpenEvolve env's interpreter (which has streamlit installed):

    /opt/anaconda3/envs/OpenEvolve/bin/streamlit run evolvestudio/gui/app.py

The GUI never makes network calls on its own. The only outbound traffic
is whatever an OpenEvolve run (Ollama or OpenAI-compatible API) emits,
and that only when you explicitly trigger it from the terminal using
the dry-run command this GUI prints.
"""
