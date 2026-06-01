from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.task_tokens import TaskTokenPair, generate_task_tokens, hash_task_token, verify_task_token


def test_generate_task_tokens_returns_view_and_manage_tokens() -> None:
    pair = generate_task_tokens()

    assert isinstance(pair, TaskTokenPair)
    assert pair.view_token != pair.manage_token
    assert len(pair.view_token) >= 32
    assert len(pair.manage_token) >= 32
    assert pair.view_token_hash != pair.manage_token_hash


def test_verify_task_token_rejects_wrong_expired_and_revoked_tokens() -> None:
    pair = generate_task_tokens()
    expires_at = datetime.now(UTC) + timedelta(days=7)

    assert verify_task_token(pair.manage_token, pair.manage_token_hash, expires_at=expires_at, revoked_at=None) is True
    assert verify_task_token("wrong-token", pair.manage_token_hash, expires_at=expires_at, revoked_at=None) is False
    assert (
        verify_task_token(
            pair.manage_token,
            pair.manage_token_hash,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
            revoked_at=None,
        )
        is False
    )
    assert (
        verify_task_token(pair.manage_token, pair.manage_token_hash, expires_at=expires_at, revoked_at=datetime.now(UTC))
        is False
    )


def test_hash_task_token_is_stable_without_storing_plain_token() -> None:
    token = "example-token"
    assert hash_task_token(token) == hash_task_token(token)
    assert token not in hash_task_token(token)
