"""Provider-agnostic LLM-grounded RAG for NiyamSetu.

Supports:
1. DeepSeek / OpenAI-compatible Chat Completions API
2. Amazon Bedrock Runtime Converse API (AWS SDK / boto3)

The evidence retrieval and legal-version guard remain deterministic. An LLM is only
called after retrieval succeeds and the legal guard does not require abstention.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Load simple KEY=VALUE entries from .env without adding an external dependency."""
    path = ROOT / ".env"
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_dotenv()


def llm_config() -> dict[str, Any]:
    """Resolve active LLM provider configuration from environment variables."""
    provider = os.getenv("NIYAMSETU_LLM_PROVIDER", "").strip().lower()
    timeout = os.getenv("NIYAMSETU_LLM_TIMEOUT", "35").strip()
    try:
        timeout_s = max(5, min(60, int(timeout)))
    except ValueError:
        timeout_s = 35

    # Auto-detection if NIYAMSETU_LLM_PROVIDER is not explicitly specified
    if not provider:
        if os.getenv("NIYAMSETU_BEDROCK_MODEL_ID") or (os.getenv("AWS_ACCESS_KEY_ID") and not os.getenv("NIYAMSETU_API_KEY")):
            provider = "bedrock"
        else:
            provider = "deepseek"

    if provider in ("bedrock", "amazon_bedrock", "aws_bedrock"):
        aws_region = (
            os.getenv("AWS_REGION", "").strip()
            or os.getenv("AWS_DEFAULT_REGION", "").strip()
            or "us-east-1"
        )
        model_id = (
            os.getenv("NIYAMSETU_BEDROCK_MODEL_ID", "").strip()
            or "anthropic.claude-3-5-sonnet-20241022-v2:0"
        )
        aws_creds_present = bool(
            os.getenv("AWS_ACCESS_KEY_ID")
            or os.getenv("AWS_PROFILE")
            or (Path.home() / ".aws" / "credentials").exists()
            or (Path.home() / ".aws" / "config").exists()
            or os.getenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
            or os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE")
            or os.getenv("NIYAMSETU_BEDROCK_ENABLED") == "1"
        )
        return {
            "provider": "bedrock",
            "enabled": aws_creds_present,
            "aws_region": aws_region,
            "model": model_id,
            "timeout": timeout_s,
            "provider_label": f"Amazon Bedrock ({model_id} in {aws_region})",
        }

    # Default provider: DeepSeek / OpenAI-compatible
    api_key = os.getenv("NIYAMSETU_API_KEY", "").strip() or os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("NIYAMSETU_LLM_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
    model = os.getenv("NIYAMSETU_LLM_MODEL", "deepseek-flash").strip() or "deepseek-flash"
    return {
        "provider": "deepseek",
        "enabled": bool(api_key),
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "timeout": timeout_s,
        "provider_label": os.getenv("NIYAMSETU_LLM_PROVIDER_LABEL", "DeepSeek / OpenAI-compatible API").strip(),
    }


def llm_public_status() -> dict[str, Any]:
    """Safe public status reporting without exposing API keys or secrets."""
    cfg = llm_config()
    return {
        "enabled": cfg["enabled"],
        "provider": cfg["provider_label"] if cfg["enabled"] else None,
        "provider_type": cfg["provider"],
        "model": cfg["model"] if cfg["enabled"] else None,
        "mode": "llm_rag" if cfg["enabled"] else "retrieval_only",
        "privacy_note": (
            f"Queries and retrieved legal passages are sent to {cfg['provider_label']} for explanation."
            if cfg["enabled"] else
            "No question is sent to an LLM. Configure API credentials or Amazon Bedrock to enable LLM-grounded answers."
        ),
    }


def _norm_rule_id(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _source_passage(query: str, rule: dict[str, Any], snippet_for) -> str:
    text = str(rule.get("text", ""))
    selected = snippet_for(query, rule)
    if len(text) <= 1200:
        return text
    if selected and selected in text:
        return selected
    return text[:1000]


def _retrieved_sources(query: str, rules: list[dict[str, Any]], score_rules, snippet_for, top_k: int = 4) -> list[dict[str, Any]]:
    ranked = score_rules(query, rules)
    if not ranked:
        return []
    selected: list[tuple[float, dict[str, Any]]] = []
    seen = set()
    for score, rule in ranked:
        if score < 1.4:
            continue
        key = _norm_rule_id(rule.get("rule"))
        if key in seen:
            continue
        seen.add(key)
        selected.append((score, rule))
        if len(selected) >= top_k:
            break
    sources = []
    from niyamsetu import source_link, official_pdf_link
    for index, (score, rule) in enumerate(selected, 1):
        passage = _source_passage(query, rule, snippet_for)
        if not passage or passage not in str(rule.get("text", "")):
            continue
        p_start = rule.get("page_start")
        p_end = rule.get("page_end", p_start)
        sources.append({
            "id": f"S{index}",
            "rule": str(rule.get("rule", "")),
            "title": str(rule.get("title", "")),
            "part": str(rule.get("part", "")),
            "page_start": p_start if isinstance(p_start, int) else None,
            "page_end": p_end if isinstance(p_end, int) else None,
            "version_id": str(rule.get("version_id", "2025-03-06-consolidated")),
            "record_scope": str(rule.get("record_scope", "uploaded_dataset_record")),
            "source_url": source_link(rule, prefer_local=True),
            "official_pdf_url": official_pdf_link(rule),
            "score": round(float(score), 2),
            "passage": passage,
            "passage_matches_loaded_record": True,
            "full_text": str(rule.get("text", ""))[:6000],
        })
    return sources


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        obj = json.loads(match.group(0))
        if isinstance(obj, dict):
            return obj
    raise ValueError("LLM response was not a valid JSON object")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in re.findall(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)", text or "")}


def _validate_llm_output(payload: dict[str, Any], query: str, sources: list[dict[str, Any]]) -> tuple[bool, str]:
    answer = payload.get("answer")
    citations = payload.get("citations")
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 3500:
        return False, "The model did not return a usable answer."
    if not isinstance(citations, list):
        return False, "The model did not return a citations list."
    abstained = payload.get("abstained") is True
    if not citations and not abstained:
        return False, "The model did not cite retrieved evidence."

    valid_ids = {s["id"] for s in sources}
    if any(not isinstance(c, str) or c not in valid_ids for c in citations):
        return False, "The model cited an item outside the retrieved evidence."

    # Guard against model-generated external URLs
    if re.search(r"https?://|www\.", answer, re.I):
        return False, "The model included external URLs in the response; citations must use source identifiers only."

    cited_sources = [s for s in sources if s["id"] in citations]

    # Guard against model-invented rule references (evaluated before generic numeric check)
    mentioned_rules = set(re.findall(r"\b(?:rule|नियम)\s*[-:#]?\s*(\d+[a-zA-Z]?(?:\([^)]+\)){0,2})\b", answer, re.I))
    if mentioned_rules:
        from niyamsetu import explicit_rule
        allowed_rules = {_norm_rule_id(s["rule"]) for s in cited_sources}
        q_exp = explicit_rule(query)
        if q_exp:
            allowed_rules.add(_norm_rule_id(q_exp))
        unsupported_rules = {_norm_rule_id(r) for r in mentioned_rules} - allowed_rules
        if unsupported_rules:
            return False, f"The model cited rule(s) {sorted(unsupported_rules)} outside the retrieved evidence."

    # Guard against model-invented page numbers (evaluated before generic numeric check)
    mentioned_pages = set()
    for m in re.finditer(r"\b(?:pages?|p\.|pp\.|पृष्ठ)\s*(\d+)(?:\s*(?:-|to|–)\s*(\d+))?\b", answer, re.I):
        mentioned_pages.add(int(m.group(1)))
        if m.group(2):
            mentioned_pages.add(int(m.group(2)))
    if mentioned_pages:
        allowed_pages = set()
        for s in cited_sources:
            p_start = s.get("page_start")
            p_end = s.get("page_end", p_start)
            if isinstance(p_start, int):
                if isinstance(p_end, int) and p_end >= p_start:
                    allowed_pages.update(range(p_start, p_end + 1))
                else:
                    allowed_pages.add(p_start)
        for m in re.finditer(r"\b(?:pages?|p\.|pp\.|पृष्ठ)\s*(\d+)\b", query, re.I):
            allowed_pages.add(int(m.group(1)))
        unsupported_pages = mentioned_pages - allowed_pages
        if unsupported_pages:
            return False, f"The model mentioned page(s) {sorted(unsupported_pages)} not matching the cited evidence."

    # Numeric tokens check
    allowed_numbers = _numbers(query + "\n" + "\n".join(s["full_text"] for s in cited_sources))
    unsupported_numbers = _numbers(answer) - allowed_numbers
    if unsupported_numbers:
        return False, f"The model introduced numerical values not found in the question or cited source text: {sorted(unsupported_numbers)}"

    # Deterministic guard against common definitive-current-law language
    unsafe_current_claims = [
        r"\bunder current (?:law|rules),? (?:you|the applicant|construction|the plot)",
        r"\bcurrent law (?:allows?|permits?|requires?|exempts?)\b",
        r"\bno (?:separate )?(?:development )?permission (?:is|required|will be required)\b",
        r"\byou (?:can|may) legally (?:build|construct|develop)\b",
        r"\byou are (?:allowed|permitted) to (?:build|construct|develop)\b",
        r"\bis exempt from (?:separate )?(?:development )?permission\b",
        r"\bdefinitively (?:valid|current|applicable|permitted)\b",
    ]
    lowered = answer.casefold()
    if any(re.search(pattern, lowered, re.I) for pattern in unsafe_current_claims):
        return False, "The answer appears to make a definitive permission/current-law claim unsupported by the verified legal-version status."

    # Substantive claim support check (when not abstained)
    if not abstained and cited_sources:
        from niyamsetu import tokens
        extra_stop = set("according historical supplied text states excerpt rule rules section sub-rule provision provisions provides requirement requirements based cited record records note notes mp madhya pradesh bhumi vikas minimum maximum shall may will can".split())
        ans_tokens = set(tokens(answer)) - extra_stop
        if ans_tokens:
            has_overlap = False
            for s in cited_sources:
                source_tokens = set(tokens(str(s.get("title", "")) + " " + str(s.get("full_text", ""))))
                overlap = ans_tokens & source_tokens
                if len(overlap) >= min(2, len(ans_tokens)):
                    has_overlap = True
                    break
            if not has_overlap:
                return False, "The cited source records do not contain the substantive terms supporting the claims in the answer."

    return True, ""


SYSTEM_PROMPT = """You are NiyamSetu, an evidence-grounded assistant for finding Madhya Pradesh building-rule passages.
You are NOT a lawyer, legal authority, or the source of legal facts. You must use ONLY the retrieved source records supplied by the user message.
The supplied consolidated rules are dated 6 March 2025 and are historical_only because later amendments have not been fully reconciled. Do NOT state or imply that these records establish current law. Do NOT advise that permission is or is not required for a specific plot if the relevant amendment/applicability is unresolved.
Do not invent or assume provisions, sub-rules, page references, values, dates, conditions, fees, or legal conclusions. Do not perform legal calculations.
If the retrieved passages do not contain enough information to answer a question or if evidence is incomplete, clearly state what is known from the source, what is missing/unknown, and what additional official document or authority is needed.
If a question makes an assumption contrary to the retrieved rules (e.g. asking why a non-existent rule allows something), refute the assumption using the source text.
Return ONLY a valid JSON object with exactly these keys: {"answer": string, "citations": ["S1"], "abstained": boolean}. Citations must use only source_id values provided. Cite only records that directly support the answer. If the sources do not support an answer, the answer must explicitly say this and citations can be an empty list. Never claim the application verified current law."""


def _build_user_payload(query: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = []
    for source in sources:
        pdf_pages = f'{source["page_start"]}-{source["page_end"]}' if source.get("page_start") is not None else "unindexed"
        evidence.append({
            "source_id": source["id"],
            "rule": source["rule"],
            "title": source["title"],
            "part": source["part"],
            "pdf_pages": pdf_pages,
            "version_id": source["version_id"],
            "verification_status": "historical_only; current law is not established by this dataset",
            "text": source["full_text"],
        })
    return {
        "question": query,
        "instructions": [
            "Answer from retrieved evidence only.",
            "Numerical values must occur in the query or cited source text.",
            "Cite relevant source IDs in JSON.",
            "The app will independently validate source IDs and numeric tokens.",
        ],
        "retrieved_sources": evidence,
    }


def _call_chat_api(query: str, sources: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    """Execute chat completion request against an OpenAI-compatible endpoint (e.g. DeepSeek)."""
    endpoint = cfg["base_url"]
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    user_payload = _build_user_payload(query, sources)
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": 700,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg["api_key"],
            "User-Agent": "NiyamSetu/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg["timeout"]) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"LLM provider returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("LLM provider could not be reached") from None
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("LLM provider response did not contain a chat completion") from None
    return _extract_json(str(content or ""))


def _call_bedrock_converse(query: str, sources: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    """Execute generation request against Amazon Bedrock using the Bedrock Runtime Converse API."""
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import (
            NoCredentialsError, PartialCredentialsError, ClientError, EndpointConnectionError
        )
    except ImportError:
        raise RuntimeError("boto3 is not installed; install boto3 to use Amazon Bedrock")

    user_payload = _build_user_payload(query, sources)
    region = cfg.get("aws_region", "us-east-1")
    model_id = cfg.get("model", "anthropic.claude-3-5-sonnet-20241022-v2:0")
    timeout = cfg.get("timeout", 35)

    client_config = Config(
        connect_timeout=min(10, timeout),
        read_timeout=timeout,
        retries={"max_attempts": 2, "mode": "standard"},
    )

    try:
        # Standard AWS credential provider chain
        client = boto3.client("bedrock-runtime", region_name=region, config=client_config)
        response = client.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": json.dumps(user_payload, ensure_ascii=False)}],
                }
            ],
            system=[{"text": SYSTEM_PROMPT}],
            inferenceConfig={
                "temperature": 0.1,
                "maxTokens": 700,
            },
        )
        output_message = response["output"]["message"]
        content_text = output_message["content"][0]["text"]
        return _extract_json(content_text)
    except (NoCredentialsError, PartialCredentialsError):
        raise RuntimeError("Amazon Bedrock authentication failed: no valid AWS credentials found in environment or credential chain.")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        if code == "AccessDeniedException":
            raise RuntimeError(f"Amazon Bedrock access denied (model '{model_id}' in '{region}'). Ensure IAM permissions include bedrock:InvokeModel.")
        elif code in ("ResourceNotFoundException", "ValidationException"):
            raise RuntimeError(f"Amazon Bedrock model '{model_id}' not found or unavailable in region '{region}'.")
        elif code == "ThrottlingException":
            raise RuntimeError("Amazon Bedrock request throttled by AWS.")
        else:
            raise RuntimeError(f"Amazon Bedrock error ({code}).")
    except EndpointConnectionError:
        raise RuntimeError(f"Could not connect to Amazon Bedrock endpoint in region '{region}'. Check network and AWS region.")
    except Exception as exc:
        if "timeout" in str(exc).lower() or type(exc).__name__ in ("ReadTimeoutError", "ConnectTimeoutError", "TimeoutError"):
            raise RuntimeError("Amazon Bedrock request timed out.")
        raise RuntimeError(f"Amazon Bedrock request failed: {type(exc).__name__}")


def answer_with_rag(query: str, rules: list[dict[str, Any]], dataset_mode: str, deterministic_search, score_rules, snippet_for) -> dict[str, Any]:
    """Run retrieval first, then grounded generation with deterministic safety gates."""
    base = deterministic_search(query, rules, dataset_mode)
    config = llm_config()
    base["generation_mode"] = "retrieval_only"
    base["llm_enabled"] = bool(config["enabled"])
    base["llm_model"] = config["model"] if config["enabled"] else None
    base["llm_provider"] = config["provider"]
    if not base.get("ok"):
        base["generation_note"] = "The question did not retrieve sufficient approved evidence; the LLM was not called."
        return base
    if base.get("status") in {"pending_official_verification", "conflicted"}:
        base["generation_note"] = "The legal-version guard requires a non-generative warning; the LLM was not called."
        return base
    if not config["enabled"]:
        if config.get("provider") == "bedrock":
            base["generation_note"] = "No AWS credentials configured; using evidence retrieval without LLM generation."
        else:
            base["generation_note"] = "No API key is configured; using evidence retrieval without LLM generation."
        return base

    sources = _retrieved_sources(query, rules, score_rules, snippet_for, top_k=4)
    if not sources:
        base["generation_note"] = "No validated retrieval context was available; the LLM was not called."
        return base
    try:
        if config["provider"] == "bedrock":
            payload = _call_bedrock_converse(query, sources, config)
        else:
            payload = _call_chat_api(query, sources, config)

        valid, reason = _validate_llm_output(payload, query, sources)
        if not valid:
            base["generation_note"] = "LLM output failed validation: " + reason + " Showing retrieval-only result instead."
            return base
        answer = payload["answer"].strip()
        citations = payload.get("citations", [])
        if payload.get("abstained") is True:
            base["ok"] = False
            base["type"] = "insufficient_context"
            base["status"] = "insufficient_context"
            base["message"] = answer
            base["generation_mode"] = "llm_rag_abstention"
            base["sources"] = [s for s in sources if s["id"] in citations]
            base["generation_note"] = "The LLM abstained based on the retrieved evidence."
            return base
        cited = [s for s in sources if s["id"] in citations]
        if not cited:
            base["generation_note"] = "LLM output contained no usable citations. Showing retrieval-only result instead."
            return base
        base["message"] = answer
        base["generation_mode"] = "llm_rag"
        base["llm_used"] = True
        base["citations"] = [s["id"] for s in cited]
        base["sources"] = [
            {k: v for k, v in source.items() if k != "full_text"}
            for source in cited
        ]
        primary = cited[0]
        base["rule"] = {
            "rule": primary["rule"], "title": primary["title"], "part": primary["part"],
            "page_start": primary["page_start"], "page_end": primary["page_end"],
            "source_url": primary["source_url"],
            "official_pdf_url": primary.get("official_pdf_url"),
            "version_id": primary["version_id"],
            "record_scope": primary["record_scope"], "passage": primary["passage"],
            "passage_matches_loaded_record": True, "score": primary["score"],
        }
        base["generation_note"] = f"LLM answer generated from retrieved source records using {config['provider_label']}; citations and numerical tokens were validated in code. Semantic legal support still requires human review."
        return base
    except Exception as exc:
        base["generation_note"] = f"LLM generation unavailable ({type(exc).__name__}). Showing retrieval-only result instead."
        return base
