import bcrypt
import hashlib
import re

_UUID_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def normalize_license_key(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return ""
    compact = re.sub(r"[-\s]", "", trimmed)
    if _UUID_HEX_RE.match(compact):
        return compact.lower()
    return trimmed


def fingerprint_license_key(value: str) -> str:
    normalized = normalize_license_key(value)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hash_license_key(license_key: str) -> str:
    hashed = bcrypt.hashpw(license_key.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_license_key(license_key: str, hashed_key: str) -> bool:
    try:
        return bcrypt.checkpw(license_key.encode("utf-8"), hashed_key.encode("utf-8"))
    except ValueError:
        return False


def verify_license_key_flexible(license_key: str, hashed_key: str) -> bool:
    trimmed = license_key.strip()
    if not trimmed:
        return False
    if verify_license_key(trimmed, hashed_key):
        return True
    normalized = normalize_license_key(trimmed)
    if normalized and normalized != trimmed:
        return verify_license_key(normalized, hashed_key)
    return False
