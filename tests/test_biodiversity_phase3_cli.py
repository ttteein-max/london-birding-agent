"""CLI and opt-in runtime-matrix coverage for Phase 3 persistence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


PROJECT_ROOT = Path(__file__).parents[1]


def _run_module(module: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
        timeout=360,
    )


def test_manage_cli_reuses_manifest_across_processes_and_records_spans(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cross-process.sqlite"
    start_report = tmp_path / "start-report"
    resume_report = tmp_path / "resume-report"
    started = _run_module(
        "scripts.manage_biodiversity_runs",
        "start",
        "--thread-id",
        "cross-process",
        "--checkpoint-db",
        str(database),
        "--request",
        (
            "Plan a two-hour expedition from SW11 4NJ on 15 January 2026 "
            "to look for robin."
        ),
        "--report-dir",
        str(start_report),
    )
    assert started.returncode == 0, started.stderr
    start_output = json.loads(started.stdout)
    assert start_output["interrupt"]["kind"] == "taxon_selection"
    accepted_key = start_output["interrupt"]["candidates"][0][
        "accepted_taxon_key"
    ]

    resumed = _run_module(
        "scripts.manage_biodiversity_runs",
        "resume",
        "--thread-id",
        "cross-process",
        "--checkpoint-db",
        str(database),
        "--resume-json",
        json.dumps({"accepted_taxon_key": accepted_key}),
        "--report-dir",
        str(resume_report),
    )
    assert resumed.returncode == 0, resumed.stderr
    resume_output = json.loads(resumed.stdout)
    assert resume_output["interrupt"]["kind"] == "actionable_tradeoff"
    metadata = json.loads((resume_report / "metadata.json").read_text())
    timings = json.loads((resume_report / "timings.json").read_text())
    assert metadata["data_mode"] == "fixture"
    assert metadata["model_mode"] == "scripted"
    assert metadata["run_manifest"]["state_schema_version"] == 2
    assert {item["kind"] for item in timings["spans"]} == {
        "node",
        "model",
        "tool",
    }

    mismatched = _run_module(
        "scripts.manage_biodiversity_runs",
        "resume",
        "--thread-id",
        "cross-process",
        "--checkpoint-db",
        str(database),
        "--data-mode",
        "live",
        "--resume-json",
        json.dumps({"option": "keep_constraints_accept_low_confidence"}),
        "--report-dir",
        str(tmp_path / "mismatch-report"),
    )
    assert mismatched.returncode == 2
    assert "does not match the saved run manifest" in mismatched.stderr


@pytest.mark.parametrize(
    ("data_mode", "model_mode"),
    [
        ("fixture", "scripted"),
        pytest.param("live", "scripted", marks=pytest.mark.live),
        pytest.param("fixture", "live", marks=pytest.mark.live),
        pytest.param("live", "live", marks=pytest.mark.live),
    ],
)
def test_phase3_data_model_runtime_matrix(
    tmp_path: Path,
    data_mode: str,
    model_mode: str,
) -> None:
    if data_mode == "live" or model_mode == "live":
        if os.getenv("RUN_LIVE_BIODIVERSITY_AGENT") != "1":
            pytest.skip("Set RUN_LIVE_BIODIVERSITY_AGENT=1 for live matrix tests")
    if model_mode == "live" and (
        not os.getenv("OPENAI_API_KEY") or not os.getenv("OPENAI_MODEL")
    ):
        pytest.skip("Live model credentials are required")
    target_date = (
        datetime.now(ZoneInfo("Europe/London")).date().isoformat()
        if data_mode == "live"
        else "2026-06-15"
    )
    completed = _run_module(
        "scripts.run_biodiversity_agent",
        "--request",
        (
            f"Plan a two-hour expedition from SW11 4NJ on {target_date} "
            "to look for Common woodpigeon."
        ),
        "--thread-id",
        f"matrix-{data_mode}-{model_mode}",
        "--checkpoint-db",
        str(tmp_path / f"{data_mode}-{model_mode}.sqlite"),
        "--data-mode",
        data_mode,
        "--model-mode",
        model_mode,
        "--auto-resume",
        "--compact",
        "--no-save-report",
    )
    assert completed.returncode == 0, completed.stderr
    assert '"terminal_status":' in completed.stdout
    assert '"run_manifest"' not in completed.stdout
