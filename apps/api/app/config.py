from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def discover_workspace_root(
    source_path: Path | None = None,
    *,
    mounted_workspace: Path | None = None,
    fallback_cwd: Path | None = None,
) -> Path:
    workspace_mount = (mounted_workspace or Path("/workspace")).expanduser()
    if workspace_mount.exists():
        return workspace_mount.resolve()

    file_path = (source_path or Path(__file__)).resolve()
    for parent in file_path.parents:
        if (parent / "config" / "dependencies.yaml").exists():
            return parent.parent.resolve()

    return (fallback_cwd or Path.cwd()).expanduser().resolve()


class Settings(BaseSettings):
    app_env: str = "development"
    base_url: str = "http://localhost"
    database_url: str = "sqlite+pysqlite:///./vehicle_koubei.db"
    redis_url: str = "redis://redis:6379/0"
    worker_queue_name: str = "vehicle-koubei"
    worker_job_timeout_seconds: int = 7200
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "vehicle-koubei-temporal"
    task_center_create_enabled: bool = False
    artifact_root: str = "/srv/koubei/jobs"
    corpus_root: str = "/srv/koubei/corpus"
    workspace_root: str = str(discover_workspace_root())

    pass_phrase_hash: str = ""
    pass_phrase_version: str = "2026-W17"
    access_control_enabled: bool = False
    session_secret: str = "change-me"
    session_cookie_name: str = "koubei_access"
    session_ttl_seconds: int = 4 * 60 * 60

    llm_provider: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model_report: str = ""
    llm_model_qa: str = ""
    vehicle_resolve_cache_ttl_seconds: int = 7 * 24 * 60 * 60
    vehicle_resolve_platform_timeout_seconds: int = 60
    job_artifact_retention_days: int = 3
    job_artifact_cleanup_interval_seconds: int = 12 * 60 * 60
    comparison_model_concurrency: int = 1

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def artifact_root_path(self) -> Path:
        return Path(self.artifact_root).expanduser().resolve()

    @property
    def corpus_root_path(self) -> Path:
        return Path(self.corpus_root).expanduser().resolve()

    @property
    def workspace_root_path(self) -> Path:
        return Path(self.workspace_root).expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
