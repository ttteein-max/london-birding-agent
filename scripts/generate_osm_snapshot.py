"""Explicitly generate the one-off London OSM green-space candidate snapshot."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import time

from app.feasibility.core import PROJECT_ROOT, file_sha256
from app.feasibility.live import USER_AGENT

OSM_DIR = PROJECT_ROOT / "data" / "osm"
QUERY_PATH = OSM_DIR / "london-green-spaces.overpassql"
ENDPOINT = "https://overpass-api.de/api/interpreter"
PRIVATE_ACCESS = {"private", "no"}
EXPLICIT_PUBLIC_ACCESS = {"yes", "permissive", "designated", "public"}
RELEVANT_TAGS = (
    "name",
    "name:en",
    "leisure",
    "landuse",
    "boundary",
    "protect_class",
    "access",
    "operator",
    "opening_hours",
    "website",
    "wikidata",
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _request_overpass(query: str) -> dict[str, Any]:
    body = urlencode({"data": query}).encode("utf-8")
    request = Request(
        ENDPOINT,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=150) as response:  # noqa: S310 - fixed endpoint
                if response.status != 200:
                    raise RuntimeError(f"Overpass returned HTTP {response.status}")
                return json.load(response)
        except HTTPError as exc:
            last_error = exc
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise RuntimeError(f"Overpass returned HTTP {exc.code}") from exc
        except (TimeoutError, URLError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(2 ** (attempt - 1) * 3)
    raise RuntimeError(
        "Overpass snapshot failed after three bounded attempts. Keep the query and "
        "generator; an OSM-derived GeoJSON or London OSM extract with provenance is "
        f"the acceptable manual fallback. Last error: {last_error}"
    )


def _position(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return element["lon"], element["lat"]
    centre = element.get("center") or {}
    if "lat" in centre and "lon" in centre:
        return centre["lon"], centre["lat"]
    return None


def process_elements(elements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    features: list[dict[str, Any]] = []
    counts = {"raw": len(elements), "private_or_no_access": 0, "missing_centre": 0, "retained": 0}
    seen: set[tuple[str, int]] = set()
    for element in elements:
        tags = element.get("tags") or {}
        access = (tags.get("access") or "").lower()
        if access in PRIVATE_ACCESS:
            counts["private_or_no_access"] += 1
            continue
        position = _position(element)
        if position is None:
            counts["missing_centre"] += 1
            continue
        identity = (str(element.get("type")), int(element.get("id")))
        if identity in seen:
            continue
        seen.add(identity)
        access_certainty = "explicit_public" if access in EXPLICIT_PUBLIC_ACCESS else "unspecified"
        properties = {
            "osm_type": identity[0],
            "osm_id": identity[1],
            "access_certainty": access_certainty,
            "source_tags": {key: tags[key] for key in RELEVANT_TAGS if key in tags},
        }
        features.append(
            {
                "type": "Feature",
                "id": f"osm-{identity[0]}-{identity[1]}",
                "geometry": {"type": "Point", "coordinates": [round(position[0], 6), round(position[1], 6)]},
                "properties": properties,
            }
        )
    features.sort(key=lambda feature: feature["id"])
    counts["retained"] = len(features)
    return features, counts


def generate_snapshot() -> tuple[Path, Path]:
    query = QUERY_PATH.read_text(encoding="utf-8")
    raw = _request_overpass(query)
    features, counts = process_elements(raw.get("elements", []))
    if not features:
        raise RuntimeError("Overpass returned no processable green-space candidates")
    retrieved_at = _utc_now()
    version = retrieved_at[:10]
    snapshot_path = OSM_DIR / f"london-green-space-candidates-{version}.geojson"
    provenance_path = OSM_DIR / f"london-green-space-candidates-{version}.provenance.json"
    collection = {
        "type": "FeatureCollection",
        "name": "London OSM public green-space candidates",
        "snapshot_version": version,
        "notice": (
            "Candidate dataset only. A missing access tag is not proof of public access; "
            "check current opening and access information before a visit."
        ),
        "attribution": "© OpenStreetMap contributors",
        "features": features,
    }
    snapshot_path.write_text(json.dumps(collection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    provenance = {
        "source_name": "OpenStreetMap via Overpass API",
        "source_url": "https://www.openstreetmap.org/",
        "endpoint": ENDPOINT,
        "request_parameters": {"query_file": str(QUERY_PATH.relative_to(PROJECT_ROOT))},
        "retrieved_at_utc": retrieved_at,
        "snapshot_version": version,
        "licence": "Open Database Licence (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "checksum_sha256": file_sha256(snapshot_path),
        "record_counts": {"before": counts["raw"], "after": counts["retained"]},
        "filtering_rules": [
            "Query named park, garden, nature reserve, recreation ground, forest, meadow and protected-area features in Greater London",
            "Exclude access=private and access=no",
            "Retain one centre point per OSM element and compact relevant source tags",
            "Mark access as explicit_public only for access=yes, permissive, designated or public; otherwise mark unspecified",
        ],
        "known_limitations": [
            "This is a candidate dataset, not a guarantee of legal access, opening, safety or accessibility",
            "Centroid points simplify polygon and multipolygon geometry",
            "OSM is community-maintained and can be incomplete or outdated",
            "Missing access tags are explicitly classified as unspecified",
        ],
        "feature_count": counts["retained"],
        "excluded_private_or_no_access": counts["private_or_no_access"],
        "excluded_missing_centre": counts["missing_centre"],
        "relevant_source_tags": list(RELEVANT_TAGS),
        "query_sha256": __import__("hashlib").sha256(query.encode("utf-8")).hexdigest(),
        "regeneration_command": "python -m scripts.generate_osm_snapshot --live-refresh",
    }
    provenance_path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return snapshot_path, provenance_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-refresh",
        action="store_true",
        help="Required opt-in: perform one sequential Overpass snapshot request",
    )
    args = parser.parse_args()
    if not args.live_refresh:
        parser.error("snapshot generation requires explicit --live-refresh")
    snapshot, provenance = generate_snapshot()
    print(f"WROTE {snapshot}")
    print(f"WROTE {provenance}")


if __name__ == "__main__":
    main()
