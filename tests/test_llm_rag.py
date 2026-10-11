from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_rag
from niyamsetu import load_rules, search, score_rules, snippet_for

RULES, MODE = load_rules()


def test_llm_disabled_falls_back_without_network(monkeypatch):
    monkeypatch.delenv("NIYAMSETU_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm_rag, "_call_chat_api", lambda *args: (_ for _ in ()).throw(AssertionError("API must not be called")))
    result = llm_rag.answer_with_rag(
        "I want a farm house on agricultural land. What minimum plot size and maximum FAR are listed in Rule 17?",
        RULES, MODE, search, score_rules, snippet_for,
    )
    assert result["ok"] is True
    assert result["generation_mode"] == "retrieval_only"
    assert result["llm_enabled"] is False
    assert "No API key is configured" in result["generation_note"]


def test_current_law_guard_does_not_call_llm(monkeypatch):
    monkeypatch.setenv("NIYAMSETU_API_KEY", "test-key")
    monkeypatch.setattr(llm_rag, "_call_chat_api", lambda *args: (_ for _ in ()).throw(AssertionError("Current-law guard must fire first")))
    result = llm_rag.answer_with_rag(
        "Do I need separate development permission for a 300 sq m plot in 2026?",
        RULES, MODE, search, score_rules, snippet_for,
    )
    assert result["status"] == "pending_official_verification"
    assert result["generation_mode"] == "retrieval_only"
    assert "LLM was not called" in result["generation_note"]


def test_valid_llm_response_uses_only_retrieved_citations(monkeypatch):
    monkeypatch.setenv("NIYAMSETU_API_KEY", "test-key")
    monkeypatch.setenv("NIYAMSETU_LLM_MODEL", "test-model")
    monkeypatch.setattr(llm_rag, "_call_chat_api", lambda query, sources, cfg: {
        "answer": "The supplied historical Rule 17 text lists a minimum plot size of 0.40 hectare and a maximum FAR of 0.10, subject to the stated conditions.",
        "citations": [sources[0]["id"]],
        "abstained": False,
    })
    result = llm_rag.answer_with_rag(
        "I want a farm house on agricultural land. What minimum plot size and maximum FAR are listed in Rule 17?",
        RULES, MODE, search, score_rules, snippet_for,
    )
    assert result["ok"] is True
    assert result["generation_mode"] == "llm_rag"
    assert result["llm_used"] is True
    assert result["sources"]
    assert result["sources"][0]["rule"] == "17"
    assert "0.40 hectare" in result["message"]


def test_unverifiable_new_numeric_value_rejects_llm_output(monkeypatch):
    monkeypatch.setenv("NIYAMSETU_API_KEY", "test-key")
    monkeypatch.setattr(llm_rag, "_call_chat_api", lambda query, sources, cfg: {
        "answer": "The minimum farm house plot is 0.80 hectare.",
        "citations": [sources[0]["id"]],
        "abstained": False,
    })
    result = llm_rag.answer_with_rag(
        "I want a farm house on agricultural land. What minimum plot size and maximum FAR are listed in Rule 17?",
        RULES, MODE, search, score_rules, snippet_for,
    )
    assert result["generation_mode"] == "retrieval_only"
    assert "numerical values not found" in result["generation_note"]


def test_unknown_citation_rejected(monkeypatch):
    source = [{"id": "S1", "full_text": "Rule 17 minimum plot 0.40 hectare"}]
    ok, message = llm_rag._validate_llm_output({
        "answer": "The minimum is 0.40 hectare.", "citations": ["S99"], "abstained": False,
    }, "What is the minimum?", source)
    assert ok is False
    assert "outside the retrieved evidence" in message


def test_model_abstention_can_have_no_citation():
    ok, _ = llm_rag._validate_llm_output({
        "answer": "I cannot determine the current requirement from these sources.",
        "citations": [], "abstained": True,
    }, "What is the current rule?", [])
    assert ok is True


def test_public_llm_status_never_discloses_api_key(monkeypatch):
    monkeypatch.setenv("NIYAMSETU_API_KEY", "secret-test-key")
    status = llm_rag.llm_public_status()
    assert status["enabled"] is True
    assert "secret-test-key" not in str(status)
    assert status["model"]


def test_chat_api_request_uses_configured_openai_compatible_endpoint(monkeypatch):
    import json
    from io import BytesIO
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return BytesIO(json.dumps({
            "choices": [{"message": {"content": '{"answer":"Historical text only.","citations":["S1"],"abstained":false}'}}]
        }).encode("utf-8"))

    monkeypatch.setattr(llm_rag.urllib.request, "urlopen", fake_urlopen)
    cfg = {
        "base_url": "https://api.example.test/v1",
        "model": "test-model",
        "api_key": "test-secret",
        "timeout": 7,
    }
    sources = [{
        "id": "S1", "rule": "17", "title": "Farm house", "part": "Part III",
        "page_start": 34, "page_end": 34, "version_id": "2025-03-06-consolidated",
        "full_text": "Rule 17 minimum plot 0.40 hectare and maximum FAR 0.10",
    }]
    result = llm_rag._call_chat_api("What does Rule 17 state?", sources, cfg)
    assert captured["url"] == "https://api.example.test/v1/chat/completions"
    assert captured["auth"] == "Bearer test-secret"
    assert captured["timeout"] == 7
    assert captured["body"]["model"] == "test-model"
    assert result["citations"] == ["S1"]


def test_definitive_permission_claim_is_rejected():
    source = [{"id": "S1", "full_text": "Rule 2(5)(c) historical placeholder text"}]
    ok, message = llm_rag._validate_llm_output({
        "answer": "No separate development permission is required.",
        "citations": ["S1"], "abstained": False,
    }, "Do I need permission in 2026?", source)
    assert ok is False
    assert "definitive permission/current-law claim" in message
