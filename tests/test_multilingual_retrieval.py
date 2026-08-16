"""Multilingual retrieval contracts for the English ACME corpus."""

from kompass.retrieval.rag import _expand_query, _tokenize, search


def test_spanish_accents_are_normalized_and_domain_terms_are_expanded():
    assert _tokenize("¿Cuántos días por año?") == ["cuantos", "dias", "por", "ano"]
    expanded = _expand_query("¿Cuántos días de vacaciones tengo por año?")
    assert "vacation annual leave entitlement" in expanded
    assert "days" in expanded
    assert "year annual" in expanded


def test_spanish_vacation_question_retrieves_entitlement():
    results = search("¿Cuántos días de vacaciones tengo por año?", k=4)
    assert any(
        result.source == "policies/vacation_policy.md"
        and result.section == "Annual Leave Entitlement"
        for result in results
    )
