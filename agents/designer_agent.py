"""Designer Agent — Specialized for UI/UX and design tasks."""
from __future__ import annotations

from .base_agent import BaseAgent, Task


class DesignerAgent(BaseAgent):
    """Agent specialized for UI/UX design, styling, and visual design."""

    def __init__(self, model: str = "qwen3:4b"):
        super().__init__(
            name="Designer",
            model=model,
            system_prompt="""You are Designer, a specialized AI design agent in the Nexus system.

Your expertise:
- UI/UX design principles
- CSS and styling
- Color theory and typography
- Responsive design
- Accessibility (WCAG guidelines)
- Component design systems

Guidelines:
- Prioritize usability and accessibility
- Follow consistent design patterns
- Consider mobile-first responsive design
- Use semantic HTML structure
- Document design decisions

When given a design task:
1. Understand the context and requirements
2. Consider user experience
3. Propose design solutions
4. Provide implementation details (CSS, HTML)""",
        )

    async def _run(self, task: Task) -> str:
        """Execute a design task."""
        prompt = f"""Design Task: {task.description}

Please provide:
1. Design approach and rationale
2. Visual design recommendations (colors, typography, spacing)
3. Implementation code (HTML/CSS)
4. Accessibility considerations
5. Responsive design notes

Context:
{task.metadata.get('details', 'No additional context provided.')}
{task.metadata.get('brand_colors', '')}
"""
        return self.call_llm(prompt, max_tokens=2000)


# Connected to Nexus Design Studio on port 11436
DESIGN_STUDIO_URL = "http://localhost:11436"
