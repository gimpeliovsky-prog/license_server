import bcrypt


def hash_license_key(license_key: str) -> str:
    hashed = bcrypt.hashpw(license_key.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_license_key(license_key: str, hashed_key: str) -> bool:
    try:
        return bcrypt.checkpw(license_key.encode("utf-8"), hashed_key.encode("utf-8"))
    except ValueError:
        return False
