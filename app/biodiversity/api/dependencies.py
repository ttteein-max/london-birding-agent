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


def _origins(value: str | None) -> tuple[str, ...]:
    raw = value or "http://127.0.0.1:5173,http://localhost:5173"
    origins = tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())
    if not origins or "*" in origins:
        raise ValueError("BIODIVERSITY_CORS_ORIGINS must contain explicit origins")
    return origins


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
    sse_heartbeat_seconds: float = 15.0
    operation_start_delay_seconds: float = 0.0

    @classmethod
    def from_environment(cls) -> "APISettings":
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
            default_data_mode=os.getenv("BIODIVERSITY_DATA_MODE", "fixture"),
            default_model_mode=os.getenv("BIODIVERSITY_MODEL_MODE", "scripted"),
            sse_heartbeat_seconds=float(
                os.getenv("BIODIVERSITY_SSE_HEARTBEAT_SECONDS", "15")
            ),
        )


def application(request: Request) -> "Phase4Application":
    return request.app.state.phase4
