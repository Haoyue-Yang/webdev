from typing import Dict, Any

from .base_agent_strategy import BaseAgentStrategy, AgentContext


class StaticAgentStrategy(BaseAgentStrategy):
    def run(self, context: AgentContext) -> Dict[str, Any]:
        eval_results = {
            'aesthetics': {'score': 0.0, 'reason': 'Not evaluated', 'error': None},
            'functional': {'score': 0.0, 'reason': 'Not evaluated', 'error': None}
        }

        print("Running static evaluation...")
        if 'aesthetics_functional' in context.evaluators:
            combined_result = context.evaluators['aesthetics_functional'].evaluate(
                context.handler, query=context.query
            )
            eval_results['aesthetics'] = combined_result.get(
                'aesthetics', {"score": None, "reason": "Aesthetics_functional evaluation failed."}
            )
            eval_results['functional'] = combined_result.get(
                'functional', {"score": None, "reason": "Aesthetics_functional evaluation failed."}
            )
        else:
            eval_results['aesthetics'] = {"score": None, "reason": "Aesthetics_functional evaluator not available."}
            eval_results['functional'] = {"score": None, "reason": "Aesthetics_functional evaluator not available."}

        return eval_results