from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.schemas import SeriesValidationRequest, SeriesValidationResponse, VehicleResolveRequest, VehicleResolveResponse
from app.services.passphrase import require_passphrase_session
from app.services.series_validator import validate_series_id
from app.services.vehicle_resolver import VehicleResolver

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])


@router.post("/resolve", response_model=VehicleResolveResponse)
def resolve_vehicle(
    payload: VehicleResolveRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VehicleResolveResponse:
    require_passphrase_session(request, settings)
    resolver = VehicleResolver(db=db, settings=settings)
    try:
        result = resolver.resolve(payload.query)
    except (RuntimeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return VehicleResolveResponse.model_validate(result)


@router.post("/validate-series", response_model=SeriesValidationResponse)
def validate_series(
    payload: SeriesValidationRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> SeriesValidationResponse:
    require_passphrase_session(request, settings)
    result = validate_series_id(
        query=payload.query,
        platform=payload.platform,
        series_id=payload.series_id,
        url=payload.url,
    )
    return SeriesValidationResponse.model_validate(result)
