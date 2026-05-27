from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.config import Settings, get_settings
from app.schemas import AccessVerifyRequest, AccessVerifyResponse
from app.services.passphrase import create_session_token, verify_passphrase

router = APIRouter(prefix="/api/access", tags=["access"])


@router.post("/verify", response_model=AccessVerifyResponse)
def verify_access(
    payload: AccessVerifyRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> AccessVerifyResponse:
    if not settings.access_control_enabled:
        return AccessVerifyResponse(ok=True, passphrase_version=settings.pass_phrase_version)

    if not verify_passphrase(payload.passphrase, settings.pass_phrase_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid passphrase")

    token = create_session_token(settings)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return AccessVerifyResponse(ok=True, passphrase_version=settings.pass_phrase_version)
