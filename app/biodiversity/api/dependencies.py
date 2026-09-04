"""Configuration and dependency access for the Phase 4 API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request

from app.biodiversity.graph import DEFAULT_BIODIVERSITY_CHECKPOINT_PATH

if TYPE_CHECKING:
    from app.biodiversity.api.services.operations import Phase4Application


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALL_RUN_MODES = (
    ("fixture", "scripted"),
    ("live", "scripted"),
    ("fixture", "live"),
    ("live", "live"),
)


def _origins(value: str | None) -> tuple[str, ...]:
    raw = value or "http://127.0.0.1:5173,http://localhost:5173"
    origins = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    if not origins or "*" in origins:
        raise ValueError("BIODIVERSITY_CORS_ORIGINS must contain explicit origins")
    return origins


def _flag(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalised = value.strip().casefold()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Boolean environment values must be true or false")


def _run_modes(
    value: str | None,
    *,
    public_demo: bool,
) -> tuple[tuple[str, str], ...]:
    raw = value or (
        "fixture/scripted"
        if public_demo
        else "fixture/scripted,live/scripted,fixture/live,live/live"
    )
    parsed: list[tuple[str, str]] = []
    for item in raw.split(","):
        parts = tuple(part.strip().casefold() for part in item.split("/"))
        if len(parts) != 2 or parts not in ALL_RUN_MODES:
            raise ValueError("BIODIVERSITY_ALLOWED_RUN_MODES contains an invalid mode")
        if parts not in parsed:
            parsed.append(parts)
    if not parsed:
        raise ValueError("BIODIVERSITY_ALLOWED_RUN_MODES cannot be empty")
    if public_demo and parsed != [("fixture", "scripted")]:
        raise ValueError("Public demo mode permits only fixture/scripted runs")
    return tuple(parsed)


def _number(name: str, default: str, *, minimum: float = 0) -> float:
    value = float(os.getenv(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class APISettings:
    checkpoint_db: Path = DEFAULT_BIODIVERSITY_CHECKPOINT_PATH
    catalog_db: Path = PROJECT_ROOT / "data/runtime/phase4-run-catalog.sqlite"
    report_root: Path = PROJECT_ROOT / "reports/runs/phase4"
    osm_directory: Path = PROJECT_ROOT / "data/osm"
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    default_data_mode: str = "fixture"
    default_model_mode: str = "scripted"
    allowed_run_modes: tuple[tuple[str, str], ...] = ALL_RUN_MODES
    public_demo: bool = False
    serve_frontend: bool = False
    frontend_dist: Path = PROJECT_ROOT / "frontend/dist"
    expose_api_docs: bool = True
    sse_heartbeat_seconds: float = 15.0
    operation_start_delay_seconds: float = 0.0
    max_concurrent_operations: int = 8
    mutations_per_minute: int = 0
    max_demo_threads: int = 0
    max_storage_bytes: int = 0
    run_retention_seconds: float = 0.0
    cleanup_interval_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.allowed_run_modes:
            raise ValueError("At least one run mode must be allowed")
        if any(mode not in ALL_RUN_MODES for mode in self.allowed_run_modes):
            raise ValueError("An unsupported run mode was configured")
        if (
            self.default_data_mode,
            self.default_model_mode,
        ) not in self.allowed_run_modes:
            raise ValueError("The default run mode must be included in the allowlist")
        if self.max_concurrent_operations < 1:
            raise ValueError("max_concurrent_operations must be positive")
        if self.public_demo:
            if self.allowed_run_modes != (("fixture", "scripted"),):
                raise ValueError("Public demo mode permits only fixture/scripted runs")
            if self.expose_api_docs:
                raise ValueError("Public demo mode cannot expose interactive API docs")
            if not all(
                (
                    self.mutations_per_minute,
                    self.max_demo_threads,
                    self.max_storage_bytes,
                    self.run_retention_seconds,
                )
            ):
                raise ValueError("Public demo resource limits must be enabled")

    @classmethod
    def from_environment(cls) -> "APISettings":
        public_demo = _flag(os.getenv("BIODIVERSITY_PUBLIC_DEMO"))
        allowed_run_modes = _run_modes(
            os.getenv("BIODIVERSITY_ALLOWED_RUN_MODES"),
            public_demo=public_demo,
        )
        default_data_mode = os.getenv("BIODIVERSITY_DATA_MODE", "fixture")
        default_model_mode = os.getenv("BIODIVERSITY_MODEL_MODE", "scripted")
        if (default_data_mode, default_model_mode) not in allowed_run_modes:
            raise ValueError("The default run mode must be included in the allowlist")
        return cls(
            checkpoint_db=Path(
                os.getenv(
                    "BIODIVERSITY_CHECKPOINT_DB",
                    str(DEFAULT_BIODIVERSITY_CHECKPOINT_PATH),
                )
            ),
            catalog_db=Path(
                os.getenv(
                    "BIODIVERSITY_RUN_CATALOG_DB",
                    str(PROJECT_ROOT / "data/runtime/phase4-run-catalog.sqlite"),
                )
            ),
            report_root=Path(
                os.getenv(
                    "BIODIVERSITY_REPORT_ROOT",
                    str(PROJECT_ROOT / "reports/runs/phase4"),
                )
            ),
            osm_directory=Path(
                os.getenv(
                    "BIODIVERSITY_OSM_DIRECTORY",
                    str(PROJECT_ROOT / "data/osm"),
                )
            ),
            cors_origins=_origins(os.getenv("BIODIVERSITY_CORS_ORIGINS")),
            default_data_mode=default_data_mode,
            default_model_mode=default_model_mode,
            allowed_run_modes=allowed_run_modes,
            public_demo=public_demo,
            serve_frontend=_flag(
                os.getenv("BIODIVERSITY_SERVE_FRONTEND"),
                default=public_demo,
            ),
            frontend_dist=Path(
                os.getenv(
                    "BIODIVERSITY_FRONTEND_DIST",
                    str(PROJECT_ROOT / "frontend/dist"),
                )
            ),
            expose_api_docs=_flag(
                None if public_demo else os.getenv("BIODIVERSITY_EXPOSE_API_DOCS"),
                default=not public_demo,
            ),
            sse_heartbeat_seconds=_number(
                "BIODIVERSITY_SSE_HEARTBEAT_SECONDS", "15", minimum=0.01
            ),
            max_concurrent_operations=int(
                _number(
                    "BIODIVERSITY_MAX_CONCURRENT_OPERATIONS",
                    "2" if public_demo else "8",
                    minimum=1,
                )
            ),
            mutations_per_minute=int(
                _number(
                    "BIODIVERSITY_MUTATIONS_PER_MINUTE",
                    "20" if public_demo else "0",
                )
            ),
            max_demo_threads=int(
                _number(
                    "BIODIVERSITY_MAX_DEMO_THREADS",
                    "100" if public_demo else "0",
                )
            ),
            max_storage_bytes=int(
                _number(
                    "BIODIVERSITY_MAX_STORAGE_MB",
                    "192" if public_demo else "0",
                )
                * 1024
                * 1024
            ),
            run_retention_seconds=(
                _number(
                    "BIODIVERSITY_RUN_RETENTION_HOURS",
                    "24" if public_demo else "0",
                )
                * 3600
            ),
            cleanup_interval_seconds=_number(
                "BIODIVERSITY_CLEANUP_INTERVAL_SECONDS", "300", minimum=1
            ),
        )


def application(request: Request) -> "Phase4Application":
    return request.app.state.phase4
