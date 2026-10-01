"""Headless Qt Test coverage for the real plugin panel with inert shell-boundary doubles."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
QML_TEST = ROOT / "tests" / "qml" / "tst_panel.qml"
QML_IMPORTS = ROOT / "tests" / "qml" / "imports"


def _qmltestrunner() -> str | None:
    discovered = shutil.which("qmltestrunner")
    packaged = Path("/usr/lib/qt6/bin/qmltestrunner")
    return discovered or (str(packaged) if packaged.is_file() else None)


def test_plugin_panel_renders_and_degrades_under_the_qt_engine() -> None:
    runner = _qmltestrunner()
    if runner is None:
        pytest.skip("Qt qmltestrunner is not available")

    completed = subprocess.run(
        [
            runner,
            "-import",
            str(QML_IMPORTS),
            "-input",
            str(QML_TEST),
            "-o",
            "-,txt",
        ],
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
