from .base_agent_strategy import BaseAgentStrategy, AgentContext
from .static_strategy import StaticAgentStrategy
from .agent_tars_strategy import AgentTarsStrategy
from .interactive_video_strategy import InteractiveVideoStrategy

AGENT_STRATEGY_REGISTRY = {
    "static": StaticAgentStrategy,
    "agent_tars": AgentTarsStrategy,
    "interactive_video": InteractiveVideoStrategy,
}