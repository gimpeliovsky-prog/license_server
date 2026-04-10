from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

ICA_COMPANIES_DATASET_ID = "ica_companies"
ICA_COMPANIES_RESOURCE_ID = "f004176c-b85f-4542-8901-7b3176f9a054"

FIELD_COMPANY_NUMBER = "\u05de\u05e1\u05e4\u05e8 \u05d7\u05d1\u05e8\u05d4"
FIELD_COMPANY_NAME = "\u05e9\u05dd \u05d7\u05d1\u05e8\u05d4"
FIELD_COMPANY_STATUS = "\u05e1\u05d8\u05d8\u05d5\u05e1 \u05d7\u05d1\u05e8\u05d4"
FIELD_COMPANY_TYPE = "\u05e1\u05d5\u05d2 \u05ea\u05d0\u05d2\u05d9\u05d3"
FIELD_CITY = "\u05e9\u05dd \u05e2\u05d9\u05e8"

ACTIVE_STATUSES = {"\u05e4\u05e2\u05d9\u05dc\u05d4"}
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\-_,.;:~\"'`()\[\]{}\\/]+")
_HEBREW_COMPANY_ID_RE = re.compile(
    r"(?:\u05d7[\s\.\-]*\u05e4|\u05d7\u05e4|\u05de\u05e1[\'\"\s]*\u05d7\u05d1\u05e8\u05d4)?[\s:\u2116#-]*([0-9]{8,9})"
)


def _client() -> httpx.Client:
    settings = get_settings()
    timeout = httpx.Timeout(settings.company_registry_timeout_seconds)
    return httpx.Client(timeout=timeout, headers={"User-Agent": "KadimaSoft-AI/1.0"})


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
    baam = "\u05d1\u05e2\u05de"
    text = text.replace(" בע מ", f" {baam}").replace(' בע"מ', f" {baam}").replace(" בע~מ", f" {baam}")
    return text


def _company_candidate(record: dict[str, Any]) -> dict[str, Any]:
    company_number = str(record.get(FIELD_COMPANY_NUMBER) or "").strip()
    company_name = str(record.get(FIELD_COMPANY_NAME) or "").strip()
    company_status = str(record.get(FIELD_COMPANY_STATUS) or "").strip()
    return {
        "company_number": company_number,
        "company_name": company_name,
        "company_status": company_status,
        "company_type": str(record.get(FIELD_COMPANY_TYPE) or "").strip() or None,
        "city": str(record.get(FIELD_CITY) or "").strip() or None,
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
        records = _request_datastore({"filters": {FIELD_COMPANY_NUMBER: int(company_number)}, "limit": 5})
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
