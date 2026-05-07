"""Tests for :99 headless display fallback in chronicle and parallel_tools.

These run without a real X session. They patch xdpyinfo to simulate :99
being up or down and verify that both tools react correctly.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# chronicle._resolve_display
# ---------------------------------------------------------------------------

def _import_chronicle():
    import importlib, sys
    # Force fresh import so module-level state is clean
    sys.modules.pop("tools.chronicle", None)
    sys.modules.pop("chronicle", None)
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools import chronicle
    return chronicle


def test_resolve_display_uses_env_when_set(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":5")
    chron = _import_chronicle()
    assert chron._resolve_display() == ":5"


def test_resolve_display_falls_back_to_99_when_xvfb_up(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result):
        chron = _import_chronicle()
        display = chron._resolve_display()
    assert display == ":99"
    assert os.environ.get("DISPLAY") == ":99"


def test_resolve_display_returns_none_when_xvfb_down(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("subprocess.run", return_value=mock_result):
        chron = _import_chronicle()
        # Reset any cached env set by previous test
        monkeypatch.delenv("DISPLAY", raising=False)
        display = chron._resolve_display()
    assert display is None


def test_tick_skips_when_no_display(monkeypatch, caplog):
    monkeypatch.delenv("DISPLAY", raising=False)
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("subprocess.run", return_value=mock_result):
        chron = _import_chronicle()
        monkeypatch.delenv("DISPLAY", raising=False)
        import logging
        with caplog.at_level(logging.DEBUG, logger="nexus.chronicle"):
            chron._tick()
    assert any("no display" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# parallel_tools._resolve_display_for_scrot
# ---------------------------------------------------------------------------

def _import_parallel():
    import sys
    sys.modules.pop("tools.parallel_tools", None)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools import parallel_tools
    return parallel_tools


def test_parallel_resolve_uses_env(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":3")
    pt = _import_parallel()
    assert pt._resolve_display_for_scrot() == ":3"


def test_parallel_resolve_falls_back_to_99(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    mock_ok = MagicMock()
    mock_ok.returncode = 0
    with patch("subprocess.run", return_value=mock_ok):
        pt = _import_parallel()
        assert pt._resolve_display_for_scrot() == ":99"


def test_parallel_resolve_none_when_no_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    mock_fail = MagicMock()
    mock_fail.returncode = 1
    with patch("subprocess.run", return_value=mock_fail):
        pt = _import_parallel()
        assert pt._resolve_display_for_scrot() is None


def test_take_screenshot_returns_error_string_when_no_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    mock_fail = MagicMock()
    mock_fail.returncode = 1
    with patch("subprocess.run", return_value=mock_fail):
        pt = _import_parallel()
        result = pt._take_screenshot()
    assert "no display" in result
