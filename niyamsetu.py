"""NiyamSetu evidence-first retrieval and response engine (stdlib only)."""
from __future__ import annotations
import json, os, re, unicodedata, urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
FULL_DATA = DATA / "bhumi_vikas_rules.jsonl"
DEMO_DATA = DATA / "demo_rules.jsonl"
MANIFEST_PATH = DATA / "legal_sources.json"
LOCAL_PDF_FILE = DATA / "bvn06032025E.pdf"

# Canonical URLs (centralized source registry)
OFFICIAL_PDF_URL = "https://www.mptownplan.gov.in/NewsPDF/bvn06032025E.pdf"
RULES_LISTING_URL = "https://mptownplan.gov.in/rule.aspx"
LOCAL_PDF_URL = "/api/pdf"
PDF_URL = OFFICIAL_PDF_URL
BASE_VERSION = "Rules consolidated to 6 March 2025"

STOPWORDS = set("a an the and or of in on to for is are was were do does did i my me we our you your can could would should what how where which who when why rule rules about tell give show need required please for with from by at this that it as be bea have has had will shall a my plot house building construction permission current latest updated today".split())
HINDI_EXPANSIONS = {
    "सामने": "front open space",
    "आगे": "front open space",
    "खाली जगह": "open space",
    "खुली जगह": "open space",
    "सेटबैक": "setback open space",
    "पीछे": "rear open space",
    "पिछला": "rear open space",
    "सड़क": "street width road",
    "रास्ता": "street width road",
    "चौड़ी": "width",
    "चौड़ा": "width",
    "मकान": "residential building house",
    "भवन": "building",
    "घर": "residential building house",
    "आवासीय": "residential building",
    "प्लॉट": "plot area",
    "वर्ग मीटर": "square meters plot area",
    "कृषि": "agricultural farm house",
    "फार्म हाउस": "farm house agricultural",
    "बिजली": "electric lines voltage",
    "तार": "overhead electric line",
    "दूरी": "distance clearance",
    "मंजूरी": "permission sanction",
    "अनुमति": "permission approval",
    "परमिशन": "permission approval",
    "कितने साल": "duration sanction years",
    "वैध": "validity duration sanction",
    "फीस": "fee charges payment",
    "शुल्क": "fee charges",
}


def build_safe_pdf_url(base_url: str, page: int | None = None) -> str:
    """Preserve valid URL encoding (%20 and %26) without double-encoding, and append page fragment if given."""
    clean_base = (base_url or "").split("#")[0].strip()
    if not clean_base:
        clean_base = LOCAL_PDF_URL if LOCAL_PDF_FILE.exists() else OFFICIAL_PDF_URL
    parsed = urllib.parse.urlsplit(clean_base)
    unquoted_path = urllib.parse.unquote(parsed.path)
    safe_path = urllib.parse.quote(unquoted_path, safe="/").replace("&", "%26")
    safe_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, safe_path, parsed.query, ""))
    if page is not None and isinstance(page, int) and page > 0:
        return f"{safe_url}#page={page}"
    return safe_url


def source_link(rule: dict[str, Any], prefer_local: bool = True) -> str:
    page = rule.get("page_start")
    page_num = page if (isinstance(page, int) and page > 0) else None
    if prefer_local and LOCAL_PDF_FILE.exists():
        base = LOCAL_PDF_URL
    else:
        base = OFFICIAL_PDF_URL
    return build_safe_pdf_url(base, page_num)


def official_pdf_link(rule: dict[str, Any]) -> str:
    page = rule.get("page_start")
    page_num = page if (isinstance(page, int) and page > 0) else None
    return build_safe_pdf_url(OFFICIAL_PDF_URL, page_num)


def citation_metadata(rule: dict[str, Any]) -> dict[str, Any]:
    page_start = rule.get("page_start")
    page_end = rule.get("page_end", page_start)
    return {
        "rule": str(rule.get("rule", "")),
        "title": str(rule.get("title", "")),
        "part": str(rule.get("part", "")),
        "page_start": page_start if isinstance(page_start, int) else None,
        "page_end": page_end if isinstance(page_end, int) else None,
        "local_pdf_url": source_link(rule, prefer_local=True),
        "official_pdf_url": official_pdf_link(rule),
        "rules_listing_url": RULES_LISTING_URL,
        "version_id": str(rule.get("version_id", "2025-03-06-consolidated")),
        "status": "historical_only",
        "record_scope": str(rule.get("record_scope", "uploaded_dataset_record")),
    }


def read_manifest() -> dict[str, Any]:
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        bs = data.setdefault("base_source", {})
        bs["official_pdf_url"] = OFFICIAL_PDF_URL
        bs["rules_listing_url"] = RULES_LISTING_URL
        bs["local_pdf_url"] = LOCAL_PDF_URL
        bs["url"] = LOCAL_PDF_URL if LOCAL_PDF_FILE.exists() else OFFICIAL_PDF_URL
        return data
    except Exception:
        return {
            "overall_current_law_status": "not_fully_verified",
            "base_source": {
                "status": "historical_only",
                "version_id": "2025-03-06-consolidated",
                "title": BASE_VERSION,
                "url": LOCAL_PDF_URL if LOCAL_PDF_FILE.exists() else OFFICIAL_PDF_URL,
                "official_pdf_url": OFFICIAL_PDF_URL,
                "rules_listing_url": RULES_LISTING_URL,
                "local_pdf_url": LOCAL_PDF_URL,
            },
            "amendment_watchlist": []
        }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no}: {exc}") from exc
            if not isinstance(item, dict) or not str(item.get("rule", "")).strip() or not str(item.get("text", "")).strip():
                raise ValueError(f"Line {line_no} needs non-empty 'rule' and 'text' fields")
            item.setdefault("title", "Untitled rule")
            item.setdefault("page_start", None)
            item.setdefault("page_end", item.get("page_start"))
            item.setdefault("version_id", "2025-03-06-consolidated")
            item.setdefault("record_scope", "uploaded_dataset_record")
            rows.append(item)
    return rows


def load_rules(prefer_demo: bool = False, dataset_path: Path | None = None) -> tuple[list[dict[str, Any]], str]:
    """Prefer user-provided full dataset, otherwise use included verified-origin excerpts."""
    if dataset_path is not None:
        p = Path(dataset_path)
        if p.exists():
            return _load_jsonl(p), "custom_dataset"
    if not prefer_demo and os.environ.get("NIYAMSETU_FORCE_DEMO") != "1" and FULL_DATA.exists():
        try:
            rows = _load_jsonl(FULL_DATA)
            if rows:
                return rows, "full_dataset"
        except Exception:
            raise
    rows = _load_jsonl(DEMO_DATA)
    return rows, "demo_excerpts"


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"\bfar\b", "floor area ratio", s)
    return re.sub(r"\s+", " ", s).strip()


def expand_hindi(query: str) -> str:
    q = query
    for hindi, english in sorted(HINDI_EXPANSIONS.items(), key=lambda x: len(x[0]), reverse=True):
        if hindi in q:
            q += " " + english
    return q


def tokens(s: str) -> list[str]:
    raw = re.findall(r"[a-z]+(?:'[a-z]+)?|[\u0900-\u097f]+|\d+(?:\.\d+)?", normalise(s))
    return [t for t in raw if t not in STOPWORDS and not t.isdigit() and len(t) > 1]


def explicit_rule(query: str) -> str | None:
    q = query.lower()
    m = re.search(r"(?:rule|नियम)\s*[-:#]?\s*(\d+[a-zA-Z]?(?:\([^)]+\)){0,2})", q, re.I)
    if m:
        return re.sub(r"\s+", "", m.group(1)).upper()
    return None


def score_rules(query: str, rules: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    q_expanded = expand_hindi(query)
    qtokens = set(tokens(q_expanded))
    rref = explicit_rule(query)
    qn = normalise(q_expanded)
    is_def_query = any(d_term in qn for d_term in ("define", "definition", "meaning", "means", "परिभाषा", "किसे कहते", "का अर्थ"))
    scored: list[tuple[float, dict[str, Any]]] = []
    for rule in rules:
        rid = str(rule.get("rule", "")).upper().replace(" ", "")
        if rref and rid == rref:
            scored.append((1000.0, rule))
            continue
        doc = f"{rule.get('rule','')} {rule.get('title','')} {rule.get('part','')} {rule.get('text','')}"
        dtokens = set(tokens(doc))
        overlap = qtokens & dtokens
        if not qtokens:
            base = 0.0
        else:
            base = sum(1.4 if t in str(rule.get('title','')).lower() else 1.0 for t in overlap)
            dn = normalise(doc)
            for phrase in ("front open space", "rear open space", "electric lines", "farm house", "duration of sanction", "permission for development"):
                if phrase in qn and phrase in dn:
                    base += 2.5
            # Rule 2 is definitions. Prevent it from outranking operative rules unless specifically asking for definitions
            if rid == "2" and not is_def_query:
                base -= 3.0
        if overlap:
            scored.append((base, rule))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def requested_rule_not_found(query: str, rules: list[dict[str, Any]]) -> bool:
    ref = explicit_rule(query)
    if not ref:
        return False
    return not any(str(r.get("rule", "")).upper().replace(" ", "") == ref for r in rules)


def _query_has_current_law_risk(query: str, rule: dict[str, Any] | None) -> bool:
    q = normalise(query)
    triggers = ["current", "latest", "updated", "today", "2026", "500 sq", "500 sqm", "separate development permission", "do i need permission", "need permission", "permission required", "permission exemption", "बिना अनुमति", "अनुमति चाहिए", "वर्तमान"]
    rno = str((rule or {}).get("rule", ""))
    return any(t in q for t in triggers) or rno in {"2", "2(5)(c)", "12"} and any(w in q for w in ("permission", "construction", "निर्माण", "परमिशन", "अनुमति", "plot"))


def deterministic_hint(query: str, rule: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return a grounded historical hint and the exact local-record substring it relies on."""
    q = normalise(expand_hindi(query))
    r = str(rule.get("rule", ""))
    text = str(rule.get("text", ""))
    if r == "56" and any(w in q for w in ("front", "सामने", "आगे")) and any(w in q for w in ("street", "road", "street width", "सड़क", "चौड़ी")):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|meters?|metres?|मीटर)?\s*(?:(?:wide|चौड़ी|चौड़ा|चौड़े)\s*)?(?:street|road|सड़क|रास्ता)", q, re.I)
        if not m:
            m = re.search(r"(?:street|road|सड़क|रास्ता)[^\d]{0,25}(\d+(?:\.\d+)?)\s*(?:m|meters?|metres?|मीटर)?", q, re.I)
        height_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|meters?|metres?|मीटर)\s*(?:tall|high|height|ऊंचा|ऊँचा|ऊंची|ऊँची)", q, re.I)
        if not m:
            return ("I found the front-open-space table, but I could not confidently identify the street width. Please enter the street width and building height.", None)
        width = float(m.group(1))
        if not height_m:
            return ("The stored Rule 56 excerpt covers residential buildings up to 12.5 m high. Please confirm the building height before applying this historical table.", None)
        height = float(height_m.group(1))
        if height > 12.5:
            return ("The stored Rule 56 text says buildings above 12.5 m use Tables 4 and 5 of Rule 42. Those table cells are not verified in this MVP, so I cannot calculate the required open space.", None)
        
        if width <= 9:
            demo_quote = "street width up to 9.0 meters = minimum 3.0 meters"
            full_match = ("up to 9.0 meters", "3.0 meters")
            val_str = "3.0 meters"
        elif width <= 12:
            demo_quote = "more than 9.0 meters and up to 12 meters = minimum 3.6 meters"
            full_match = ("up to 12 meters", "3.6 meters")
            val_str = "3.6 meters"
        elif width <= 18:
            demo_quote = "more than 12.0 meters and up to 18 meters = minimum 4.5 meters"
            full_match = ("up to 18 meters", "4.5 meters")
            val_str = "4.5 meters"
        else:
            demo_quote = "above 18 meters = minimum 6.0 meters"
            full_match = ("above 18 meters", "6.0 meters")
            val_str = "6.0 meters"

        if demo_quote in text:
            quote = demo_quote
        else:
            quote = next((l.strip() for l in text.splitlines() if all(p.lower() in l.lower() for p in full_match)), None)
            
        if not quote:
            return ("The table value could not be validated against the loaded rule text; no numerical answer was produced.", None)
        return (f"In the supplied historical Rule 56 excerpt, a {width:g} m street falls in the row '{quote}'. The indicated minimum front open space is {val_str}. This is historical source evidence only; the current applicable rule has not been fully verified.", quote)

    if r == "56" and any(w in q for w in ("rear", "back open space", "पीछे", "पिछला")):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:sq\s*m|sqm|square\s*meters?|square\s*metres?|वर्ग\s*मीटर)", q, re.I)
        if not m:
            return ("I found the rear-open-space table, but could not identify the plot area. Please specify the plot area in square metres.", None)
        area = float(m.group(1))
        if area <= 40:
            demo_quote = "plot area up to 40 square meters = Nil"
            full_match = ("up to 40", "nil")
            val_str = "Nil"
        elif area <= 150:
            demo_quote = "above 40 and up to 150 square meters = 1.50 meters"
            full_match = ("150", "1.50")
            val_str = "1.50 meters"
        elif area <= 225:
            demo_quote = "above 150 and up to 225 square meters = 2.50 meters"
            full_match = ("225", "2.50")
            val_str = "2.50 meters"
        else:
            demo_quote = "above 225 square meters = 3.00 meters"
            full_match = ("above 225", "3.00")
            val_str = "3.00 meters"

        if demo_quote in text:
            quote = demo_quote
        else:
            quote = next((l.strip() for l in text.splitlines() if all(p.lower() in l.lower() for p in full_match)), None)

        if not quote:
            return ("The table value could not be validated against the loaded rule text; no numerical answer was produced.", None)
        return (f"In the supplied historical Rule 56 excerpt, a {area:g} sq m plot matches '{quote}'. The indicated minimum rear open space is {val_str}. This is not confirmation of today's applicable legal requirement.", quote)

    if r == "17" and any(w in q for w in ("farm house", "farmhouse", "कृषि", "फार्म हाउस")):
        demo_quote = "Minimum size of the plot shall be 0.40 hectare; (b) Maximum F.A.R. shall be 0.10"
        if demo_quote in text:
            return ("The supplied historical Rule 17 excerpt lists a minimum farm-house plot size of 0.40 hectare and maximum F.A.R. of 0.10, subject to the stated conditions. Current applicability is not verified.", demo_quote)
        if "0.40 hectare" in text and "0.10" in text:
            match_passage = next((p.strip() for p in re.split(r"(?<=[.;])\s+", text) if "0.40 hectare" in p), None)
            if match_passage:
                return ("The supplied historical Rule 17 excerpt lists a minimum farm-house plot size of 0.40 hectare and maximum F.A.R. of 0.10, subject to the stated conditions. Current applicability is not verified.", match_passage)

    if r == "52" and any(w in q for w in ("electric", "voltage", "बिजली", "तार")):
        demo_quote = "Low and Medium voltage lines and service lines: vertical 2.5 meters, horizontal 1.2 meters."
        if demo_quote in text:
            return ("The supplied historical Rule 52 excerpt lists clearance for low/medium voltage lines as 2.5 meters vertically and 1.2 meters horizontally, or the greater applicable requirement under Electricity Rules. Current applicability is not verified.", demo_quote)
        if "2.5" in text and "1.2" in text and "low and medium voltage" in text.lower():
            match_passage = next((p.strip() for p in text.splitlines() if "low and medium voltage" in p.lower() and "2.5" in p), None)
            if match_passage:
                return ("The supplied historical Rule 52 excerpt lists clearance for low/medium voltage lines as 2.5 meters vertically and 1.2 meters horizontally, or the greater applicable requirement under Electricity Rules. Current applicability is not verified.", match_passage)

    if r == "23" and any(w in q for w in ("duration", "validity", "renew", "sanction", "कितने साल", "वैध")):
        quote = "the sanction once accorded shall remain valid up to three years."
        if quote in text.lower():
            idx = text.lower().find(quote)
            exact_quote = text[idx:idx+len(quote)]
            return ("The supplied historical Rule 23 excerpt states a validity period of up to three years for the described development permission, with further conditions on revalidation. Current applicability is not verified.", exact_quote)
    return (None, None)


def snippet_for(query: str, rule: dict[str, Any]) -> str:
    text = str(rule.get("text", "")).strip()
    qtokens = set(tokens(expand_hindi(query)))
    pieces = re.split(r"(?<=[.;])\s+", text)
    scored = []
    for p in pieces:
        pt = set(tokens(p))
        score = len(qtokens & pt)
        if score:
            scored.append((score, p.strip()))
    if scored:
        p = max(scored, key=lambda x: (x[0], -len(x[1])))[1]
        if len(p) > 950:
            p = p[:950].rsplit(" ", 1)[0] + "..."
        return p
    return text[:900] + ("..." if len(text) > 900 else "")


def search(query: str, rules: list[dict[str, Any]] | None = None, dataset_mode: str | None = None) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "type": "empty", "message": "Enter a question to search the supplied rules."}
    if rules is None:
        rules, dataset_mode = load_rules()
    mode = dataset_mode or "demo_excerpts"
    qnorm = normalise(expand_hindi(query))

    fee_query = any(term in qnorm for term in ("fee", "fees", "charges", "शुल्क", "फीस"))
    if fee_query and not explicit_rule(query):
        return {"ok": False, "type": "not_found", "message": "This MVP has no independently verified, applicable fee table. It cannot provide a fee amount. Search the official source and have the applicable fee schedule checked by the responsible authority.", "query": query, "dataset_mode": mode}

    tax_query = any(term in qnorm for term in ("property tax", "tax online", "pay tax", "house tax", "संपत्ति कर", "गृह कर"))
    if tax_query and not explicit_rule(query):
        return {"ok": False, "type": "not_found", "message": "Property tax is not governed by the Madhya Pradesh Bhumi Vikas Rules, 2012 (which regulate building permissions and physical development standards). Property taxes are administered separately under municipal corporation acts. No relevant rule was found in this dataset.", "query": query, "dataset_mode": mode}

    qraw = normalise(query)
    amendment_sensitive = (
        any(term in qraw for term in ("2026", "latest", "current", "updated", "today", "500 sq", "500 sqm", "वर्तमान"))
        and any(term in qraw for term in ("permission", "construction", "development", "plot", "भवन", "निर्माण", "अनुमति", "परमिशन"))
    ) or ("separate development permission" in qraw)
    if requested_rule_not_found(query, rules):
        if amendment_sensitive or explicit_rule(query) in {"2(5)(C)", "2(5)", "2(C)"}:
            return {"ok": False, "type": "pending_official_verification", "status": "pending_official_verification",
                    "message": f"Rule {explicit_rule(query)} is not in the bundled excerpt dataset. A later amendment may be relevant, but its primary notification and effective applicability are not verified here. No legal conclusion was generated.",
                    "query": query, "dataset_mode": mode}
        return {"ok": False, "type": "not_found", "message": f"Rule {explicit_rule(query)} is not present in the currently loaded dataset. No legal answer was generated.", "query": query, "dataset_mode": mode}
    ranked = score_rules(query, rules)
    if not ranked or ranked[0][0] < 1.6:
        if amendment_sensitive:
            historical_permission = next((r for r in rules if str(r.get("rule", "")).upper().replace(" ", "") == "12"), None)
            if historical_permission is not None:
                ranked = [(1.6, historical_permission)]
            else:
                return {"ok": False, "type": "pending_official_verification", "status": "pending_official_verification",
                        "message": "This question may depend on post-March-2025 legal changes. The applicable primary notification has not been verified in this prototype, and no definitive current-law conclusion can be provided.",
                        "query": query, "dataset_mode": mode}
        else:
            return {"ok": False, "type": "not_found", "message": "I could not find sufficiently relevant evidence in the currently loaded data. No legal answer was generated.", "query": query, "dataset_mode": mode}

    top_score, top = ranked[0]
    risky = _query_has_current_law_risk(query, top) or amendment_sensitive
    hint, quote = deterministic_hint(query, top)
    passage = quote if quote and quote in str(top.get("text", "")) else snippet_for(query, top)
    raw_passage = passage[:-3] if passage.endswith('...') else passage
    quote_valid = raw_passage in str(top.get('text', ''))
    if not quote_valid:
        return {"ok": False, "type": "validation_failed", "message": "The retrieved passage failed the local text check. No answer was produced.", "query": query, "dataset_mode": mode}

    if risky:
        message = "I found potentially relevant historical text, but current legal applicability cannot be verified because this question may depend on post-March-2025 changes or unresolved permission rules. I cannot give a definitive current-law conclusion."
        status = "pending_official_verification"
        kind = "caution"
    elif hint:
        message = hint
        status = "historical_only"
        kind = "historical_evidence"
    else:
        message = "I found a relevant passage in the supplied March 6, 2025 consolidated text. It is historical evidence only; the available sources do not establish that this is the complete current law. Please review the cited page before relying on it."
        status = "historical_only"
        kind = "historical_evidence"

    page_start = top.get("page_start")
    page_end = top.get("page_end", page_start)
    result_rule = {
        "rule": str(top.get("rule", "")),
        "title": str(top.get("title", "")),
        "part": str(top.get("part", "")),
        "page_start": page_start if isinstance(page_start, int) else None,
        "page_end": page_end if isinstance(page_end, int) else None,
        "source_url": source_link(top, prefer_local=True),
        "official_pdf_url": official_pdf_link(top),
        "rules_listing_url": RULES_LISTING_URL,
        "version_id": str(top.get("version_id", "2025-03-06-consolidated")),
        "record_scope": str(top.get("record_scope", "uploaded_dataset_record")),
        "passage": passage,
        "passage_matches_loaded_record": quote_valid,
        "score": round(top_score, 2),
    }
    return {"ok": True, "type": kind, "message": message, "status": status, "query": query,
            "dataset_mode": mode, "rule": result_rule, "records_loaded": len(rules),
            "base_version": BASE_VERSION,
            "official_pdf_url": OFFICIAL_PDF_URL,
            "rules_listing_url": RULES_LISTING_URL,
            "local_pdf_url": LOCAL_PDF_URL,
            "warning": "This tool is a rule-finding aid, not an official legal determination. A text match does not by itself establish that a provision is current or applicable to a specific property."}


def health() -> dict[str, Any]:
    rules, mode = load_rules()
    manifest = read_manifest()
    return {"ok": True, "product": "NiyamSetu", "records_loaded": len(rules), "dataset_mode": mode,
            "base_version": BASE_VERSION, "current_law_status": manifest.get("overall_current_law_status", "not_fully_verified"),
            "official_pdf_url": OFFICIAL_PDF_URL, "rules_listing_url": RULES_LISTING_URL, "local_pdf_url": LOCAL_PDF_URL,
            "local_pdf_available": LOCAL_PDF_FILE.exists(),
            "amendment_watchlist_count": len(manifest.get("amendment_watchlist", []))}
