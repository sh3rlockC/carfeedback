from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import secrets


@dataclass(frozen=True)
class TaskTokenPair:
    view_token: str
    manage_token: str
    view_token_hash: str
    manage_token_hash: str


def hash_task_token(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def generate_task_tokens() -> TaskTokenPair:
    view_token = secrets.token_urlsafe(32)
    manage_token = secrets.token_urlsafe(32)
    return TaskTokenPair(
        view_token=view_token,
        manage_token=manage_token,
        view_token_hash=hash_task_token(view_token),
        manage_token_hash=hash_task_token(manage_token),
    )


def verify_task_token(
    token: str,
    expected_hash: str,
    *,
    expires_at: datetime | None,
    revoked_at: datetime | None,
) -> bool:
    if revoked_at is not None:
        return False
    if expires_at is not None:
        now = datetime.now(UTC)
        expires = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if expires <= now:
            return False
    return hmac.compare_digest(hash_task_token(token), expected_hash)
