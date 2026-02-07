from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ERPAllowlistEntry, ERPAllowlistType


@dataclass(frozen=True)
class Allowlist:
    doctypes: dict[str, str]
    methods: set[str]


def normalize_doctype(value: str) -> str:
    return " ".join(value.strip().split())


def normalize_method(value: str) -> str:
    return value.strip().upper()


def has_allowlist_entries(db: Session) -> bool:
    return db.query(ERPAllowlistEntry.id).first() is not None


def seed_allowlist_from_settings(db: Session) -> int:
    settings = get_settings()
    existing = {
        (entry.entry_type, entry.value)
        for entry in db.query(ERPAllowlistEntry).all()
    }
    entries: list[ERPAllowlistEntry] = []
    inserted = 0

    for raw in settings.erp_allowed_doctypes:
        normalized = normalize_doctype(raw)
        key = (ERPAllowlistType.doctype, normalized)
        if normalized and key not in existing:
            entries.append(
                ERPAllowlistEntry(entry_type=ERPAllowlistType.doctype, value=normalized)
            )
            existing.add(key)
            inserted += 1

    for raw in settings.erp_allowed_methods:
        normalized = normalize_method(raw)
        key = (ERPAllowlistType.method, normalized)
        if normalized and key not in existing:
            entries.append(
                ERPAllowlistEntry(entry_type=ERPAllowlistType.method, value=normalized)
            )
            existing.add(key)
            inserted += 1

    if not entries:
        return 0

    db.add_all(entries)
    db.commit()
    return inserted


def get_allowlist(db: Session) -> Allowlist:
    entries = db.query(ERPAllowlistEntry).all()
    if not entries:
        settings = get_settings()
        doctypes = [normalize_doctype(item) for item in settings.erp_allowed_doctypes if item]
        methods = [normalize_method(item) for item in settings.erp_allowed_methods if item]
        return Allowlist(build_doctype_map(doctypes), set(methods))

    doctypes = [entry.value for entry in entries if entry.entry_type == ERPAllowlistType.doctype]
    methods = [entry.value for entry in entries if entry.entry_type == ERPAllowlistType.method]
    return Allowlist(build_doctype_map(doctypes), {normalize_method(value) for value in methods})


def build_doctype_map(values: list[str]) -> dict[str, str]:
    allowed: dict[str, str] = {}
    for value in values:
        normalized = normalize_doctype(value)
        if normalized:
            allowed[normalized.lower()] = normalized
    return allowed
