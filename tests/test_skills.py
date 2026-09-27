"""Step 4 skills: store, prompt block, and the post-task review policy."""
from __future__ import annotations

import json

import pytest

from core import skills

TRACE = ["terminal(systemctl status x) → inactive", "terminal(journalctl -u x) → port in use",
         "terminal(systemctl restart x) → ok"]


@pytest.fixture(autouse=True)
def tmp_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")


def fake_chat(decision: dict):
    calls = []

    def chat(messages, **kw):
        calls.append(messages)
        return json.dumps(decision)
    chat.calls = calls
    return chat


def test_save_read_counts_uses_and_lists():
    assert skills.save("Check Service Down!", "a service is down", "1. status\n2. logs") \
        == "check-service-down"
    assert skills.read("check-service-down") == "1. status\n2. logs"
    skills.read("check-service-down")
    (s,) = skills.list_skills()
    assert (s["name"], s["when"], s["uses"]) == ("check-service-down", "a service is down", 2)
    assert "- check-service-down — a service is down" in skills.prompt_block()


def test_resave_keeps_use_count_and_missing_is_empty():
    skills.save("x", "w", "a")
    skills.read("x")
    skills.save("x", "w2", "b")
    assert skills.get("x")["uses"] == 1 and skills.get("x")["body"] == "b"
    assert skills.read("nope") == ""
    assert skills.prompt_block().startswith("# SKILLS")


def test_empty_library_adds_nothing_to_prompt():
    assert skills.prompt_block() == ""


def test_review_skips_short_tasks_without_calling_brain():
    chat = fake_chat({"action": "create", "name": "n", "when": "w", "body": "b"})
    assert skills.review_task("t", "r", TRACE[:2], chat=chat)["saved"] is None
    assert chat.calls == []


def test_review_creates_skill_and_lists_existing_in_prompt():
    skills.save("old-one", "old when", "steps")
    chat = fake_chat({"action": "create", "name": "Restart Stuck Service",
                      "when": "service won't start", "body": "1. check port"})
    d = skills.review_task("fix x", "done", TRACE, chat=chat)
    assert d["saved"] == "restart-stuck-service"
    assert "old-one — old when" in chat.calls[0][0]["content"]


def test_review_none_and_secret_bodies_save_nothing():
    assert skills.review_task("t", "r", TRACE, chat=fake_chat(
        {"action": "none", "name": "", "when": "", "body": ""}))["saved"] is None
    d = skills.review_task("t", "r", TRACE, chat=fake_chat(
        {"action": "create", "name": "leaky", "when": "w",
         "body": "1. export API_KEY=abcd1234efgh5678"}))
    assert d["saved"] is None and skills.list_skills() == []


def test_patch_of_unknown_skill_becomes_create():
    d = skills.review_task("t", "r", TRACE, chat=fake_chat(
        {"action": "patch", "name": "ghost", "when": "w", "body": "1. x"}))
    assert (d["action"], d["saved"]) == ("create", "ghost")
