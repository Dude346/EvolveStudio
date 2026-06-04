"""Generate an OpenEvolve harness from a natural-language problem via a local LLM.

The LLM (default: Ollama `gpt-oss:20b`) reads a problem statement (LeetCode,
Project Euler, etc.) and returns a STRUCTURED SPEC — function name, signature,
a naive baseline body, and a list of test cases. We then compile that spec into
the three OpenEvolve files using the existing, known-correct `unit_tests`
compiler. The LLM never writes the evaluator or config directly (those must
match OpenEvolve's contract exactly), which keeps the risky surface minimal.

Robustness:
  - JSON is extracted defensively from the model output (handles ```json
    fences and reasoning preambles).
  - The spec is validated; a bad/missing signature is reconstructed from the
    function name + the arity of the first test case.
  - Never compiles an invalid spec — raises HarnessGenError with a message the
    UI can show.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Iterator, Optional

from openai import OpenAI

from evolvestudio.compiler.unit_tests import UnitTestProblem, compile_problem
from evolvestudio.experiments import GENERATED_ROOT, slugify

OLLAMA_BASE = "http://localhost:11434/v1"
DEFAULT_MODEL = "gpt-oss:20b"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class HarnessGenError(Exception):
    pass


SYSTEM_PROMPT = """You compile programming problems into test harnesses.

Given a problem (LeetCode, Project Euler, coding interview, etc.), output a SINGLE
JSON object — and NOTHING else — with exactly these keys:

{
  "title": "<short title, <= 60 chars>",
  "function_name": "<the function the solver must implement, a valid Python identifier>",
  "function_signature": "def <function_name>(<params>):",
  "baseline_code": "<a naive but RUNNABLE Python body for that function>",
  "test_cases": [ {"args": [<positional args>], "expected": <expected return value>}, ... ]
}

Hard rules:
- "args" is ALWAYS a list of the positional arguments. For a one-argument call like
  f([3,1,2]), use "args": [[3,1,2]] (the outer list is the argument list, the inner
  list is the single argument). For f(2, 7) use "args": [2, 7].
- "expected" is the correct return value. COMPUTE IT CAREFULLY — wrong expected values
  make a correct solution look wrong.
- Use ONLY JSON literals in args/expected (numbers, strings, booleans, null, lists,
  objects). NEVER write an expression. WRONG: {"args": ["a"*100]}. RIGHT: write the
  value out in full, or use a short input you can write literally like {"args": ["aaaa"]}.
  No `*`, no `+`, no function calls, no comments. Keep test inputs small enough to spell out.
- "baseline_code" is the function BODY ONLY (no `def` line). Write it at top-level
  indentation (the harness indents it). It must be valid Python and should actually
  attempt the problem (a simple/naive approach is fine and even preferred — leave room
  to optimize). If the function recurses, it must call itself by its own name.
- Provide 6-12 test cases covering edge cases (empty, single, duplicates, large).
- Output ONLY the JSON object. No markdown fences, no prose, no explanation.
"""


def _client(api_base: str = OLLAMA_BASE) -> OpenAI:
    return OpenAI(base_url=api_base, api_key="ollama")


def list_ollama_models(api_base: str = OLLAMA_BASE) -> list[str]:
    """Live list of installed Ollama models that can chat (embeddings filtered).

    Returns [] if Ollama isn't reachable. gpt-oss:20b is floated to the top
    when present so the default lands first in UI dropdowns.
    """
    try:
        data = _client(api_base).models.list().data
        names = [m.id for m in data]
    except Exception:
        return []
    names = [n for n in names if "embed" not in n.lower()]
    names.sort()
    if DEFAULT_MODEL in names:
        names.remove(DEFAULT_MODEL)
        names.insert(0, DEFAULT_MODEL)
    return names


def _user_prompt(statement: str) -> str:
    return f"Problem:\n\n{statement.strip()}\n\nReturn the JSON harness spec now."


# --------------------------------------------------------------------------
# JSON extraction + validation
# --------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of model output."""
    if not text or not text.strip():
        raise HarnessGenError("model returned empty output")

    # ```json ... ``` fence
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None

    if candidate is None:
        # First balanced { ... } by brace counting.
        start = text.find("{")
        if start == -1:
            raise HarnessGenError("no JSON object found in model output")
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    break
        if candidate is None:
            raise HarnessGenError("unterminated JSON object in model output")

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        # Fallback: the model may have emitted Python-style output (single
        # quotes, True/False/None). ast.literal_eval handles those safely.
        try:
            obj = ast.literal_eval(candidate)
            if isinstance(obj, dict):
                return obj
        except (ValueError, SyntaxError):
            pass
        raise HarnessGenError(f"model output was not valid JSON: {e}")


def _validate_and_normalize(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise HarnessGenError("spec is not a JSON object")

    title = str(spec.get("title") or "").strip() or "Generated problem"
    title = title[:80]

    fn = str(spec.get("function_name") or "").strip()
    if not _IDENT_RE.match(fn):
        raise HarnessGenError(f"invalid function_name: {fn!r}")

    test_cases = spec.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        raise HarnessGenError("test_cases must be a non-empty list")
    norm_cases = []
    for i, c in enumerate(test_cases):
        if not isinstance(c, dict) or "args" not in c or "expected" not in c:
            raise HarnessGenError(f"test case #{i} must have 'args' and 'expected'")
        if not isinstance(c["args"], list):
            raise HarnessGenError(f"test case #{i} 'args' must be a list")
        norm_cases.append({"args": c["args"], "expected": c["expected"]})

    sig = str(spec.get("function_signature") or "").strip()
    if not (sig.startswith("def ") and fn in sig and sig.endswith(":")):
        # Reconstruct from arity of the first test case.
        arity = len(norm_cases[0]["args"])
        params = ", ".join(f"a{i}" for i in range(arity))
        sig = f"def {fn}({params}):"

    baseline = spec.get("baseline_code")
    if not isinstance(baseline, str) or not baseline.strip():
        # Minimal placeholder body so the file is runnable; OpenEvolve evolves it.
        baseline = "raise NotImplementedError\n"

    return {
        "title": title,
        "function_name": fn,
        "function_signature": sig,
        "baseline_code": baseline.rstrip() + "\n",
        "test_cases": norm_cases,
    }


# --------------------------------------------------------------------------
# Generation (sync)
# --------------------------------------------------------------------------


def generate_spec(
    statement: str,
    model: str = DEFAULT_MODEL,
    api_base: str = OLLAMA_BASE,
) -> dict:
    """Call the LLM once and return a validated, normalized spec dict."""
    if not statement or not statement.strip():
        raise HarnessGenError("problem statement is empty")
    try:
        resp = _client(api_base).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(statement)},
            ],
            temperature=0.2,
            max_tokens=6000,
        )
    except Exception as e:  # noqa: BLE001
        raise HarnessGenError(f"LLM call failed (is `ollama serve` running?): {e}")
    content = resp.choices[0].message.content or ""
    return _validate_and_normalize(_extract_json(content))


def spec_to_problem(spec: dict, statement: str) -> UnitTestProblem:
    return UnitTestProblem(
        slug=slugify(spec["title"]),
        title=spec["title"],
        statement=statement.strip(),
        function_name=spec["function_name"],
        function_signature=spec["function_signature"],
        test_cases=spec["test_cases"],
        baseline_code=spec["baseline_code"],
        per_test_timeout=2.0,
    )


def compile_spec(spec: dict, statement: str) -> Path:
    problem = spec_to_problem(spec, statement)
    return compile_problem(problem, GENERATED_ROOT)


def generate_and_compile(
    statement: str, model: str = DEFAULT_MODEL, api_base: str = OLLAMA_BASE
) -> dict:
    """Full pipeline: generate spec -> compile -> return {slug, spec, files}."""
    spec = generate_spec(statement, model=model, api_base=api_base)
    exp_dir = compile_spec(spec, statement)
    return {
        "slug": exp_dir.name,
        "spec": spec,
        "files": {
            "initial_program": (exp_dir / "initial_program.py").read_text(),
            "evaluator": (exp_dir / "evaluator.py").read_text(),
            "config": (exp_dir / "config.yaml").read_text(),
        },
    }


# --------------------------------------------------------------------------
# Generation (streaming) — yields raw text chunks for "live" UX
# --------------------------------------------------------------------------


def stream_raw(
    statement: str, model: str = DEFAULT_MODEL, api_base: str = OLLAMA_BASE
) -> Iterator[str]:
    """Yield text chunks as the model generates. Caller accumulates + parses."""
    stream = _client(api_base).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(statement)},
        ],
        temperature=0.2,
        max_tokens=6000,
        stream=True,
    )
    for chunk in stream:
        try:
            delta = chunk.choices[0].delta.content
        except (IndexError, AttributeError):
            delta = None
        if delta:
            yield delta
