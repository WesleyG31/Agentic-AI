"""Data Analyst tool: answer quantitative questions a single SELECT can't express.

The agent fetches rows with a read-only SELECT, then computes over them in a sandboxed
Python snippet (distributions, multi-step aggregation, what-if math). Read-only end to
end — the SQL layer already rejects writes — so it is not gated behind HITL.
"""

from langchain_core.tools import tool

from kompass.retrieval.nl2sql import run_sql
from kompass.sandbox.executor import run_python


@tool
def analyze(question: str, sql: str, code: str) -> str:
    """Compute an analytic answer beyond a single SQL SELECT.

    Fetch the raw rows with `sql` (one read-only SELECT), then compute over them in `code`,
    which receives the rows as `data["rows"]` (a list of dicts) and must assign the answer to
    a variable `result`. Only json/math/statistics/datetime/collections/itertools/functools
    may be imported.

    Example — average total of delivered orders:
      sql  = "SELECT total_eur FROM orders WHERE status = 'delivered'"
      code = "import statistics as s; result = round(s.mean(r['total_eur'] "
             "for r in data['rows']), 2)"
    """
    rows = run_sql(sql)
    result = run_python(code, data={"rows": rows})
    if not result.ok:
        return f"Analysis failed: {result.error}"
    return f"Result: {result.value} (computed over {len(rows)} row(s) from the query)"
