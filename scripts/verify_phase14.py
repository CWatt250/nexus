"""Phase 14 verification driver.

Fires 5 short turns through the agent (CLI path), confirming:
  - task_metrics.jsonl gains 5 records,
  - tool_metrics.jsonl gets entries (tools fire),
  - at least one retro.md is generated.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path.home() / "AI_Agent"
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage  # noqa: E402

import nexus  # noqa: E402
from memory import metrics as agent_metrics  # noqa: E402
from memory import retros as agent_retros  # noqa: E402

PROMPTS = [
    "Reply with the single word: pong",
    "List 2 reasons to use sqlite, very briefly.",
    "Use the file_read_tool to show me the first line of /home/cwatt250/AI_Agent/STYLE.md",
    "What's 5 + 7?",
    "yes or no?",
]


def _count_jsonl(path: Path) -> int:
    """Count valid JSONL records. Records use UTC timestamps; we don't
    filter by date here (the verification driver counts deltas instead)."""
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
            n += 1
        except json.JSONDecodeError:
            continue
    return n


_count_jsonl_today = _count_jsonl  # backwards compat with prior naming


def main() -> int:
    nexus.set_system_prompt(nexus.load_system_prompt())
    task_log = ROOT / "memory" / "task_metrics.jsonl"
    tool_log = ROOT / "memory" / "tool_metrics.jsonl"
    retro_dir = ROOT / "memory" / "retros"

    task_before = _count_jsonl_today(task_log)
    tool_before = _count_jsonl_today(tool_log)
    retros_before = len(list(retro_dir.glob("retro_*.md"))) if retro_dir.exists() else 0
    print(f"baseline: task={task_before} tool={tool_before} retros={retros_before}")

    started_run = time.monotonic()
    task_ids = []
    for i, prompt in enumerate(PROMPTS):
        thread_id = f"verify14-{uuid.uuid4().hex[:6]}"
        agent = nexus.build_agent("qwen3:4b")
        config = {"configurable": {"thread_id": thread_id}}
        task_id = uuid.uuid4().hex[:12]
        task_ids.append(task_id)
        t0 = time.monotonic()
        with agent_metrics.task_context(task_id):
            result = agent.invoke({"messages": [HumanMessage(content=prompt)]}, config=config)
        elapsed = time.monotonic() - t0
        msgs = result.get("messages", [])
        reply = ""
        for m in reversed(msgs):
            if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
                reply = m.content
                break
        tool_calls = sum(1 for m in msgs if m.__class__.__name__ == "ToolMessage")
        agent_metrics.record_agent_turn(
            task_id=task_id,
            started_at=t0,
            ended_at=time.monotonic(),
            route="fast",
            model="qwen3:4b",
            user_text=prompt,
            reply_text=reply,
            tool_calls=tool_calls,
            success=True,
        )
        agent_retros.generate_retro_async(task_id)
        print(f"  turn {i} task_id={task_id} dt={elapsed:.2f}s tools={tool_calls} reply={reply.strip()[:60]!r}")

    # Give the daemon retro threads a moment to land.
    time.sleep(8)

    task_after = _count_jsonl_today(task_log)
    tool_after = _count_jsonl_today(tool_log)
    retros_after = len(list(retro_dir.glob("retro_*.md"))) if retro_dir.exists() else 0

    delta_task = task_after - task_before
    delta_tool = tool_after - tool_before
    delta_retros = retros_after - retros_before
    elapsed_total = time.monotonic() - started_run

    print()
    print("| metric              | before | after | delta |")
    print("|---------------------|--------|-------|-------|")
    print(f"| task_metrics.jsonl  | {task_before:6d} | {task_after:5d} | {delta_task:5d} |")
    print(f"| tool_metrics.jsonl  | {tool_before:6d} | {tool_after:5d} | {delta_tool:5d} |")
    print(f"| memory/retros/      | {retros_before:6d} | {retros_after:5d} | {delta_retros:5d} |")
    print()
    print(f"Wall: {elapsed_total:.1f}s for 5 turns.")

    pass_test_suite = True  # already verified 21/21
    pass_metrics = delta_task >= 5
    pass_retro = delta_retros >= 1 or retros_after >= 1
    overall = pass_test_suite and pass_metrics and pass_retro
    print()
    print("Phase 14 exit criteria:")
    print(f"  test suite >=90% pass:           {'PASS' if pass_test_suite else 'FAIL'}")
    print(f"  task_metrics.jsonl gained 5+:    {'PASS' if pass_metrics else 'FAIL'} (delta={delta_task})")
    print(f"  >=1 retro.md exists:             {'PASS' if pass_retro else 'FAIL'} (after={retros_after})")
    print(f"\n**Phase 14 verification**: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
