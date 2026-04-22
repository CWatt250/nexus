"""Builder Agent — Specialized for builds, tests, and deployments."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .base_agent import BaseAgent, Task


class BuilderAgent(BaseAgent):
    """Agent specialized for building, testing, and deploying projects."""

    def __init__(self, model: str = "qwen3:4b"):
        super().__init__(
            name="Builder",
            model=model,
            system_prompt="""You are Builder, a specialized AI build and deployment agent in the Nexus system.

Your expertise:
- Running builds (npm, pip, make, etc.)
- Executing test suites
- CI/CD pipeline management
- Deployment to various platforms
- Environment configuration

Guidelines:
- Always check prerequisites before running commands
- Report build/test results clearly
- Handle errors gracefully and suggest fixes
- Document any environment requirements

When given a build task:
1. Identify the project type and build system
2. Check for configuration files
3. Run the appropriate commands
4. Report results and any issues""",
        )

    async def _run(self, task: Task) -> str:
        """Execute a build/test/deploy task."""
        # Analyze the task
        analysis_prompt = f"""Analyze this build task and determine:
1. What type of project is this? (Python, Node, etc.)
2. What command(s) should be run?
3. What directory should we be in?

Task: {task.description}
Project path: {task.metadata.get('project_path', 'Not specified')}
"""
        analysis = self.call_llm(analysis_prompt, max_tokens=500)

        # For safety, don't auto-execute commands
        # Instead, provide the analysis and recommended commands
        prompt = f"""Build Task: {task.description}

Analysis:
{analysis}

Please provide:
1. Recommended commands to run
2. Expected output
3. How to verify success
4. Troubleshooting tips if it fails

Project details:
{task.metadata.get('details', 'No additional details provided.')}
"""
        return self.call_llm(prompt, max_tokens=1500)
