"""
pytest wrapper over the smoke suites.

The suites are standalone scripts on purpose - they run with nothing installed
but the standard library, which matters when someone clones this repo and wants
to check it works before committing to a toolchain. This wrapper exists so CI
and `pytest` users get the same coverage without a second implementation.

Each suite runs in its own subprocess. A suite that crashes the interpreter, or
leaves import state behind, then fails alone instead of taking the run with it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
REPO = TESTS.parent

SUITES = [
    pytest.param("smoke_iq.py", id="iq-layers"),
    pytest.param("smoke_mcp.py", id="mcp-servers"),
    pytest.param("smoke_api.py", id="api-routing"),
    pytest.param("smoke_containers.py", id="container-layout"),
    pytest.param("smoke_web.py", id="operator-console"),
    pytest.param("smoke_scenarios.py", id="demo-scenarios"),
]


@pytest.mark.parametrize("suite", SUITES)
def test_smoke_suite(suite: str) -> None:
    script = TESTS / suite
    assert script.is_file(), f"missing smoke suite: {suite}"

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        # Surface the suite's own report - it names the failing check, which is
        # far more useful than an assertion on the exit code.
        pytest.fail(
            f"{suite} failed (exit {result.returncode})\n\n"
            f"{result.stdout}\n{result.stderr}",
            pytrace=False,
        )
