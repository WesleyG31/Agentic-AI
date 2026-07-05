"""Demo-grade sandbox for running model-generated Python.

Isolation here is three layers: an AST allowlist (rejects non-whitelisted imports,
dangerous builtins, and dunder traversal before anything runs), execution in a
separate `python -I` subprocess (isolated mode, no inherited env or user site), and
a hard wall-clock timeout. That is enough to demo safe analytics; a production system
would run this inside a container / gVisor / Firecracker or a service like E2B, which
is the only way to truly contain arbitrary code. Executing model-generated code is a
trust boundary, so the AST check is strict by design.
"""

import ast
import json
import subprocess
import sys

from pydantic import BaseModel

ALLOWED_IMPORTS = {
    "json", "math", "statistics", "datetime", "collections", "itertools", "functools",
}
FORBIDDEN_NAMES = {
    "open", "eval", "exec", "compile", "input", "__import__",
    "os", "sys", "subprocess", "globals", "locals", "vars", "getattr", "setattr", "delattr",
}

# Runs in the child: read {code, data} from stdin, exec the (already AST-vetted) code with
# `data` in scope, emit the `result` variable after a sentinel so the parent can split it out.
_RUNNER = (
    "import sys, json\n"
    "p = json.load(sys.stdin)\n"
    "ns = {'data': p.get('data')}\n"
    "exec(p['code'], ns)\n"
    "sys.stdout.write('__RESULT__' + json.dumps(ns.get('result'), default=str))\n"
)


class Result(BaseModel):
    """Outcome of a sandboxed run."""

    ok: bool
    stdout: str = ""
    value: str | None = None
    error: str | None = None


def _reject(code: str) -> str | None:
    """Return a rejection reason if the code violates the allowlist, else None."""
    tree = ast.parse(code)  # SyntaxError propagates — a caller bug, not a handled case
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [n.name for n in node.names if n.name.split(".")[0] not in ALLOWED_IMPORTS]
            if bad:
                return f"import not allowed: {', '.join(bad)}"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                return f"import not allowed: {node.module}"
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return f"name not allowed: {node.id}"
        elif isinstance(node, ast.Attribute):
            dunder = node.attr.startswith("__") and node.attr.endswith("__")
            if node.attr in FORBIDDEN_NAMES or dunder:
                return f"attribute not allowed: {node.attr}"
    return None


def run_python(code: str, data: dict | None = None, timeout_s: float = 5) -> Result:
    """Run `code` in the sandbox with `data` available as a variable; return its `result`.

    The snippet reads its input from `data` and assigns the answer to a variable named
    `result`, which comes back JSON-serialized in `Result.value`.
    """
    reason = _reject(code)
    if reason:
        return Result(ok=False, error=reason)

    payload = json.dumps({"code": code, "data": data})
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _RUNNER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return Result(ok=False, error=f"execution exceeded {timeout_s}s and was killed")

    if proc.returncode != 0:
        return Result(ok=False, stdout=proc.stdout, error=proc.stderr.strip().splitlines()[-1])

    stdout, _, value = proc.stdout.partition("__RESULT__")
    return Result(ok=True, stdout=stdout, value=value or None)
