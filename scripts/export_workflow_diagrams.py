"""Export the current compiled graph for the documentation renderer (offline)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.biodiversity.api.services.topology import build_workflow_topology
from app.biodiversity.graph import build_biodiversity_graph
from app.biodiversity.testing import make_scripted_biodiversity_models


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the export is stale.")
    args = parser.parse_args()
    request_model, evidence_model, composer_model = make_scripted_biodiversity_models()
    graph = build_biodiversity_graph(
        parser_model=request_model,
        evidence_model=evidence_model,
        composer_model=composer_model,
    )
    topology = build_workflow_topology(graph).model_dump(mode="json")
    path = Path(__file__).resolve().parents[1] / "docs/diagrams/langgraph-topology.json"
    content = json.dumps(topology, indent=2, ensure_ascii=True) + "\n"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise SystemExit("Diagram topology is stale. Run this module without --check.")
    else:
        path.write_text(content, encoding="utf-8")
    print(
        f"{'Verified' if args.check else 'Exported'} {topology['workflow_version']}: "
        f"{len(topology['nodes'])} nodes (including START/END), "
        f"{len(topology['edges'])} edges. No agent execution or network requests."
    )


if __name__ == "__main__":
    main()
