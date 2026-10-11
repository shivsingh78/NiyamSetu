from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from niyamsetu import load_rules, search, explicit_rule, normalise

RULES, MODE = load_rules(prefer_demo=True)


def test_demo_dataset_loads_with_provenance():
    assert MODE == "demo_excerpts"
    assert len(RULES) == 5
    assert all(r["version_id"] == "2025-03-06-consolidated" for r in RULES)
    assert all(r["record_scope"] == "demo_excerpt" for r in RULES)


def test_explicit_rule_reference():
    assert explicit_rule("What does Rule 56 say?") == "56"
    assert explicit_rule("Explain Rule 2(5)(c)") == "2(5)(C)"


def test_front_space_returns_historical_table_value_and_source():
    result = search("My house faces a 10 m wide street and will be 9 m tall. How much front open space does the supplied Rule 56 excerpt state?", RULES, MODE)
    assert result["ok"] is True
    assert result["status"] == "historical_only"
    assert "3.6 meters" in result["message"]
    assert result["rule"]["rule"] == "56"
    assert result["rule"]["page_start"] == 77
    assert result["rule"]["passage_matches_loaded_record"] is True


def test_rear_space_150_square_metres():
    result = search("For a 150 sq m residential plot, what does the supplied Rule 56 excerpt say about rear open space?", RULES, MODE)
    assert result["ok"] is True
    assert "1.50 meters" in result["message"]
    assert result["rule"]["passage"] in next(r["text"] for r in RULES if r["rule"] == "56")


def test_rule17_farm_house():
    result = search("I want a farm house on agricultural land. What minimum plot size and maximum FAR are listed in the supplied Rule 17 excerpt?", RULES, MODE)
    assert result["ok"] is True
    assert result["rule"]["rule"] == "17"
    assert "0.40 hectare" in result["message"]
    assert "0.10" in result["message"]


def test_electric_line_clearance():
    result = search("How far must a building stay from low-voltage overhead electric lines according to the supplied Rule 52 excerpt?", RULES, MODE)
    assert result["ok"] is True
    assert result["rule"]["rule"] == "52"
    assert "2.5 meters" in result["message"]
    assert "1.2 meters" in result["message"]


def test_unverified_2026_permission_question_is_flagged():
    result = search("Do I need separate development permission for a 300 sq m plot that matches the Development Plan in 2026?", RULES, MODE)
    assert result["ok"] is True
    assert result["status"] == "pending_official_verification"
    assert "cannot give a definitive current-law conclusion" in result["message"]


def test_unknown_rule_does_not_fall_through_to_random_result():
    result = search("What does Rule 104 say?", RULES, MODE)
    assert result["ok"] is False
    assert result["type"] == "not_found"


def test_unsupported_property_tax_question_abstains():
    result = search("How do I pay my property tax online?", RULES, MODE)
    assert result["ok"] is False
    assert result["type"] == "not_found"


def test_current_fee_amount_is_not_invented():
    result = search("What is the current building permission fee for a residential house?", RULES, MODE)
    assert result["ok"] is False
    assert "cannot provide a fee amount" in result["message"]


def test_quote_is_substring_of_stored_record():
    result = search("Rule 52 distance from electric lines", RULES, MODE)
    assert result["ok"] is True
    record = next(r for r in RULES if r["rule"] == result["rule"]["rule"])
    assert result["rule"]["passage"] in record["text"]


def test_hindi_query_is_understood_but_asks_for_missing_height():
    result = search("मेरा प्लॉट 200 वर्ग मीटर का है और सामने 9 मीटर चौड़ी सड़क है। आगे कितनी जगह खाली छोड़नी होगी?", RULES, MODE)
    assert result["ok"] is True
    assert result["rule"]["rule"] == "56"
    assert "building height" in result["message"]


def test_direct_reported_amendment_question_is_pending_not_invented():
    result = search("What changed in Rule 2(5)(c) in 2026?", RULES, MODE)
    assert result["ok"] is False
    assert result["type"] == "pending_official_verification"
    assert "primary notification" in result["message"]
