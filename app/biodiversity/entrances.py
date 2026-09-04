"""Versioned OSM public-green-space entrance snapshot access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from app.biodiversity.models import (
    AccessCertainty,
    PublicSiteCandidate,
    SourceFailure,
    ToolErrorCode,
    WGS84Point,
)
from app.biodiversity.routing_models import PublicEntranceCandidate
from app.feasibility.core import PROJECT_ROOT, file_sha256

ENTRANCE_DIRECTORY = PROJECT_ROOT / "data" / "osm"
EXPLICIT_PUBLIC = {"yes", "permissive", "designated", "public"}
BLOCKED = {"no", "private"}
SERVICE_ONLY = {
    "emergency",
    "emergency_access",
    "service",
    "exit",
    "exit_only",
    "delivery",
}


class EntranceRepository(Protocol):
    def for_site(self, site: PublicSiteCandidate) -> list[PublicEntranceCandidate]: ...


def entrance_is_eligible(tags: dict[str, str]) -> bool:
    """Apply conservative walking/access rules without inferring public access."""

    normalised = {key: str(value).strip().casefold() for key, value in tags.items()}
    if normalised.get("access") in BLOCKED or normalised.get("foot") in BLOCKED:
        return False
    if normalised.get("entrance") in SERVICE_ONLY:
        return False
    if normalised.get("routing:entrance") in SERVICE_ONLY:
        return False
    if normalised.get("service") in SERVICE_ONLY:
        return False
    if normalised.get("emergency") == "yes":
        return False
    return bool(
        normalised.get("entrance")
        or normalised.get("routing:entrance")
        or normalised.get("barrier") == "gate"
    )


def entrance_access_certainty(tags: dict[str, str]) -> AccessCertainty:
    """Only an explicit access tag establishes explicit-public certainty."""

    access = str(tags.get("access") or "").strip().casefold()
    return (
        AccessCertainty.explicit_public
        if access in EXPLICIT_PUBLIC
        else AccessCertainty.unspecified
    )


def entrance_priority(candidate: PublicEntranceCandidate) -> tuple[int, int, int, str]:
    """Prefer routing/main, then explicit-public, then stable OSM identity."""

    routing = (candidate.routing_entrance or "").casefold()
    entrance = (candidate.entrance or "").casefold()
    return (
        0 if routing in {"main", "main_entrance"} else 1,
        0 if entrance in {"main", "main_entrance"} else 1,
        0 if candidate.access_certainty == AccessCertainty.explicit_public else 1,
        candidate.entrance_id,
    )


class SnapshotEntranceRepository:
    """Read one checked, versioned snapshot; runtime never calls Overpass."""

    def __init__(self, directory: Path = ENTRANCE_DIRECTORY) -> None:
        self.directory = directory
        self._features: list[dict[str, Any]] | None = None

    def _load(self) -> list[dict[str, Any]]:
        if self._features is not None:
            return self._features
        artifacts = sorted(
            self.directory.glob("london-public-green-space-entrances-*.geojson")
        )
        provenance_paths = sorted(
            self.directory.glob(
                "london-public-green-space-entrances-*.provenance.json"
            )
        )
        if len(artifacts) != 1 or len(provenance_paths) != 1:
            raise SourceFailure(
                ToolErrorCode.missing_or_corrupt_fixture,
                "Expected exactly one versioned London entrance snapshot.",
                source=str(self.directory),
            )
        try:
            provenance = json.loads(provenance_paths[0].read_text(encoding="utf-8"))
            artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceFailure(
                ToolErrorCode.missing_or_corrupt_fixture,
                "The London entrance snapshot is missing or malformed.",
                source=str(artifacts[0]),
            ) from exc
        if file_sha256(artifacts[0]) != provenance.get("checksum_sha256"):
            raise SourceFailure(
                ToolErrorCode.missing_or_corrupt_fixture,
                "London entrance snapshot checksum does not match provenance.",
                source=str(artifacts[0]),
            )
        features = artifact.get("features")
        if not isinstance(features, list) or len(features) != provenance.get(
            "feature_count"
        ):
            raise SourceFailure(
                ToolErrorCode.missing_or_corrupt_fixture,
                "London entrance snapshot count is inconsistent.",
                source=str(artifacts[0]),
            )
        self._features = features
        return features

    def for_site(self, site: PublicSiteCandidate) -> list[PublicEntranceCandidate]:
        output: list[PublicEntranceCandidate] = []
        for feature in self._load():
            properties = feature.get("properties") or {}
            associations = properties.get("site_associations") or []
            association = next(
                (
                    item
                    for item in associations
                    if item.get("site_id") == site.site_id
                    and item.get("site_osm_type")
                    == site.site_id.removeprefix("osm-").rsplit("-", 1)[0]
                    and str(item.get("site_osm_id"))
                    == site.site_id.rsplit("-", 1)[-1]
                    and item.get("association_method") == "osm_boundary_member"
                ),
                None,
            )
            if association is None:
                continue
            tags = {
                str(key): str(value)
                for key, value in (properties.get("source_tags") or {}).items()
            }
            if not entrance_is_eligible(tags):
                continue
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates") or []
            if geometry.get("type") != "Point" or len(coordinates) != 2:
                continue
            entrance_id = str(feature.get("id") or "")
            output.append(
                PublicEntranceCandidate(
                    entrance_id=entrance_id,
                    site_id=site.site_id,
                    site_osm_type=str(association["site_osm_type"]),
                    site_osm_id=int(association["site_osm_id"]),
                    label=str(
                        tags.get("name")
                        or tags.get("routing:entrance")
                        or tags.get("entrance")
                        or f"Public-site entrance {entrance_id}"
                    ),
                    point=WGS84Point(
                        longitude=float(coordinates[0]),
                        latitude=float(coordinates[1]),
                    ),
                    access_certainty=entrance_access_certainty(tags),
                    routing_entrance=tags.get("routing:entrance"),
                    entrance=tags.get("entrance"),
                    barrier=tags.get("barrier"),
                    access=tags.get("access"),
                    foot=tags.get("foot"),
                    wheelchair=tags.get("wheelchair"),
                    opening_hours=tags.get("opening_hours"),
                    association_method="osm_boundary_member",
                    source_tags=tags,
                    limitations=[
                        "OSM entrance tags identify a physical access point, not a legal access guarantee.",
                        *(
                            [
                                "The access tag is missing or non-explicit; public walking access remains uncertain."
                            ]
                            if entrance_access_certainty(tags)
                            == AccessCertainty.unspecified
                            else []
                        ),
                    ],
                )
            )
        return sorted(output, key=entrance_priority)
