"""Tests for Phase 23.1 scaffolding intent detection + route_message wiring."""
from __future__ import annotations

import pytest


# --- 1. _detect_scaffold_intent — recipe selection ----------------------
@pytest.mark.parametrize("msg,expected_recipe", [
    ("Scaffold a Next.js marketplace called shoppable", "nextjs-marketplace"),
    ("create a Next.js app for X", "nextjs-landing"),  # no specifier → landing fallback
    # When both keywords present, more specific first-match wins —
    # 'saas' is checked before 'dashboard' because "SaaS app" is the
    # bigger product category, dashboard is a UI flavor.
    ("create a SaaS dashboard called my-saas", "nextjs-saas"),
    ("create an analytics dashboard called metrics-app", "nextjs-dashboard"),
    ("spin up a SaaS app called creator", "nextjs-saas"),
    ("build me a landing page for the new product", "nextjs-landing"),
    ("scaffold a marketplace named shoppable-video", "nextjs-marketplace"),
    ("set up a FastAPI backend called api-server", "python-fastapi"),
    ("create a Click CLI called toolname", "python-cli"),
    ("start a new project for stripe-connect testing", "nextjs-marketplace"),
])
def test_detect_recipe(msg: str, expected_recipe: str) -> None:
    from workers.conversation_handler import _detect_scaffold_intent
    out = _detect_scaffold_intent(msg)
    assert out is not None, f"no intent detected for: {msg!r}"
    assert out["recipe"] == expected_recipe


# --- 2. _detect_scaffold_intent — non-scaffold messages return None ------
@pytest.mark.parametrize("msg", [
    "what's the weather in Pasco WA",
    "create an issue on the cli repo",      # GitHub op, not a scaffold
    "build me a research summary",          # research, not scaffold
    "scaffold",                              # bare trigger, no recipe hint
    "",
])
def test_no_intent_for_non_scaffold(msg: str) -> None:
    from workers.conversation_handler import _detect_scaffold_intent
    assert _detect_scaffold_intent(msg) is None


# --- 3. Name extraction --------------------------------------------------
def test_name_extracted_from_called_clause() -> None:
    from workers.conversation_handler import _detect_scaffold_intent
    out = _detect_scaffold_intent("Scaffold a Next.js marketplace called shoppable-video")
    assert out["name"] == "shoppable-video"
    assert out["missing"] == []


def test_name_extracted_from_quotes() -> None:
    from workers.conversation_handler import _detect_scaffold_intent
    out = _detect_scaffold_intent('Create a SaaS app "creator-os"')
    assert out["name"] == "creator-os"


def test_missing_name_flagged() -> None:
    from workers.conversation_handler import _detect_scaffold_intent
    out = _detect_scaffold_intent("Scaffold a Next.js marketplace")
    assert out["recipe"] == "nextjs-marketplace"
    assert out["name"] is None
    assert out["missing"] == ["name"]


# --- 4. route_message asks for a name when missing ----------------------
def test_route_message_prompts_for_name(monkeypatch) -> None:
    from workers import conversation_handler as ch

    # Make sure the classifier never runs.
    def loud(msg):
        pytest.fail("classifier should not run when scaffold intent fires")
    monkeypatch.setattr(ch, "classify_intent_llm", loud)

    res = ch.route_message("Scaffold a Next.js marketplace")
    assert res["kind"] == "scaffold"
    assert res["meta"]["needs_name"] is True
    assert res["meta"]["scaffold_recipe"] == "nextjs-marketplace"
    assert "name" in res["reply"].lower()


# --- 5. route_message enqueues a structured task with name + recipe -----
def test_route_message_enqueues_scaffold_task(monkeypatch) -> None:
    from workers import conversation_handler as ch

    enqueued: list[str] = []
    monkeypatch.setattr(ch.task_queue, "enqueue",
                        lambda input_text, **_: (enqueued.append(input_text) or "scaffold01"))

    def loud(msg):
        pytest.fail("classifier should not run for matched scaffold intent")
    monkeypatch.setattr(ch, "classify_intent_llm", loud)

    res = ch.route_message("Scaffold a Next.js marketplace called shoppable")
    assert res["kind"] == "scaffold"
    assert res["meta"]["scaffold_name"] == "shoppable"
    assert res["meta"]["scaffold_recipe"] == "nextjs-marketplace"
    assert res["meta"]["task_id"] == "scaffold01"
    assert "task_id=scaffold01" in res["reply"]

    assert len(enqueued) == 1
    enq_input = enqueued[0]
    assert "[scaffold:nextjs-marketplace]" in enq_input
    assert 'name="shoppable"' in enq_input
    assert "scaffold_project" in enq_input
