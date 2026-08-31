"""Regression guard proving Phase 1.1 leaves incident infrastructure unchanged."""

from __future__ import annotations

import subprocess

from app.feasibility.core import PROJECT_ROOT

PHASE01_COMMIT = "00e2700d46ee59943ccae33612463f504c979bd0"
PROTECTED_PATHS = (
    "app/agents",
    "app/graph",
    "app/schemas.py",
    "app/streaming",
    "app/testing",
    "app/tools/incident_tools.py",
    "scripts/run_agent.py",
    "scripts/run_workflow.py",
    "tests/test_event_streaming.py",
    "tests/test_workflow.py",
)


def test_incident_infrastructure_has_no_diff_from_phase01() -> None:
    completed = subprocess.run(
        ["git", "diff", "--name-only", PHASE01_COMMIT, "--", *PROTECTED_PATHS],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == ""
