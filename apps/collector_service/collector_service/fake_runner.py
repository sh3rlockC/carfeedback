import os

from collector_service.models import CollectorEvent, CollectorRunRequest, CollectorRunStatus


TRUTHY_VALUES = {"1", "true", "yes"}


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in TRUTHY_VALUES


def _failure_category_from_env() -> str | None:
    explicit_failure = os.getenv("FAKE_COLLECTOR_FAIL_CATEGORY")
    if explicit_failure:
        return explicit_failure
    if _is_truthy(os.getenv("FAKE_COLLECTOR_EMPTY_DATA")):
        return "empty_data"
    return None


def create_initial_status(request: CollectorRunRequest) -> CollectorRunStatus:
    return CollectorRunStatus(
        run_id=request.run_id,
        platform=request.platform,
        status="queued",
        progress_total=request.max_scan_pages,
        resume_cursor=dict(request.resume_cursor),
        events=[
            CollectorEvent(
                event_type="run_queued",
                message="Collector run queued.",
                progress_current=0,
                progress_total=request.max_scan_pages,
            )
        ],
    )


def run_fake_collector(
    request: CollectorRunRequest, status: CollectorRunStatus
) -> CollectorRunStatus:
    page_seconds = os.getenv("FAKE_COLLECTOR_PAGE_SECONDS", "0")
    status.status = "running"
    status.events.append(
        CollectorEvent(
            event_type="run_started",
            message="Collector run started.",
            progress_current=0,
            progress_total=request.max_scan_pages,
            payload={
                "mode": request.mode,
                "series_id": request.series_id,
                "page_seconds": page_seconds,
            },
        )
    )

    for page in range(1, request.max_scan_pages + 1):
        status.progress_current = page
        status.resume_cursor = {"page": page}
        status.events.append(
            CollectorEvent(
                event_type="page_scanned",
                message=f"Scanned page {page}.",
                progress_current=page,
                progress_total=request.max_scan_pages,
                payload={
                    "page": page,
                    "page_seconds": page_seconds,
                    "known_links_seen": len(request.known_links),
                    "stop_after_known_pages": request.stop_after_known_pages,
                },
            )
        )

    failure_category = _failure_category_from_env()
    if failure_category:
        status.status = "failed"
        status.failure_category = failure_category
        status.events.append(
            CollectorEvent(
                event_type="run_failed",
                message="Collector run failed.",
                progress_current=status.progress_current,
                progress_total=status.progress_total,
                payload={"failure_category": failure_category},
            )
        )
        return status

    status.status = "succeeded"
    status.output_path = f"/fake_collector_outputs/{request.run_id}.json"
    status.events.append(
        CollectorEvent(
            event_type="run_succeeded",
            message="Collector run succeeded.",
            progress_current=status.progress_current,
            progress_total=status.progress_total,
            payload={"output_path": status.output_path},
        )
    )
    return status

