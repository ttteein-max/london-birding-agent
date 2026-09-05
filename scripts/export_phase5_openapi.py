"""Export the Phase 5 FastAPI contract for the generated browser client."""

from __future__ import annotations

import json
from pathlib import Path

from app.biodiversity.api.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "docs" / "phase-5-openapi.json"


if __name__ == "__main__":
    OUTPUT.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)
