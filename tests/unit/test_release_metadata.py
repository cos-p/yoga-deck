import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from yoga_deck import __version__
from yoga_deck.cli import main

_ROOT = Path(__file__).parents[2]


def _is_git_ignored(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(_ROOT), "check-ignore", "-q", str(path)],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _publication_text_files() -> list[Path]:
    roots = [
        _ROOT / ".github",
        _ROOT / "docs",
        _ROOT / "config",
        _ROOT / "hooks",
        _ROOT / "omarchy-plugin",
        _ROOT / "src",
        _ROOT / "systemd",
        _ROOT / "tests",
    ]
    # Root files the repository ignores (local-only notes) are never published.
    files = [path for path in _ROOT.iterdir() if path.is_file() and not _is_git_ignored(path)]
    for root in roots:
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    return sorted(set(files))


def _is_deliberate_test_fixture(path: Path, label: str, value: str) -> bool:
    """Allow only the exact synthetic identifiers used to prove redaction/discovery."""

    relative = path.relative_to(_ROOT)
    if not relative.parts or relative.parts[0] != "tests":
        return False
    if label == "private home path":
        return value == "/home/alice/"
    if label == "volatile input node":
        return bool(re.fullmatch(r"/dev/input/event(?:3|9|12|71|99)", value))
    return False


def test_public_alpha_version_is_consistent(capsys) -> None:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    plugin = json.loads((_ROOT / "omarchy-plugin/manifest.json").read_text())

    assert project["project"]["version"] == "0.1.0"
    assert plugin["version"] == "0.1.0"
    assert __version__ == "0.1.0"

    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "yoga-doctor 0.1.0"


def test_public_release_metadata_agrees() -> None:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]
    plugin = json.loads((_ROOT / "omarchy-plugin/manifest.json").read_text())
    license_text = (_ROOT / "LICENSE").read_text()

    assert project["license"]["text"] == "MIT"
    assert plugin["license"] == "MIT"
    assert license_text.startswith("MIT License\n")
    assert project["authors"][0]["name"] == plugin["author"]
    assert f"Copyright (c) 2026 {project['authors'][0]['name']}" in license_text


def test_publishable_evidence_has_no_private_identifiers() -> None:
    forbidden = {
        "private home path": re.compile(r"/(?:home|Users)/[^/\s]+/"),
        "Windows user path": re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\s]+"),
        "email address": re.compile(r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}"),
        "IPv4 address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "MAC address": re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b"),
        "UUID": re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
            r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
        ),
        "volatile input node": re.compile(r"/dev/input/event\d+"),
    }

    findings: list[str] = []
    for path in _publication_text_files():
        text = path.read_text()
        for label, pattern in forbidden.items():
            for match in pattern.finditer(text):
                if not _is_deliberate_test_fixture(path, label, match.group(0)):
                    findings.append(f"{path.relative_to(_ROOT)}: {label}")

    assert findings == []
