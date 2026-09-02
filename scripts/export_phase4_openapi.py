"""Export the deterministic Phase 4 OpenAPI document for frontend type generation."""

from __future__ import annotations

import json
from pathlib import Path

from app.biodiversity.api.main import create_app


PROJECT_ROOT = Path(__file__).parents[1]
OUTPUT = PROJECT_ROOT / "docs" / "phase-4-openapi.json"


def main() -> None:
    OUTPUT.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
