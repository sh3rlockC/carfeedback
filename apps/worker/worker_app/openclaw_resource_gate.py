from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_MAX_ACTIVE_COLLECTIONS = 2
DEFAULT_BURST_ACTIVE_COLLECTIONS = 3
DEFAULT_MIN_MEM_AVAILABLE_MB = 3000
DEFAULT_BURST_MIN_MEM_AVAILABLE_MB = 4000
DEFAULT_MAX_SWAP_USED_MB = 512


@dataclass(frozen=True)
class ResourceSnapshot:
    mem_available_mb: int | None
    swap_used_mb: int | None


@dataclass(frozen=True)
class OpenClawResourceSettings:
    max_active_collections: int = DEFAULT_MAX_ACTIVE_COLLECTIONS
    burst_active_collections: int = DEFAULT_BURST_ACTIVE_COLLECTIONS
    min_mem_available_mb: int = DEFAULT_MIN_MEM_AVAILABLE_MB
    burst_min_mem_available_mb: int = DEFAULT_BURST_MIN_MEM_AVAILABLE_MB
    max_swap_used_mb: int = DEFAULT_MAX_SWAP_USED_MB

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "OpenClawResourceSettings":
        env = os.environ if environ is None else environ
        return cls(
            max_active_collections=_env_int(env, "OPENCLAW_MAX_ACTIVE_COLLECTIONS", cls.max_active_collections),
            burst_active_collections=_env_int(env, "OPENCLAW_BURST_ACTIVE_COLLECTIONS", cls.burst_active_collections),
            min_mem_available_mb=_env_int(env, "OPENCLAW_MIN_MEM_AVAILABLE_MB", cls.min_mem_available_mb),
            burst_min_mem_available_mb=_env_int(
                env,
                "OPENCLAW_BURST_MIN_MEM_AVAILABLE_MB",
                cls.burst_min_mem_available_mb,
            ),
            max_swap_used_mb=_env_int(env, "OPENCLAW_MAX_SWAP_USED_MB", cls.max_swap_used_mb),
        )


@dataclass(frozen=True)
class OpenClawAdmissionDecision:
    allowed: bool
    reason: str
    active_collections: int
    effective_limit: int
    settings: OpenClawResourceSettings
    snapshot: ResourceSnapshot
    burst: bool = False

    def event_payload(self) -> dict[str, int | str | bool | None]:
        return {
            "reason": self.reason,
            "active_collections": self.active_collections,
            "effective_limit": self.effective_limit,
            "max_active_collections": self.settings.max_active_collections,
            "burst_active_collections": self.settings.burst_active_collections,
            "min_mem_available_mb": self.settings.min_mem_available_mb,
            "burst_min_mem_available_mb": self.settings.burst_min_mem_available_mb,
            "max_swap_used_mb": self.settings.max_swap_used_mb,
            "mem_available_mb": self.snapshot.mem_available_mb,
            "swap_used_mb": self.snapshot.swap_used_mb,
            "burst": self.burst,
        }


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def read_openclaw_resource_snapshot(proc_meminfo_path: str = "/proc/meminfo") -> ResourceSnapshot:
    meminfo = _read_meminfo(proc_meminfo_path)
    mem_available_mb = _kb_to_mb(meminfo.get("MemAvailable"))
    swap_total_mb = _kb_to_mb(meminfo.get("SwapTotal"))
    swap_free_mb = _kb_to_mb(meminfo.get("SwapFree"))
    swap_used_mb = None
    if swap_total_mb is not None and swap_free_mb is not None:
        swap_used_mb = max(swap_total_mb - swap_free_mb, 0)
    return ResourceSnapshot(mem_available_mb=mem_available_mb, swap_used_mb=swap_used_mb)


def _read_meminfo(path: str) -> dict[str, int]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    result: dict[str, int] = {}
    for line in lines:
        key, separator, rest = line.partition(":")
        if not separator:
            continue
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            result[key] = int(parts[0])
        except ValueError:
            continue
    return result


def _kb_to_mb(value: int | None) -> int | None:
    if value is None:
        return None
    return max(value // 1024, 0)


def decide_openclaw_admission(
    *,
    active_collections: int,
    snapshot: ResourceSnapshot,
    settings: OpenClawResourceSettings,
) -> OpenClawAdmissionDecision:
    max_active = max(settings.max_active_collections, 0)
    burst_active = max(settings.burst_active_collections, max_active)
    normalized_settings = OpenClawResourceSettings(
        max_active_collections=max_active,
        burst_active_collections=burst_active,
        min_mem_available_mb=max(settings.min_mem_available_mb, 0),
        burst_min_mem_available_mb=max(settings.burst_min_mem_available_mb, 0),
        max_swap_used_mb=max(settings.max_swap_used_mb, 0),
    )

    swap_high = (
        snapshot.swap_used_mb is not None
        and snapshot.swap_used_mb > normalized_settings.max_swap_used_mb
    )
    if active_collections < max_active:
        if swap_high:
            return _blocked("swap_high", active_collections, max_active, normalized_settings, snapshot)
        if snapshot.mem_available_mb is not None and snapshot.mem_available_mb < normalized_settings.min_mem_available_mb:
            return _blocked("memory_low", active_collections, max_active, normalized_settings, snapshot)
        return OpenClawAdmissionDecision(
            allowed=True,
            reason="within_regular_capacity",
            active_collections=active_collections,
            effective_limit=max_active,
            settings=normalized_settings,
            snapshot=snapshot,
        )

    if active_collections >= burst_active:
        return _blocked("active_limit", active_collections, burst_active, normalized_settings, snapshot)
    if swap_high:
        return _blocked("swap_high", active_collections, burst_active, normalized_settings, snapshot)
    if snapshot.mem_available_mb is None:
        return _blocked("meminfo_unavailable", active_collections, max_active, normalized_settings, snapshot)
    if snapshot.mem_available_mb < normalized_settings.burst_min_mem_available_mb:
        return _blocked("burst_memory_low", active_collections, burst_active, normalized_settings, snapshot)
    return OpenClawAdmissionDecision(
        allowed=True,
        reason="within_burst_capacity",
        active_collections=active_collections,
        effective_limit=burst_active,
        settings=normalized_settings,
        snapshot=snapshot,
        burst=True,
    )


def _blocked(
    reason: str,
    active_collections: int,
    effective_limit: int,
    settings: OpenClawResourceSettings,
    snapshot: ResourceSnapshot,
) -> OpenClawAdmissionDecision:
    return OpenClawAdmissionDecision(
        allowed=False,
        reason=reason,
        active_collections=active_collections,
        effective_limit=effective_limit,
        settings=settings,
        snapshot=snapshot,
    )
