from typing import Literal

from pydantic import BaseModel, Field


CollectorPlatform = Literal["autohome", "dongchedi"]
CollectorMode = Literal["full_refresh", "incremental", "retry", "backfill"]
CollectorRunState = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
]


class CollectorRunRequest(BaseModel):
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, min_length=1)
    platform: CollectorPlatform
    query_key: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    series_id: str = Field(min_length=1)
    mode: CollectorMode
    known_links: list[str] = Field(default_factory=list)
    resume_cursor: dict = Field(default_factory=dict)
    max_scan_pages: int = Field(default=10, ge=1)
    stop_after_known_pages: int = Field(default=2, ge=0)


class CollectorEvent(BaseModel):
    event_type: str
    message: str
    progress_current: int = 0
    progress_total: int = 0
    payload: dict = Field(default_factory=dict)


class CollectorRunStatus(BaseModel):
    run_id: str
    platform: CollectorPlatform
    status: CollectorRunState
    progress_current: int = 0
    progress_total: int = 0
    events: list[CollectorEvent] = Field(default_factory=list)
    resume_cursor: dict = Field(default_factory=dict)
    failure_category: str | None = None
    output_path: str | None = None
