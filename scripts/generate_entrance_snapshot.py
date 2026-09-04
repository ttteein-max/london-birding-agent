"""Explicitly refresh the versioned London public-green-space entrance snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.biodiversity.entrances import entrance_access_certainty, entrance_is_eligible
from app.feasibility.core import PROJECT_ROOT, file_sha256
from app.feasibility.live import USER_AGENT

OSM_DIR = PROJECT_ROOT / "data" / "osm"
QUERY_PATH = OSM_DIR / "london-green-space-entrances.overpassql"
ENDPOINT = "https://overpass-api.de/api/interpreter"
GREEN_TAGS = {"park", "garden", "nature_reserve", "recreation_ground"}
RELEVANT_TAGS = (
    "name",
    "entrance",
    "routing:entrance",
    "barrier",
    "access",
    "foot",
    "wheelchair",
    "opening_hours",
    "service",
    "emergency",
)


def _retrieve(query: str) -> dict[str, Any]:
    try:
        response = httpx.post(
            ENDPOINT,
            data={"data": query},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=210,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise TypeError("Overpass returned a non-object response")
        return value
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise RuntimeError("The bounded Overpass entrance refresh failed.") from exc


def process_elements(
    elements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_type_id = {
        (str(item.get("type")), int(item.get("id"))): item
        for item in elements
        if item.get("type") in {"node", "way", "relation"} and item.get("id")
    }
    green_ways = {
        key[1]: value
        for key, value in by_type_id.items()
        if key[0] == "way"
        and (value.get("tags") or {}).get("leisure") in GREEN_TAGS
    }
    green_relations = {
        key[1]: value
        for key, value in by_type_id.items()
        if key[0] == "relation"
        and (value.get("tags") or {}).get("leisure") in GREEN_TAGS
    }
    relation_way_to_sites: dict[int, list[int]] = {}
    for relation_id, relation in green_relations.items():
        for member in relation.get("members") or []:
            if member.get("type") == "way" and member.get("role") in {"outer", ""}:
                relation_way_to_sites.setdefault(int(member["ref"]), []).append(
                    relation_id
                )
    node_associations: dict[int, set[tuple[str, int]]] = {}
    for way_id, way in {
        key[1]: value
        for key, value in by_type_id.items()
        if key[0] == "way"
    }.items():
        associations: set[tuple[str, int]] = set()
        if way_id in green_ways:
            associations.add(("way", way_id))
        associations.update(
            ("relation", relation_id)
            for relation_id in relation_way_to_sites.get(way_id, [])
        )
        for node_id in way.get("nodes") or []:
            node_associations.setdefault(int(node_id), set()).update(associations)

    counts = {
        "raw_tagged_nodes": 0,
        "retained": 0,
        "excluded_access_or_role": 0,
        "excluded_unassociated": 0,
    }
    features: list[dict[str, Any]] = []
    for (element_type, node_id), node in by_type_id.items():
        if element_type != "node":
            continue
        tags = {
            str(key): str(value)
            for key, value in (node.get("tags") or {}).items()
            if key in RELEVANT_TAGS
        }
        if not (
            tags.get("entrance")
            or tags.get("routing:entrance")
            or tags.get("barrier") == "gate"
        ):
            continue
        counts["raw_tagged_nodes"] += 1
        if not entrance_is_eligible(tags):
            counts["excluded_access_or_role"] += 1
            continue
        associations = sorted(node_associations.get(node_id, set()))
        if not associations:
            counts["excluded_unassociated"] += 1
            continue
        if node.get("lon") is None or node.get("lat") is None:
            continue
        features.append(
            {
                "type": "Feature",
                "id": f"osm-node-{node_id}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        round(float(node["lon"]), 7),
                        round(float(node["lat"]), 7),
                    ],
                },
                "properties": {
                    "osm_type": "node",
                    "osm_id": node_id,
                    "access_certainty": entrance_access_certainty(tags).value,
                    "source_tags": tags,
                    "site_associations": [
                        {
                            "site_id": f"osm-{site_type}-{site_id}",
                            "site_osm_type": site_type,
                            "site_osm_id": site_id,
                            "association_method": "osm_boundary_member",
                        }
                        for site_type, site_id in associations
                    ],
                },
            }
        )
    features.sort(key=lambda item: item["id"])
    counts["retained"] = len(features)
    return features, counts


def generate_snapshot(output_dir: Path = OSM_DIR) -> tuple[Path, Path]:
    query = QUERY_PATH.read_text(encoding="utf-8")
    raw = _retrieve(query)
    features, counts = process_elements(raw.get("elements") or [])
    if not features:
        raise RuntimeError("Overpass returned no eligible associated entrances.")
    retrieved_at = datetime.now(UTC).replace(microsecond=0)
    version = retrieved_at.date().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = output_dir / f"london-public-green-space-entrances-{version}.geojson"
    provenance = output_dir / (
        f"london-public-green-space-entrances-{version}.provenance.json"
    )
    snapshot.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "London OSM public-green-space entrances",
                "snapshot_version": version,
                "attribution": "© OpenStreetMap contributors",
                "features": features,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    provenance.write_text(
        json.dumps(
            {
                "source_name": "OpenStreetMap via Overpass API",
                "source_url": "https://www.openstreetmap.org/",
                "endpoint": ENDPOINT,
                "request_parameters": {
                    "query_file": str(QUERY_PATH.relative_to(PROJECT_ROOT))
                },
                "retrieved_at_utc": retrieved_at.isoformat().replace("+00:00", "Z"),
                "snapshot_version": version,
                "licence": "Open Database Licence (ODbL) 1.0",
                "attribution": "© OpenStreetMap contributors",
                "checksum_sha256": file_sha256(snapshot),
                "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "feature_count": len(features),
                "record_counts": counts,
                "filtering_rules": [
                    "Retain entrance=*, routing:entrance=* or barrier=gate nodes only when they are members of the associated green-space boundary identity.",
                    "Exclude access=no/private, foot=no/private, emergency, service and exit-only nodes.",
                    "Treat missing access as uncertain; never infer public access from entrance or gate tags.",
                    "Prefer routing:entrance=main/main_entrance, then entrance=main/main_entrance, then explicit-public access at runtime.",
                ],
                "known_limitations": [
                    "OSM entrance tags identify mapped physical access points, not legal access rights or guaranteed opening.",
                    "Entrances not mapped as members of a site boundary are deliberately omitted.",
                    "Community-maintained access, foot and wheelchair tags may be incomplete or outdated.",
                    "No centroid, internal sample point or nearest-road substitute is generated.",
                ],
                "regeneration_command": "python -m scripts.generate_entrance_snapshot --live-refresh",
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return snapshot, provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-refresh", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OSM_DIR)
    args = parser.parse_args()
    if not args.live_refresh:
        parser.error("entrance snapshot generation requires explicit --live-refresh")
    snapshot, provenance = generate_snapshot(args.output_dir)
    print(f"WROTE {snapshot}")
    print(f"WROTE {provenance}")


if __name__ == "__main__":
    main()
