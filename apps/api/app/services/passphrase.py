from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import HTTPException, Request, status

from app.config import Settings


def hash_passphrase(passphrase: str) -> str:
    digest = hashlib.sha256(passphrase.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_passphrase(passphrase: str, stored_hash: str) -> bool:
    if not stored_hash or ":" not in stored_hash:
        return False
    algorithm, expected = stored_hash.split(":", 1)
    if algorithm != "sha256":
        return False
    actual = hashlib.sha256(passphrase.encode("utf-8")).hexdigest()
    return hmac.compare_digest(actual, expected)


def create_session_token(settings: Settings) -> str:
    payload = {
        "version": settings.pass_phrase_version,
        "exp": int(time.time()) + settings.session_ttl_seconds,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    signature = hmac.new(settings.session_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def decode_session_token(token: str, settings: Settings) -> dict[str, Any]:
    if "." not in token:
        raise ValueError("malformed token")
    encoded, signature = token.split(".", 1)
    expected_signature = hmac.new(settings.session_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("signature mismatch")
    padded = encoded + "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
    if payload.get("version") != settings.pass_phrase_version:
        raise ValueError("version mismatch")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("expired")
    return payload


def require_passphrase_session(request: Request, settings: Settings) -> None:
    if not settings.access_control_enabled:
        return

    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="passphrase session required")
    try:
        decode_session_token(token, settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
