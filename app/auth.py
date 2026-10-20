"""Password hashing + signed session tokens. Stdlib only - no new dependency.

Password storage: PBKDF2-HMAC-SHA256 with a random salt, stored as
``pbkdf2_sha256$<iterations>$<salt-hex>$<hash-hex>``. The raw password is
never stored or logged.

Sessions: stateless, signed tokens (not JWT, but the same idea) - no
session table in MongoDB. ``make_token`` packs ``{name, role, exp}`` into
base64 JSON and appends an HMAC-SHA256 signature keyed by ``SESSION_SECRET``
(from .env). ``verify_token`` re-checks the signature and expiry. Anyone
holding a valid token is trusted as that name/role for its lifetime -
there is no per-request database lookup or server-side revocation list.
That's a deliberate simplicity trade-off for a small trusted team; it means
deleting an annotator's account does not immediately invalidate tokens
they already hold (they expire naturally after TOKEN_TTL_SECONDS).
"""
import base64
import hashlib
import hmac
import json
import os
import time

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 days
PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(dk, expected)
    except (ValueError, AttributeError):
        return False


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(name: str, role: str, ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    if not SESSION_SECRET:
        raise RuntimeError("SESSION_SECRET is not set in .env - cannot issue login tokens.")
    payload = {"name": name, "role": role, "exp": int(time.time()) + ttl_seconds}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64e(sig)}"


def verify_token(token: str):
    """Returns {"name", "role", "exp"} if the token is valid and unexpired, else None."""
    if not SESSION_SECRET or not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected_sig, _b64d(sig_b64)):
            return None
        payload = json.loads(_b64d(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, KeyError, TypeError):
        return None
