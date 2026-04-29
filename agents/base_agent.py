"""Base Agent class that all sub-agents inherit from."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

OLLAMA_URL = "http://localhost:11434"
MEMORY_DIR = Path.home() / "AI_Agent" / "memory"


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"


@dataclass
class Task:
    """Represents a task to be executed by an agent."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    task_type: str = "general"
    priority: int = 5  # 1 = highest, 10 = lowest
    assigned_to: Optional[str] = None
    status: AgentStatus = AgentStatus.IDLE
    result: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


_TOOLS_MD_PATH = Path(__file__).resolve().parent.parent / "TOOLS.md"


def _tool_inventory_block() -> str:
    """Append TOOLS.md to every sub-agent's system prompt so the model
    never denies a capability that exists in the live registry. Returns
    empty string if TOOLS.md isn't generated yet (first-boot guard)."""
    if not _TOOLS_MD_PATH.exists():
        return ""
    try:
        body = _TOOLS_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    return (
        "\n\n# AVAILABLE TOOLS\n"
        "You have access to these tools — use them, don't deny capability:\n\n"
        + body
    )


class BaseAgent(ABC):
    """Base class for all Nexus sub-agents."""

    def __init__(
        self,
        name: str,
        model: str = "qwen3:4b",
        system_prompt: Optional[str] = None,
    ):
        self.id = str(uuid.uuid4())[:8]
        self.name = name
        self.model = model
        base_prompt = system_prompt or self._default_system_prompt()
        self.system_prompt = base_prompt + _tool_inventory_block()
        self.status = AgentStatus.IDLE
        self.current_task: Optional[Task] = None
        self.task_history: List[Task] = []
        self.created_at = time.time()

    def _default_system_prompt(self) -> str:
        return f"""You are {self.name}, a specialized AI agent in the Nexus system.
You work as part of a team of agents, each with specific expertise.
Be concise, accurate, and collaborative. Report your findings clearly.
If you encounter issues, describe them precisely so they can be resolved."""

    async def execute_task(self, task: Task) -> str:
        """Execute a task and return the result."""
        self.current_task = task
        self.status = AgentStatus.RUNNING
        task.status = AgentStatus.RUNNING
        task.assigned_to = self.name

        # Phase 19.5 — fan agent lifecycle into the event bus so the
        # Sparky overlay can render anchored mini-bubbles per sub-agent.
        try:
            from core import event_bus
            event_bus.emit(
                "subagent_started",
                agent=self.name, agent_id=self.id, task_id=task.id,
                description=(task.description or "")[:200],
            )
        except Exception:
            pass

        try:
            result = await self._run(task)
            task.status = AgentStatus.COMPLETED
            task.result = result
            task.completed_at = time.time()
            self.status = AgentStatus.IDLE
            self.task_history.append(task)
            try:
                from core import event_bus
                event_bus.emit(
                    "subagent_completed",
                    agent=self.name, agent_id=self.id, task_id=task.id,
                    result_preview=(result or "")[:200],
                )
            except Exception:
                pass
            return result

        except Exception as e:
            task.status = AgentStatus.FAILED
            task.result = f"Error: {type(e).__name__}: {e}"
            task.completed_at = time.time()
            self.status = AgentStatus.IDLE
            self.task_history.append(task)
            try:
                from core import event_bus
                event_bus.emit(
                    "subagent_failed",
                    agent=self.name, agent_id=self.id, task_id=task.id,
                    error=task.result[:200],
                )
            except Exception:
                pass
            return task.result

        finally:
            self.current_task = None

    @abstractmethod
    async def _run(self, task: Task) -> str:
        """Implement the actual task execution logic."""
        pass

    def call_llm(self, prompt: str, max_tokens: int = 1000) -> str:
        """Call the LLM via Ollama."""
        try:
            response = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "system": self.system_prompt,
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
                timeout=120,
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "").strip()
        except Exception as e:
            return f"LLM Error: {type(e).__name__}: {e}"

    def handoff_to(self, agent_name: str, task: Task) -> None:
        """Hand off a task to another agent (handled by orchestrator)."""
        task.metadata["handoff_from"] = self.name
        task.metadata["handoff_to"] = agent_name
        task.assigned_to = None
        task.status = AgentStatus.WAITING

    def to_dict(self) -> Dict[str, Any]:
        """Serialize agent state to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "status": self.status.value,
            "current_task": self.current_task.id if self.current_task else None,
            "tasks_completed": len(self.task_history),
            "created_at": self.created_at,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} status={self.status.value}>"
