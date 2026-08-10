"""Evidence backends: Google Fact Check + web search + LLM judge."""

from __future__ import annotations

import time
from typing import Any

from bili_fact_checker.config import Settings
from bili_fact_checker.httputil import get_json, post_json, quote
from bili_fact_checker.providers import chat, extract_json_object


def search_google_factcheck(settings: Settings, query: str) -> dict[str, Any] | None:
    if not settings.google_factcheck_api_key or not query.strip():
        return None
    url = (
        "https://factchecktools.googleapis.com/v1alpha1/claims:search"
        f"?query={quote(query)}&key={settings.google_factcheck_api_key}"
    )
    try:
        data = get_json(url, proxy=settings.proxy, timeout=20)
    except Exception:
        return None
    claims = data.get("claims") or []
    if not claims:
        return None
    c = claims[0]
    review = (c.get("claimReview") or [{}])[0]
    return {
        "tier": "sourced_factcheck",
        "rating": review.get("textualRating", ""),
        "publisher": (review.get("publisher") or {}).get("name", ""),
        "url": review.get("url", ""),
        "claim_text": c.get("text", ""),
    }


def search_web(settings: Settings, query: str, limit: int = 5) -> list[dict[str, str]]:
    """Tier-1 evidence. Prefer SearXNG, then Tavily, else empty."""
    if settings.searxng_url and query.strip():
        try:
            url = (
                f"{settings.searxng_url}/search"
                f"?q={quote(query)}&format=json&language=zh-CN"
            )
            data = get_json(url, proxy=settings.proxy, timeout=20)
            out: list[dict[str, str]] = []
            for r in (data.get("results") or [])[:limit]:
                out.append(
                    {
                        "title": str(r.get("title") or ""),
                        "url": str(r.get("url") or ""),
                        "snippet": str(r.get("content") or r.get("snippet") or ""),
                    }
                )
            if out:
                return out
        except Exception:
            pass

    if settings.tavily_api_key and query.strip():
        try:
            data = post_json(
                "https://api.tavily.com/search",
                {
                    "api_key": settings.tavily_api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": limit,
                },
                proxy=settings.proxy,
                timeout=30,
            )
            out = []
            for r in data.get("results") or []:
                out.append(
                    {
                        "title": str(r.get("title") or ""),
                        "url": str(r.get("url") or ""),
                        "snippet": str(r.get("content") or ""),
                    }
                )
            return out
        except Exception:
            return []

    return []


def gather_evidence(settings: Settings, claim: dict[str, Any]) -> dict[str, Any]:
    q_en = claim.get("claim_en") or claim.get("claim_zh") or ""
    q_zh = claim.get("claim_zh") or ""

    fc = search_google_factcheck(settings, str(q_en))
    web = search_web(settings, str(q_zh or q_en))

    evidence: list[dict[str, Any]] = []
    if fc:
        evidence.append(fc)
    for w in web:
        evidence.append({"tier": "sourced_web", **w})

    return {
        "google_factcheck": fc,
        "web": web,
        "evidence": evidence,
        "has_sourced_evidence": bool(evidence),
    }


def judge_claim(settings: Settings, claim: dict[str, Any], bundled: dict[str, Any]) -> dict[str, Any]:
    """LLM verdict. Must label model_inference when no sourced evidence."""
    evidence = bundled.get("evidence") or []
    if not evidence:
        prompt = f"""没有外部证据。请仅基于常识给出谨慎判断。

声明：{claim.get('claim_zh')}
英文：{claim.get('claim_en')}

输出 JSON：
{{"verdict":"unverified|likely_true|likely_false|disputed",
  "confidence":0.0到1.0,
  "label":"model_inference",
  "rationale":"简短中文理由"}}
"""
        raw = chat(settings, prompt, system="你是谨慎的事实核查员。只输出 JSON。")
        obj = extract_json_object(raw)
        return {
            "verdict": str(obj.get("verdict") or "unverified"),
            "confidence": float(obj.get("confidence") or 0.2),
            "label": "model_inference",
            "rationale": str(obj.get("rationale") or "无外部证据，仅为模型推断"),
            "sources": [],
        }

    prompt = f"""根据下列证据裁决声明。只能依据证据，不要编造来源。

声明：{claim.get('claim_zh')}
英文：{claim.get('claim_en')}
证据 JSON：{evidence}

输出 JSON：
{{"verdict":"supported|refuted|disputed|unverified",
  "confidence":0.0到1.0,
  "label":"sourced_factcheck 或 sourced_web（取最高质量证据层）",
  "rationale":"简短中文理由并点明证据",
  "source_urls":["用到的 url"]}}
"""
    raw = chat(settings, prompt, system="你是事实核查员。只根据证据裁决，只输出 JSON。")
    obj = extract_json_object(raw)
    label = str(obj.get("label") or "")
    if "factcheck" in label:
        label = "sourced_factcheck"
    elif "web" in label:
        label = "sourced_web"
    else:
        label = "sourced_factcheck" if bundled.get("google_factcheck") else "sourced_web"

    urls = obj.get("source_urls") or []
    if not isinstance(urls, list):
        urls = []
    if not urls:
        urls = [e.get("url") for e in evidence if e.get("url")]

    return {
        "verdict": str(obj.get("verdict") or "unverified"),
        "confidence": float(obj.get("confidence") or 0.5),
        "label": label,
        "rationale": str(obj.get("rationale") or ""),
        "sources": [u for u in urls if u],
    }


def verify_claims(settings: Settings, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for i, claim in enumerate(claims):
        bundled = gather_evidence(settings, claim)
        judgment = judge_claim(settings, claim, bundled)
        results.append({**claim, **bundled, "judgment": judgment})
        time.sleep(0.3)
    return results
