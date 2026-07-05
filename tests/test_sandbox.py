"""Sandbox executor: computes safe snippets, rejects dangerous ones, enforces the timeout.

All LLM-free — exercises the AST allowlist and subprocess isolation directly.
"""

from kompass.sandbox.executor import run_python


def test_safe_snippet_returns_value():
    r = run_python(
        "result = sum(row['x'] for row in data['rows'])",
        data={"rows": [{"x": 1}, {"x": 2}, {"x": 3}]},
    )
    assert r.ok and r.value == "6"


def test_whitelisted_import_works():
    r = run_python("import statistics; result = round(statistics.mean([2, 4, 7]), 2)")
    assert r.ok and r.value == "4.33"


def test_rejects_forbidden_import():
    r = run_python("import os\nresult = os.getcwd()")
    assert not r.ok and "import not allowed" in r.error


def test_rejects_open_and_dunder_escape():
    assert not run_python("result = open('secrets.txt').read()").ok
    assert not run_python("result = ().__class__.__bases__").ok


def test_timeout_kills_runaway():
    r = run_python("while True:\n    pass", timeout_s=2)
    assert not r.ok and "exceeded" in r.error
