"""비밀번호 해시(scrypt)와 서명된 세션 토큰(HMAC). 서버리스 환경을 위해 서버 저장소 없이 검증한다."""
import base64
import hashlib
import hmac
import json
import os
import re
import time

from .config import get_secret_key

TOKEN_TTL_SECONDS = 7 * 24 * 3600
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(get_secret_key().encode(), payload.encode(), hashlib.sha256).digest())


def make_token(user_id: int, now: float | None = None) -> str:
    payload = _b64(json.dumps({"uid": user_id, "exp": int((now or time.time()) + TOKEN_TTL_SECONDS)}).encode())
    return f"{payload}.{_sign(payload)}"


def read_token(token: str | None, now: float | None = None) -> int | None:
    """유효하면 user_id, 아니면 None."""
    if not token or "." not in token:
        return None
    payload, sig = token.split(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < (now or time.time()):
        return None
    return int(data["uid"])
