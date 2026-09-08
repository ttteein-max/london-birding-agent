"""Export or verify the current FastAPI contract for the generated browser client."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.biodiversity.api.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "docs" / "api" / "openapi.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the contract is stale.")
    args = parser.parse_args()
    content = json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI contract is stale. Run this module without --check.")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(content, encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
