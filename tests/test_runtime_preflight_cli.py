from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_help_from_clean_import_path(monkeypatch: pytest.MonkeyPatch, script: Path) -> None:
    clean_path = [item for item in sys.path if item not in {"", str(ROOT)}]
    monkeypatch.setattr(sys, "path", clean_path)
    for name in ("app", "app.runtime_discovery", "app.runtime_binding"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "argv", [script.name, "--help"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(script), run_name="__main__")

    assert exc_info.value.code == 0
    assert str(ROOT) in sys.path


def test_runtime_preflight_bootstraps_repository_root(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_help_from_clean_import_path(monkeypatch, ROOT / "scripts" / "runtime_preflight.py")


def test_cognisymbiosis_preflight_bootstraps_repository_root(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_help_from_clean_import_path(monkeypatch, ROOT / "scripts" / "cognisymbiosis_runtime_preflight.py")
