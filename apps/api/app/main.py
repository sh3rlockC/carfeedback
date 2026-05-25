from __future__ import annotations

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db import init_db
from app.routes.access import router as access_router
from app.routes.admin import router as admin_router
from app.routes.comparisons import router as comparisons_router
from app.routes.jobs import router as jobs_router
from app.routes.tasks import router as tasks_router
from app.routes.vehicles import router as vehicles_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    init_db(settings)

    app = FastAPI(title="Vehicle Koubei API", version="0.1.0")
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(access_router)
    app.include_router(admin_router)
    app.include_router(vehicles_router)
    app.include_router(jobs_router)
    app.include_router(comparisons_router)
    app.include_router(tasks_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
