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
        self.system_prompt = system_prompt or self._default_system_prompt()
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

        try:
            result = await self._run(task)
            task.status = AgentStatus.COMPLETED
            task.result = result
            task.completed_at = time.time()
            self.status = AgentStatus.IDLE
            self.task_history.append(task)
            return result

        except Exception as e:
            task.status = AgentStatus.FAILED
            task.result = f"Error: {type(e).__name__}: {e}"
            task.completed_at = time.time()
            self.status = AgentStatus.IDLE
            self.task_history.append(task)
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
