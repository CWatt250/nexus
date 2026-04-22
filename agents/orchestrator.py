"""Nexus Orchestrator — Top-level agent that delegates to sub-agents."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from .base_agent import AgentStatus, BaseAgent, Task

TASK_QUEUE_PATH = Path.home() / "AI_Agent" / "memory" / "task-queue.json"


class Orchestrator:
    """Orchestrates multiple sub-agents, routing tasks based on type."""

    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.task_queue: List[Task] = []
        self.completed_tasks: List[Task] = []
        self.running = False
        self._load_queue()

    def register_agent(self, agent: BaseAgent) -> None:
        """Register a sub-agent with the orchestrator."""
        self.agents[agent.name.lower()] = agent

    def unregister_agent(self, name: str) -> None:
        """Remove an agent from the orchestrator."""
        self.agents.pop(name.lower(), None)

    def route_task(self, task: Task) -> Optional[str]:
        """Determine which agent should handle a task based on its type."""
        task_type = task.task_type.lower()

        # Routing rules
        routing = {
            "coding": "coder",
            "code": "coder",
            "programming": "coder",
            "fix": "coder",
            "debug": "coder",
            "research": "researcher",
            "search": "researcher",
            "find": "researcher",
            "lookup": "researcher",
            "build": "builder",
            "test": "builder",
            "deploy": "builder",
            "ci": "builder",
            "design": "designer",
            "ui": "designer",
            "ux": "designer",
            "style": "designer",
        }

        for keyword, agent_name in routing.items():
            if keyword in task_type or keyword in task.description.lower():
                if agent_name in self.agents:
                    return agent_name

        # Default to first available agent or return None
        if self.agents:
            return list(self.agents.keys())[0]
        return None

    async def submit_task(self, task: Task) -> str:
        """Submit a task to the orchestrator for execution."""
        # Route to appropriate agent
        agent_name = self.route_task(task)

        if not agent_name:
            return "Error: No agent available to handle this task"

        agent = self.agents[agent_name]

        if agent.status == AgentStatus.RUNNING:
            # Queue the task
            self.task_queue.append(task)
            self._save_queue()
            return f"Task queued (agent {agent_name} is busy). Queue position: {len(self.task_queue)}"

        # Execute immediately
        result = await agent.execute_task(task)
        self.completed_tasks.append(task)

        # Notify via Telegram if configured
        await self._notify_completion(task, result)

        return result

    async def process_queue(self) -> int:
        """Process pending tasks in the queue. Returns number processed."""
        processed = 0

        while self.task_queue:
            task = self.task_queue[0]
            agent_name = self.route_task(task)

            if not agent_name:
                # Skip tasks we can't handle
                self.task_queue.pop(0)
                continue

            agent = self.agents[agent_name]

            if agent.status == AgentStatus.RUNNING:
                # Agent busy, try next task or wait
                break

            # Execute task
            self.task_queue.pop(0)
            result = await agent.execute_task(task)
            self.completed_tasks.append(task)
            processed += 1

            await self._notify_completion(task, result)

        self._save_queue()
        return processed

    async def _notify_completion(self, task: Task, result: str) -> None:
        """Send notification when task completes."""
        try:
            from tools.telegram_tool import notify_task_complete
            duration = task.completed_at - task.created_at if task.completed_at else 0
            notify_task_complete(task.description[:50], duration)
        except Exception:
            # Telegram not configured, skip notification
            pass

    def get_status(self) -> Dict[str, Any]:
        """Get current status of all agents and queue."""
        return {
            "agents": {name: agent.to_dict() for name, agent in self.agents.items()},
            "queue_length": len(self.task_queue),
            "queued_tasks": [
                {"id": t.id, "description": t.description[:50], "type": t.task_type}
                for t in self.task_queue[:10]
            ],
            "completed_today": sum(
                1 for t in self.completed_tasks
                if t.completed_at and t.completed_at > time.time() - 86400
            ),
        }

    def _save_queue(self) -> None:
        """Persist task queue to disk."""
        try:
            data = [
                {
                    "id": t.id,
                    "description": t.description,
                    "task_type": t.task_type,
                    "priority": t.priority,
                    "created_at": t.created_at,
                    "metadata": t.metadata,
                }
                for t in self.task_queue
            ]
            TASK_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            TASK_QUEUE_PATH.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _load_queue(self) -> None:
        """Load task queue from disk."""
        try:
            if TASK_QUEUE_PATH.exists():
                data = json.loads(TASK_QUEUE_PATH.read_text())
                self.task_queue = [
                    Task(
                        id=t["id"],
                        description=t["description"],
                        task_type=t["task_type"],
                        priority=t["priority"],
                        created_at=t["created_at"],
                        metadata=t.get("metadata", {}),
                    )
                    for t in data
                ]
        except Exception:
            self.task_queue = []


# Global orchestrator instance
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """Get or create the global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
