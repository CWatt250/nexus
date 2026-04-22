#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Test agents module imports."""
import sys
sys.path.insert(0, "/home/cwatt250/AI_Agent")

from agents.base_agent import BaseAgent, Task, AgentStatus
from agents.orchestrator import Orchestrator, get_orchestrator
from agents.coder_agent import CoderAgent
from agents.researcher_agent import ResearcherAgent
from agents.builder_agent import BuilderAgent
from agents.designer_agent import DesignerAgent

print("All agents loaded successfully!")

# Create orchestrator and register agents
orch = get_orchestrator()
orch.register_agent(CoderAgent())
orch.register_agent(ResearcherAgent())
orch.register_agent(BuilderAgent())
orch.register_agent(DesignerAgent())

print(f"Orchestrator has {len(orch.agents)} agents registered:")
for name, agent in orch.agents.items():
    print(f"  - {name}: {agent}")
