from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

ICA_COMPANIES_DATASET_ID = "ica_companies"
ICA_COMPANIES_RESOURCE_ID = "f004176c-b85f-4542-8901-7b3176f9a054"
ACTIVE_STATUSES = {"פעילה"}
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\-_,.;:~\"'`()\[\]{}\\/]+")
_HEBREW_COMPANY_ID_RE = re.compile(r"(?:ח[\s\.\-]*פ|חפ|מס[\'\"\s]*חברה)?[\s:№#-]*([0-9]{8,9})")


def _client() -> httpx.Client:
    settings = get_settings()
    timeout = httpx.Timeout(settings.company_registry_timeout_seconds)
    return httpx.Client(timeout=timeout)


def extract_company_number(query: str | None) -> str | None:
    text = str(query or "").strip()
    if not text:
        return None
    match = _HEBREW_COMPANY_ID_RE.search(text)
    if match:
        return match.group(1)
    digits = "".join(ch for ch in text if ch.isdigit())
    if 8 <= len(digits) <= 9:
        return digits
    return None


def normalize_company_name(value: str | None) -> str:
    text = str(value or "").strip().casefold()
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = text.replace(" בע מ", " בעמ").replace(" בע\"מ", " בעמ").replace(" בע~מ", " בעמ")
    return text


def _company_candidate(record: dict[str, Any]) -> dict[str, Any]:
    company_number = str(record.get("מספר חברה") or "").strip()
    company_name = str(record.get("שם חברה") or "").strip()
    company_status = str(record.get("סטטוס חברה") or "").strip()
    return {
        "company_number": company_number,
        "company_name": company_name,
        "company_status": company_status,
        "company_type": str(record.get("סוג תאגיד") or "").strip() or None,
        "city": str(record.get("שם עיר") or "").strip() or None,
        "is_active": company_status in ACTIVE_STATUSES,
    }


def _request_datastore(params: dict[str, Any]) -> list[dict[str, Any]]:
    base_url = "https://data.gov.il/api/3/action/datastore_search"
    final_params = {"resource_id": ICA_COMPANIES_RESOURCE_ID, **params}
    with _client() as client:
        response = client.get(base_url, params=final_params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("success"):
        return []
    result = payload.get("result", {})
    records = result.get("records", [])
    return records if isinstance(records, list) else []


def search_companies(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    text = str(query or "").strip()
    if not text:
        return []
    company_number = extract_company_number(text)
    if company_number:
        records = _request_datastore({"filters": {"מספר חברה": int(company_number)}, "limit": 5})
        return [_company_candidate(record) for record in records if isinstance(record, dict)]
    records = _request_datastore({"q": text, "limit": max(1, min(limit, 10))})
    return [_company_candidate(record) for record in records if isinstance(record, dict)]


def resolve_company_query(query: str, *, limit: int = 5) -> dict[str, Any]:
    text = str(query or "").strip()
    if not text:
        return {"match_status": "none", "candidates": []}
    candidates = search_companies(text, limit=limit)
    if not candidates:
        return {"match_status": "none", "candidates": []}

    company_number = extract_company_number(text)
    if company_number:
        exact = [candidate for candidate in candidates if candidate.get("company_number") == company_number]
        if len(exact) == 1:
            return {"match_status": "exact", "candidate": exact[0], "candidates": exact}

    normalized_query = normalize_company_name(text)
    exact_name_matches = [
        candidate for candidate in candidates if normalize_company_name(candidate.get("company_name")) == normalized_query
    ]
    if len(exact_name_matches) == 1:
        return {"match_status": "exact", "candidate": exact_name_matches[0], "candidates": exact_name_matches}

    if len(candidates) == 1:
        return {"match_status": "exact", "candidate": candidates[0], "candidates": candidates}

    active_candidates = [candidate for candidate in candidates if candidate.get("is_active")]
    if len(active_candidates) == 1:
        return {"match_status": "exact", "candidate": active_candidates[0], "candidates": active_candidates}

    return {"match_status": "ambiguous", "candidates": candidates[: max(1, min(limit, 5))]}
