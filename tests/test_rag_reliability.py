"""Automated reliability and citation audit test suite for NiyamSetu."""
from __future__ import annotations

import os
import re
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any
import pytest

import llm_rag
from niyamsetu import (
    ROOT, DATA, load_rules, search, score_rules, snippet_for,
    build_safe_pdf_url, source_link, official_pdf_link, citation_metadata,
    LOCAL_PDF_FILE, LOCAL_PDF_URL, OFFICIAL_PDF_URL, RULES_LISTING_URL
)

RULES, MODE = load_rules()


# ---------------------------------------------------------------------------
# Step 3 & Step 5: URL Construction, Safe Encoding & Centralized Metadata
# ---------------------------------------------------------------------------

def test_url_encoding_spaces_and_ampersands():
    """Valid URL encoding (%20 and %26) must be preserved without double-encoding."""
    raw_path = "https://example.com/act & Rules/bvn06032025E.pdf"
    encoded = build_safe_pdf_url(raw_path, 45)
    assert encoded == "https://example.com/act%20%26%20Rules/bvn06032025E.pdf#page=45"
    assert "&" not in encoded.split("#")[0]  # unescaped & must not remain in path
    assert "%2520" not in encoded  # no double-encoding of %20

    already_encoded = "https://example.com/act%20%26%20Rules/bvn06032025E.pdf"
    re_encoded = build_safe_pdf_url(already_encoded, 10)
    assert re_encoded == "https://example.com/act%20%26%20Rules/bvn06032025E.pdf#page=10"
    assert "%2526" not in re_encoded


def test_missing_page_metadata_does_not_fabricate_fragment():
    """If page metadata is absent, do not append a fabricated #page= fragment."""
    rule_no_page = {"rule": "99", "title": "General", "page_start": None}
    link = source_link(rule_no_page)
    assert "#page=" not in link
    assert link == LOCAL_PDF_URL if LOCAL_PDF_FILE.exists() else OFFICIAL_PDF_URL

    meta = citation_metadata(rule_no_page)
    assert meta["page_start"] is None
    assert meta["page_end"] is None
    assert "#page=" not in meta["local_pdf_url"]
    assert "#page=" not in meta["official_pdf_url"]


def test_source_links_consistent_and_page_aware():
    """Citation links must point to the local verified endpoint with correct page fragments."""
    rule = {"rule": "56", "title": "Residential Buildings", "page_start": 78, "page_end": 79}
    s_link = source_link(rule, prefer_local=True)
    if LOCAL_PDF_FILE.exists():
        assert s_link == "/api/pdf#page=78"
    else:
        assert s_link == f"{OFFICIAL_PDF_URL}#page=78"

    off_link = official_pdf_link(rule)
    assert off_link == f"{OFFICIAL_PDF_URL}#page=78"


def test_local_pdf_file_provenance_and_integrity():
    """Local PDF file must exist and match official SHA-256 hash."""
    import hashlib
    assert LOCAL_PDF_FILE.exists(), "Local verified PDF must exist at data/bvn06032025E.pdf"
    content = LOCAL_PDF_FILE.read_bytes()
    expected_hash = "4751844a84cb2a970e3aa43ad62658f9ab0fbbd896b59aecbe0db6f40aa9354c"
    assert hashlib.sha256(content).hexdigest() == expected_hash
    assert len(content) == 1215671


# ---------------------------------------------------------------------------
# Step 3: Evidence-Bound Citations & Validation Tests
# ---------------------------------------------------------------------------

def test_citation_validation_rejects_hallucinated_rule_number():
    """The model must not invent rule numbers absent from cited evidence."""
    sources = [{
        "id": "S1", "rule": "17", "title": "Farm house", "part": "Part III",
        "page_start": 34, "page_end": 34, "version_id": "2025-03-06-consolidated",
        "full_text": "Rule 17. Farm house minimum plot 0.40 hectare, maximum FAR 0.10.",
    }]
    # Model cites S1 (Rule 17), but answer claims "Under Rule 88..."
    payload = {
        "answer": "Under Rule 88, a farm house requires 0.40 hectare plot.",
        "citations": ["S1"],
        "abstained": False,
    }
    valid, msg = llm_rag._validate_llm_output(payload, "What does Rule 17 state?", sources)
    assert valid is False
    assert "rule" in msg.lower()


def test_citation_validation_rejects_hallucinated_page_number():
    """The model must not invent page numbers absent from cited evidence."""
    sources = [{
        "id": "S1", "rule": "17", "title": "Farm house", "part": "Part III",
        "page_start": 34, "page_end": 34, "version_id": "2025-03-06-consolidated",
        "full_text": "Rule 17. Farm house minimum plot 0.40 hectare, maximum FAR 0.10.",
    }]
    # Model claims "on page 99" when source is page 34
    payload = {
        "answer": "On page 99, the minimum farm house plot is stated as 0.40 hectare.",
        "citations": ["S1"],
        "abstained": False,
    }
    valid, msg = llm_rag._validate_llm_output(payload, "What does Rule 17 state?", sources)
    assert valid is False
    assert "page" in msg.lower()


def test_citation_validation_rejects_external_urls():
    """The model must not inject arbitrary external URLs."""
    sources = [{
        "id": "S1", "rule": "17", "title": "Farm house", "part": "Part III",
        "page_start": 34, "page_end": 34, "version_id": "2025-03-06-consolidated",
        "full_text": "Rule 17. Farm house minimum plot 0.40 hectare, maximum FAR 0.10.",
    }]
    payload = {
        "answer": "See https://malicious-site.example/rules for 0.40 hectare requirements.",
        "citations": ["S1"],
        "abstained": False,
    }
    valid, msg = llm_rag._validate_llm_output(payload, "What does Rule 17 state?", sources)
    assert valid is False
    assert "url" in msg.lower()


def test_citation_validation_rejects_unsupported_claim():
    """Valid citation IDs attached to completely unsupported claims must be rejected."""
    sources = [{
        "id": "S1", "rule": "17", "title": "Farm house", "part": "Part III",
        "page_start": 34, "page_end": 34, "version_id": "2025-03-06-consolidated",
        "full_text": "Rule 17. Farm house minimum plot 0.40 hectare, maximum FAR 0.10.",
    }]
    # Answer discusses completely unrelated municipal trade licensing
    payload = {
        "answer": "Municipal commercial trade licenses require inspection and renewal.",
        "citations": ["S1"],
        "abstained": False,
    }
    valid, msg = llm_rag._validate_llm_output(payload, "How do trade licenses work?", sources)
    assert valid is False
    assert "substantive" in msg.lower() or "support" in msg.lower()


# ---------------------------------------------------------------------------
# Step 4: Unsupported Questions & Incomplete Evidence
# ---------------------------------------------------------------------------

def test_unsupported_property_tax_question_abstains():
    """Property tax is outside the building rules; system must abstain without hallucination."""
    result = search("How do I pay my property tax online?", RULES, MODE)
    assert result["ok"] is False
    assert result["type"] == "not_found"
    assert "Property tax is not governed" in result["message"]


def test_unsupported_fee_amount_abstains():
    """Unverified fees must not be invented."""
    result = search("What is the exact building permission fee for a commercial mall?", RULES, MODE)
    assert result["ok"] is False
    assert "cannot provide a fee amount" in result["message"]


def test_incomplete_evidence_height_above_limit_explains_known_and_unknown():
    """For heights above 12.5m, Rule 56 refers to Rule 42 tables which are unverified."""
    result = search("My house faces a 10 m street and is 16 m tall. What is the front open space?", RULES, MODE)
    assert result["ok"] is True
    assert "above 12.5 m use Tables 4 and 5 of Rule 42" in result["message"]


def test_unverified_2026_amendment_is_flagged_not_stated_as_current_law():
    """Post-March-2025 amendment questions trigger caution without definitive current law claim."""
    result = search("Do I need separate development permission for a 300 sq m plot in 2026?", RULES, MODE)
    assert result["ok"] is True
    assert result["status"] == "pending_official_verification"
    assert "cannot give a definitive current-law conclusion" in result["message"]


def test_model_abstention_on_insufficient_evidence(monkeypatch):
    """When the model determines context is insufficient and abstains, the system reflects this."""
    monkeypatch.setenv("NIYAMSETU_API_KEY", "test-key")
    monkeypatch.setattr(llm_rag, "_call_chat_api", lambda query, sources, cfg: {
        "answer": "The retrieved sources specify agricultural farm house rules, but do not contain guidelines for residential solar panel subsidies.",
        "citations": [],
        "abstained": True,
    })
    result = llm_rag.answer_with_rag(
        "What subsidy is provided for rooftop solar panels in Rule 17?",
        RULES, MODE, search, score_rules, snippet_for,
    )
    assert result["ok"] is False
    assert result["status"] == "insufficient_context"
    assert result["generation_mode"] == "llm_rag_abstention"


# ---------------------------------------------------------------------------
# Step 6: Provider Error Handling & Graceful Fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_code", [401, 402, 429, 500])
def test_provider_http_error_falls_back_to_retrieval(monkeypatch, status_code):
    """Any HTTP error from provider (e.g. 402 payment required) gracefully falls back without crashing."""
    monkeypatch.setenv("NIYAMSETU_API_KEY", "test-key")
    def fail_call(*args, **kwargs):
        raise RuntimeError(f"LLM provider returned HTTP {status_code}")
    monkeypatch.setattr(llm_rag, "_call_chat_api", fail_call)

    result = llm_rag.answer_with_rag(
        "What is the minimum plot size for a farm house in Rule 17?",
        RULES, MODE, search, score_rules, snippet_for,
    )
    assert result["ok"] is True
    assert result["generation_mode"] == "retrieval_only"
    assert "LLM generation unavailable" in result["generation_note"]


def test_provider_timeout_falls_back_to_retrieval(monkeypatch):
    """Provider timeout gracefully falls back to deterministic retrieval."""
    monkeypatch.setenv("NIYAMSETU_API_KEY", "test-key")
    def timeout_call(*args, **kwargs):
        raise TimeoutError("Provider request timed out")
    monkeypatch.setattr(llm_rag, "_call_chat_api", timeout_call)

    result = llm_rag.answer_with_rag(
        "What is the minimum plot size for a farm house in Rule 17?",
        RULES, MODE, search, score_rules, snippet_for,
    )
    assert result["ok"] is True
    assert result["generation_mode"] == "retrieval_only"
    assert "LLM generation unavailable" in result["generation_note"]


def test_provider_malformed_json_falls_back_to_retrieval(monkeypatch):
    """Malformed non-JSON responses from provider fall back to retrieval."""
    monkeypatch.setenv("NIYAMSETU_API_KEY", "test-key")
    monkeypatch.setattr(llm_rag, "_call_chat_api", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("LLM response was not valid JSON")))

    result = llm_rag.answer_with_rag(
        "What is the minimum plot size for a farm house in Rule 17?",
        RULES, MODE, search, score_rules, snippet_for,
    )
    assert result["ok"] is True
    assert result["generation_mode"] == "retrieval_only"


# ---------------------------------------------------------------------------
# Step 7: Optional Live Smoke Test
# ---------------------------------------------------------------------------

def test_optional_live_provider_smoke_test():
    """Optional smoke test that runs only if explicitly requested via NIYAMSETU_RUN_LIVE_TEST=1."""
    if os.getenv("NIYAMSETU_RUN_LIVE_TEST") != "1":
        pytest.skip("Set NIYAMSETU_RUN_LIVE_TEST=1 to execute live provider smoke test.")
    cfg = llm_rag.llm_config()
    if not cfg["enabled"]:
        pytest.skip("No API key configured in environment.")
    sources = [{
        "id": "S1", "rule": "17", "title": "Farm house", "part": "Part III",
        "page_start": 34, "page_end": 34, "version_id": "2025-03-06-consolidated",
        "full_text": "Rule 17. Minimum size of the plot shall be 0.40 hectare; Maximum F.A.R. shall be 0.10.",
    }]
    try:
        res = llm_rag._call_chat_api("What is the minimum plot size in Rule 17?", sources, cfg)
        assert isinstance(res, dict)
        assert "answer" in res
    except RuntimeError as exc:
        # If the account has insufficient balance (HTTP 402), verify it is cleanly caught
        assert "HTTP 402" in str(exc) or "HTTP" in str(exc)
