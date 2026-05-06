from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseEvaluator(ABC):
    """
    Abstract base class for all evaluators.
    """

    @abstractmethod
    def evaluate(self, language_handler, **kwargs) -> Dict[str, Any]:
        """
        Runs the evaluation.

        Args:
            language_handler: The language handler for the project being evaluated.
            **kwargs: Additional arguments needed for the specific evaluation.

        Returns:
            A dictionary containing the evaluation results (e.g., score, reason).
        """
        pass 