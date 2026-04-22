"""Coder Agent — Specialized for coding tasks."""
from __future__ import annotations

from .base_agent import BaseAgent, Task


class CoderAgent(BaseAgent):
    """Agent specialized for coding tasks, bug fixes, and code generation."""

    def __init__(self, model: str = "qwen3:4b"):
        super().__init__(
            name="Coder",
            model=model,
            system_prompt="""You are Coder, a specialized AI coding agent in the Nexus system.

Your expertise:
- Writing clean, efficient, well-documented code
- Debugging and fixing bugs
- Code review and improvement suggestions
- Implementing features from specifications

Guidelines:
- Always explain your approach before writing code
- Write simple, readable code over clever code
- Include error handling and edge cases
- Follow the existing code style in the project
- Test your code mentally before presenting it

When given a coding task:
1. Understand the requirements
2. Plan your approach
3. Write the code
4. Explain what you did and why""",
        )

    async def _run(self, task: Task) -> str:
        """Execute a coding task."""
        prompt = f"""Task: {task.description}

Please complete this coding task. Provide:
1. Your understanding of the task
2. Your approach
3. The code solution
4. Any important notes or caveats

Task details:
{task.metadata.get('details', 'No additional details provided.')}
"""
        return self.call_llm(prompt, max_tokens=2000)
