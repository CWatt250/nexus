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
        """Execute a research task and return a markdown report with cited
        sources (Phase 16.3). Uses Brave web + news search, RAG memory, and
        browser_tool for the most-promising hit."""
        import re

        query = task.description[:200]

        web_results = "_(Brave search unavailable)_"
        news_results = ""
        rag_results = "_(RAG search unavailable)_"
        try:
            from tools.brave_search_tool import brave_search, brave_search_news
            web_results = brave_search.invoke({"query": query, "count": 5})
            try:
                news_results = brave_search_news.invoke({"query": query, "count": 3})
            except Exception:
                news_results = ""
        except Exception:
            pass
        try:
            from tools.rag_tool import memory_search
            rag_results = memory_search.invoke({"query_text": query, "k": 4})
        except Exception:
            pass

        # Pull the first URL from web results and grab its text body for
        # deeper context. Cap to 2KB so we don't blow the prompt budget.
        deep_dive = ""
        urls = re.findall(r"https?://\S+", web_results or "")
        urls = [u.rstrip(").,;\"' ") for u in urls]
        if urls:
            primary = urls[0]
            try:
                from tools.browser_tool import browser_tool
                body = browser_tool.invoke({"url": primary})
                if isinstance(body, str):
                    deep_dive = f"\n\n## Primary source ({primary})\n{body[:2000]}"
            except Exception:
                deep_dive = ""

        report_prompt = (
            f"You are Researcher in the Nexus system. Write a concise markdown "
            f"research report on the topic below. Use the supplied web hits, "
            f"news snippets, RAG context, and primary-source excerpt. The report "
            f"MUST follow this layout:\n\n"
            f"# Research: <topic>\n"
            f"## Summary\n(3-5 bullet key findings)\n"
            f"## Details\n(1-2 short paragraphs)\n"
            f"## Sources\n(numbered list of URLs actually used)\n\n"
            f"Topic: {task.description}\n\n"
            f"## WEB\n{web_results}\n\n"
            f"## NEWS\n{news_results or '_(none)_'}\n\n"
            f"## MEMORY\n{rag_results}{deep_dive}\n\n"
            f"Write the report now. Cite by linking the URLs you actually pulled "
            f"from above. Do not invent sources."
        )
        return self.call_llm(report_prompt, max_tokens=2000)
