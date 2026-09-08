"""Build a full same-snapshot fixture candidate with hardened safe-map cells."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.validate_evidence_fixtures import build_live_candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = build_live_candidate(args.output_dir)
    occurrence = json.loads(
        (output / "gbif-occurrences-london.json").read_text(encoding="utf-8")
    )
    print(
        json.dumps(
            {
                "candidate": str(output),
                "refresh_scope": "full_same_snapshot_fixture_set",
                "occurrence_promotion_command": (
                    "python -m scripts.validate_evidence_fixtures "
                    f"--promote-occurrence-candidate {output}"
                ),
                "safe_cell_counts": {
                    item["input"]: len(item.get("safe_map_cells", []))
                    for item in occurrence["payload"]["results"]
                },
                "raw_occurrence_coordinates_persisted": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
