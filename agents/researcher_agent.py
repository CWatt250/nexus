"""Researcher Agent — Specialized for research and information gathering."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "AI_Agent"))

from .base_agent import BaseAgent, Task


class ResearcherAgent(BaseAgent):
    """Agent specialized for research, web searches, and information synthesis."""

    def __init__(self, model: str = "qwen3:4b"):
        super().__init__(
            name="Researcher",
            model=model,
            system_prompt="""You are Researcher, a specialized AI research agent in the Nexus system.

Your expertise:
- Finding and synthesizing information from multiple sources
- Web searches and analysis
- Summarizing complex topics
- Fact-checking and verification

Guidelines:
- Always cite your sources when possible
- Distinguish between facts and speculation
- Present multiple perspectives when relevant
- Organize findings clearly with headers and bullet points

When given a research task:
1. Understand what information is needed
2. Use available tools (web search, browsing)
3. Synthesize findings
4. Present a clear, organized summary""",
        )

    async def _run(self, task: Task) -> str:
        """Execute a research task."""
        # Try to use Brave Search if available
        search_results = ""
        try:
            from tools.brave_search_tool import brave_search
            query = task.description[:100]
            search_results = brave_search.invoke(query)
        except Exception:
            search_results = "Web search not available."

        prompt = f"""Research Task: {task.description}

Web Search Results:
{search_results}

Based on the search results and your knowledge, please provide:
1. Key findings
2. Relevant details
3. Sources and references
4. Conclusions and recommendations

Additional context:
{task.metadata.get('details', 'No additional details provided.')}
"""
        return self.call_llm(prompt, max_tokens=2000)
