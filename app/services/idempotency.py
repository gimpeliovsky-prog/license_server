import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ERPIdempotencyEntry

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
LEGACY_IDEMPOTENCY_KEY_HEADER = "X-Idempotency-Key"
MAX_IDEMPOTENCY_KEY_LENGTH = 128


@dataclass(frozen=True)
class IdempotencyReplay:
    status_code: int
    content_type: str | None
    body: str


def extract_idempotency_key(request: Request) -> str | None:
    raw = request.headers.get(IDEMPOTENCY_KEY_HEADER) or request.headers.get(LEGACY_IDEMPOTENCY_KEY_HEADER)
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(status_code=400, detail="Idempotency key too long")
    return key


def build_request_hash(payload: dict | None) -> str:
    serialized = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def get_replay_if_match(
    db: Session,
    tenant_id: UUID,
    method: str,
    endpoint: str,
    idempotency_key: str,
    request_hash: str,
) -> IdempotencyReplay | None:
    existing = (
        db.query(ERPIdempotencyEntry)
        .filter(
            ERPIdempotencyEntry.tenant_id == tenant_id,
            ERPIdempotencyEntry.method == method,
            ERPIdempotencyEntry.endpoint == endpoint,
            ERPIdempotencyEntry.idempotency_key == idempotency_key,
        )
        .first()
    )
    if not existing:
        return None
    if existing.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="Idempotency key already used with different payload")
    return IdempotencyReplay(
        status_code=existing.response_status,
        content_type=existing.response_content_type,
        body=existing.response_body or "",
    )


def store_response(
    db: Session,
    tenant_id: UUID,
    method: str,
    endpoint: str,
    idempotency_key: str,
    request_hash: str,
    response_status: int,
    response_content_type: str | None,
    response_body: bytes,
) -> IdempotencyReplay | None:
    entry = ERPIdempotencyEntry(
        tenant_id=tenant_id,
        method=method,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        response_status=response_status,
        response_content_type=response_content_type,
        response_body=response_body.decode("utf-8", errors="replace"),
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return get_replay_if_match(db, tenant_id, method, endpoint, idempotency_key, request_hash)
    return None
