"""Exercise the complete userscript with GM and DOM adapters, never real cookies."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aistudio_api.api.diagnostics_client import BrowserExportRequest


def test_userscript_results_storage_downloads_and_backend_contract():
    runtime = shutil.which("bun") or shutil.which("node")
    if runtime is None:
        pytest.skip("Userscript runtime checks require Bun or Node.js")
    harness = Path(__file__).parents[1] / "fixtures/userscript_diagnostics.mjs"
    result = subprocess.run(
        [runtime, str(harness)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["scenarios"] >= 17
    validated = BrowserExportRequest.model_validate(
        {"client_report": report["sample_report"]}
    )
    assert any(
        event.outcome == "unconfirmed" for event in validated.client_report.events
    )
