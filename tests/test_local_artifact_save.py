"""Tests for the all-local artifact save (2026-07-12).

Covers:
  - Fenced ```html block → extracted and saved to games dir
  - Bare <!DOCTYPE…</html> (no fences, per the local system prompt) → saved
  - Multiple fenced blocks (bash + html) → the HTML one wins, not bash
  - Log with no HTML (chat-style answer) → nothing saved, returns None
  - Name collision → second save gets a -<id> suffixed name
  - _summarize_log_tail no longer returns a bare fence line
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path.home() / "AI_Agent"
sys.path.insert(0, str(ROOT))

import workers.cc_dispatcher as mod

HTML = "<!DOCTYPE html>\n<html><head><title>t</title></head><body><canvas></canvas></body></html>"


def _write_log(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cc_test.log"
    p.write_text(text, encoding="utf-8")
    return p


def _patch_games_dir(monkeypatch, tmp_path: Path) -> Path:
    games = tmp_path / "AI_Agent" / "games"
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    return games


def test_fenced_html_saved(tmp_path, monkeypatch):
    games = _patch_games_dir(monkeypatch, tmp_path)
    log = _write_log(tmp_path, f"Here is your game:\n```html\n{HTML}\n```\nEnjoy!")
    out = mod._save_local_artifact(log, "build me a snake game", "cc_aabbccdd")
    assert out is not None and out.parent == games
    assert out.name == "build-me-a-snake-game.html"
    assert "<canvas>" in out.read_text()


def test_bare_html_saved(tmp_path, monkeypatch):
    _patch_games_dir(monkeypatch, tmp_path)
    log = _write_log(tmp_path, f"Sure, here it is.\n\n{HTML}\n\nDone.")
    out = mod._save_local_artifact(log, "pong", "cc_aabbccdd")
    assert out is not None
    assert out.read_text().strip().startswith("<!DOCTYPE html")


def test_html_block_beats_bash_block(tmp_path, monkeypatch):
    _patch_games_dir(monkeypatch, tmp_path)
    bash = "```bash\ncp game.html ~/AI_Agent/games/  # a longer bash block " + "x" * 200 + "\n```"
    log = _write_log(tmp_path, f"{bash}\n```html\n{HTML}\n```")
    out = mod._save_local_artifact(log, "clock", "cc_aabbccdd")
    assert out is not None
    assert "<!DOCTYPE html" in out.read_text()
    assert "cp game.html" not in out.read_text()


def test_no_html_returns_none(tmp_path, monkeypatch):
    _patch_games_dir(monkeypatch, tmp_path)
    log = _write_log(tmp_path, "The answer is 42. No code needed.")
    assert mod._save_local_artifact(log, "question", "cc_aabbccdd") is None


def test_collision_gets_suffixed_name(tmp_path, monkeypatch):
    _patch_games_dir(monkeypatch, tmp_path)
    log = _write_log(tmp_path, f"```html\n{HTML}\n```")
    first = mod._save_local_artifact(log, "snake", "cc_11223344")
    second = mod._save_local_artifact(log, "snake", "cc_55667788")
    assert first.name == "snake.html"
    assert second.name == "snake-556677.html"


def test_summary_skips_fence_lines(tmp_path):
    log = _write_log(tmp_path, "All done, game complete.\n```\n")
    assert mod._summarize_log_tail(log) == "All done, game complete."
