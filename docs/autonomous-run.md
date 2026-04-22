# Autonomous Operation Guide

## nonstop-agent

nonstop-agent allows Nexus to run autonomously, continuously processing tasks.

### Installation

```bash
# Option 1: pip install
pip install nonstop-agent

# Option 2: From source
git clone https://github.com/seolcoding/nonstop-agent
cd nonstop-agent
pip install -e .
```

### Configuration

Configure nonstop-agent to point at Nexus:

```bash
# Create config file
mkdir -p ~/.config/nonstop-agent
cat > ~/.config/nonstop-agent/config.yaml << 'EOF'
agent:
  workspace: ~/AI_Agent
  entry_point: nexus.py
  model: qwen3:4b
  max_iterations: 100
  sleep_between_tasks: 30

memory:
  type: chroma
  path: ~/AI_Agent/chroma

notifications:
  telegram:
    enabled: true
    on_complete: true
    on_error: true
EOF
```

### Running

```bash
# Start autonomous mode
nonstop-agent run --workspace ~/AI_Agent

# With task queue
nonstop-agent run --workspace ~/AI_Agent --queue ~/AI_Agent/memory/task-queue.json

# With monitoring dashboard
nonstop-agent run --workspace ~/AI_Agent --dashboard
```

### Alternative: Using Nexus Orchestrator

You can also use the built-in multi-agent orchestrator:

```python
from agents.orchestrator import get_orchestrator
from agents.coder_agent import CoderAgent
from agents.researcher_agent import ResearcherAgent
from agents.builder_agent import BuilderAgent
from agents.designer_agent import DesignerAgent
from agents.base_agent import Task

# Initialize
orch = get_orchestrator()
orch.register_agent(CoderAgent())
orch.register_agent(ResearcherAgent())
orch.register_agent(BuilderAgent())
orch.register_agent(DesignerAgent())

# Submit tasks
import asyncio
task = Task(description="Research best practices for...", task_type="research")
result = asyncio.run(orch.submit_task(task))

# Process queue
processed = asyncio.run(orch.process_queue())
```

### Systemd Service (Optional)

```bash
# Create service file
sudo tee /etc/systemd/system/nexus-autonomous.service << 'EOF'
[Unit]
Description=Nexus Autonomous Agent
After=network.target ollama.service

[Service]
Type=simple
User=cwatt250
WorkingDirectory=/home/cwatt250/AI_Agent
ExecStart=/home/cwatt250/AI_Agent/venv/bin/python -c "from agents.orchestrator import get_orchestrator; import asyncio; orch = get_orchestrator(); asyncio.run(orch.process_queue())"
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable nexus-autonomous
sudo systemctl start nexus-autonomous
```
