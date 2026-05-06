from typing import Dict, Any
from .base_evaluator import BaseEvaluator
from ..utils.process_utils import run_command

class RunningEvaluator(BaseEvaluator):
    """
    Evaluates if the project's dev server is running and responsive.
    """

    def evaluate(self, language_handler, **kwargs) -> Dict[str, Any]:
        """
        Executes the 'test_running_script' to check if the server is alive.
        """
        print("Running 'Running' evaluation...")
        
        config = language_handler.config
        test_script = config.get('test_running_script')

        if not test_script:
            return {"score": 0.0, "reason": "No 'test_running_script' defined for this language.", "error": "Configuration missing."}

        # Format the script with the actual port (if applicable)
        if hasattr(language_handler, 'port') and language_handler.port:
            test_script = test_script.format(port=language_handler.port)
        else:
            # For languages without ports (like Python), use the script as-is
            test_script = test_script

        return_code, stdout, stderr = run_command(test_script, cwd=str(language_handler.project_dir))

        if return_code == 0:
            print("Running evaluation successful.")
            return {"score": 1.0, "reason": "Server is running and responsive.", "error": None}
        else:
            print(f"Running evaluation failed. Error: {stderr}")
            return {"score": 0.0, "reason": "Server failed to respond.", "error": stderr} 