"""FastAPI app: API routes + static frontend mount.

Run (from the project root):
    uvicorn evolvestudio.server.main:app --reload --port 8501

The frontend is served from ../../frontend at "/". API routes live under
/api/* and are registered before the catch-all static mount so they win.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import json as _json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from evolvestudio import experiments as exp
from evolvestudio import harness_gen as gen
from evolvestudio.server import lineage as lin
from evolvestudio.server import runs as runs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(title="EvolveStudio API", version="0.1.0")


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class ExperimentBody(BaseModel):
    title: str
    statement: str = ""
    initial_program: str
    evaluator: str
    config: str
    slug: Optional[str] = None


class RunBody(BaseModel):
    slug: str
    iterations: Optional[int] = 10
    python: str = exp.DEFAULT_PY
    output_dir: Optional[str] = None
    target_score: Optional[float] = 1.0  # stop early once a candidate hits this
    model: Optional[str] = None  # evolution-loop model (rewrites config.yaml)


class GenerateBody(BaseModel):
    statement: str
    model: Optional[str] = None


# --------------------------------------------------------------------------
# Experiments
# --------------------------------------------------------------------------


@app.get("/api/experiments")
def list_experiments() -> list[dict]:
    return exp.experiment_summaries()


@app.post("/api/experiments")
def create_experiment(body: ExperimentBody) -> dict:
    exp_dir = exp.save_experiment_from_text(
        title=body.title,
        statement=body.statement,
        initial=body.initial_program,
        evaluator=body.evaluator,
        config=body.config,
        slug=body.slug,
    )
    return {"slug": exp_dir.name}


@app.get("/api/experiments/{slug}")
def get_experiment(slug: str) -> dict:
    data = exp.read_experiment(slug)
    if data is None:
        raise HTTPException(status_code=404, detail=f"experiment not found: {slug}")
    return data


# --------------------------------------------------------------------------
# Demos (quick-start chips)
# --------------------------------------------------------------------------


@app.get("/api/models")
def list_models() -> dict:
    """Live list of installed Ollama chat models (for the UI dropdowns)."""
    names = gen.list_ollama_models()
    return {"models": names, "default": gen.DEFAULT_MODEL}


@app.get("/api/demos")
def list_demos() -> list[str]:
    return list(exp.DEMOS.keys())


@app.get("/api/demos/{kind}")
def get_demo(kind: str) -> dict:
    try:
        return exp.demo_files(kind)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --------------------------------------------------------------------------
# LLM harness generation
# --------------------------------------------------------------------------


@app.post("/api/generate")
def generate(body: GenerateBody) -> dict:
    """Synchronous: gpt-oss -> spec -> compiled harness. Returns {slug, spec, files}."""
    model = body.model or gen.DEFAULT_MODEL
    try:
        return gen.generate_and_compile(body.statement, model=model)
    except gen.HarnessGenError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/generate/stream")
def generate_stream(body: GenerateBody) -> StreamingResponse:
    """Streaming (SSE): live model tokens, then a final result/error event."""
    model = body.model or gen.DEFAULT_MODEL
    statement = body.statement

    def event_gen():
        acc = []
        try:
            for delta in gen.stream_raw(statement, model=model):
                acc.append(delta)
                yield f"data: {_json.dumps({'type': 'token', 'text': delta})}\n\n"
            full = "".join(acc)
            spec = gen._validate_and_normalize(gen._extract_json(full))
            exp_dir = gen.compile_spec(spec, statement)
            payload = {
                "type": "result",
                "slug": exp_dir.name,
                "spec": spec,
                "files": {
                    "initial_program": (exp_dir / "initial_program.py").read_text(),
                    "evaluator": (exp_dir / "evaluator.py").read_text(),
                    "config": (exp_dir / "config.yaml").read_text(),
                },
            }
            yield f"data: {_json.dumps(payload)}\n\n"
        except gen.HarnessGenError as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {_json.dumps({'type': 'error', 'message': f'unexpected: {e}'})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


@app.post("/api/runs")
def create_run(body: RunBody) -> dict:
    # If a model was chosen, bake it into the experiment's config before launch.
    if body.model:
        exp.set_experiment_model(body.slug, body.model)
    try:
        return runs.launch_run(
            slug=body.slug,
            iterations=body.iterations,
            python_exe=body.python,
            output_dir=body.output_dir,
            target_score=body.target_score,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict:
    return runs.stop_run(run_id)


@app.get("/api/runs/{run_id}/status")
def run_status(run_id: str) -> dict:
    return runs.run_status(run_id)


@app.get("/api/runs/{run_id}/lineage")
def run_lineage(run_id: str) -> dict:
    out_dir = runs.resolve_output_dir(run_id)
    if out_dir is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    return lin.build_lineage(out_dir)


@app.get("/api/runs/{run_id}/node/{node_id}")
def run_node(run_id: str, node_id: str) -> dict:
    out_dir = runs.resolve_output_dir(run_id)
    if out_dir is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    node = lin.build_node(out_dir, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"node not found: {node_id}")
    return node


# Convenience: list runs for an experiment (used by the Results selector).
@app.get("/api/experiments/{slug}/runs")
def list_experiment_runs(slug: str) -> list[dict]:
    exp_dir = exp.GENERATED_ROOT / slug
    out = []
    for run_dir in exp.list_runs(exp_dir):
        out.append({"run_id": run_dir.name, "name": run_dir.name})
    return out


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# --------------------------------------------------------------------------
# Static frontend (mounted LAST so /api/* wins)
# --------------------------------------------------------------------------

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
