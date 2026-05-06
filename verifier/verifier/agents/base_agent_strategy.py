from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any


class AgentContext:
    """Context object passed to agent strategies."""

    def __init__(
        self,
        config: Dict[str, Any],
        handler: Any,
        project_path: Path,
        query: str,
        evaluators: Dict[str, Any],
    ):
        self.config = config
        self.handler = handler
        self.project_path = project_path
        self.query = query
        self.evaluators = evaluators


class BaseAgentStrategy(ABC):
    @abstractmethod
    def run(self, context: AgentContext) -> Dict[str, Any]:
        """
        Execute the agent strategy.

        Returns:
            Dict with 'aesthetics' and 'functional' keys on 0-8 scale.
        """
        pass