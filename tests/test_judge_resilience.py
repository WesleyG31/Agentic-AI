from evals import judge as judge_module


class _Structured:
    def __init__(self, result):
        self._result = result

    def invoke(self, prompt, config=None):
        return self._result


class _Model:
    def __init__(self, results):
        self._results = iter(results)

    def with_structured_output(self, schema, method):
        return _Structured(next(self._results))


def test_judge_retries_when_provider_returns_none(monkeypatch):
    expected = judge_module.Verdict(
        answer_correctness=True,
        hallucination=False,
        tool_selection=True,
        tool_arguments_correct=True,
        retrieval_relevance=1.0,
        task_success=True,
        notes="valid fallback mode",
    )
    model = _Model([None, expected])
    monkeypatch.setattr(judge_module, "pick", lambda tier: model)
    item = {
        "question": "Question",
        "category": "abstain",
        "expected_facts": [],
        "expected_tools": [],
    }

    assert judge_module.judge(item, "Unavailable") == expected
