# verifier/verifier.py

import tempfile
import shutil
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
import traceback
import logging

from utils.config import load_config, _deep_update
from .languages.react_handler import ReactHandler
from .languages.html_handler import HTMLHandler
from .languages.webdev_scaffold_2_handler import WebdevScaffold2Handler
from .evaluators.running_evaluator import RunningEvaluator
from .evaluators.aesthetics_functional_evaluator import AestheticsFunctionalEvaluator
from .css_reset_check import check_css_reset_issue
from .agents import StaticAgentStrategy, AgentTarsStrategy, InteractiveVideoStrategy, AgentContext

VALID_AGENT_TYPES = ("static", "agent_tars", "interactive_video")

# Mapping from language name to handler class
LANGUAGE_HANDLER_REGISTRY = {
    "react": ReactHandler,
    "html": HTMLHandler,
    "webdev_scaffold_2": WebdevScaffold2Handler,
}


class Verifier:
    """
    The main Verifier class. Orchestrates the entire verification process.
    """

    def __init__(self, config_path: str = 'conf/config.yaml', evaluator_overrides: Dict[str, Any] = None):
        self.config = load_config(Path(config_path))
        if evaluator_overrides:
            _deep_update(self.config, evaluator_overrides)
        self.temp_dir = Path(tempfile.mkdtemp())

        # Generate unique instance ID for this verifier
        import uuid
        self._instance_id = str(uuid.uuid4())[:8]

        # Track verification completion to prevent duplicates
        self._verification_completed = False

        # Track verification start to detect instance reuse
        self._verification_started = False

        # Initialize logger for this instance
        self._logger = self._setup_logger()

        # Initialize evaluators
        evaluators_config = self.config['evaluators']

        self.evaluators = {
            "running": RunningEvaluator(),
        }

        if evaluators_config['aesthetics_functional']['enabled']:
            af_config = evaluators_config['aesthetics_functional']
            self.evaluators["aesthetics_functional"] = AestheticsFunctionalEvaluator(af_config, self.temp_dir)

        # Load return_usage config
        self._return_usage = evaluators_config.get('return_usage', True)

        # Initialize agent strategies
        self.agent_strategies = {
            "static": StaticAgentStrategy(),
            "agent_tars": AgentTarsStrategy(),
            "interactive_video": InteractiveVideoStrategy(),
        }

    def _extract_usage_from_project(self, project_path: Path) -> Dict[str, Any]:
        """
        Extract token usage statistics from the saved LLM response file.

        Args:
            project_path: Path to the project directory

        Returns:
            Dictionary with usage statistics, empty dict if not available
        """
        usage_stats = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }

        try:
            verifier_dir = project_path / ".verifier"

            # Collect all timestamped response files; fall back to legacy name if none
            response_files = sorted(verifier_dir.glob("llm_response_*.json"))
            if not response_files:
                legacy = verifier_dir / "llm_response.json"
                if legacy.exists():
                    response_files = [legacy]

            for response_file in response_files:
                with open(response_file, 'r', encoding='utf-8') as f:
                    response_data = json.load(f)
                    usage = response_data.get("usage", {})
                    if usage:
                        # Support both OpenAI (prompt_tokens) and Anthropic (input_tokens) keys
                        prompt   = usage.get("prompt_tokens",     usage.get("input_tokens",     0))
                        complete = usage.get("completion_tokens", usage.get("output_tokens",     0))
                        total    = usage.get("total_tokens",      prompt + complete)
                        usage_stats["prompt_tokens"]     += prompt
                        usage_stats["completion_tokens"] += complete
                        usage_stats["total_tokens"]      += total

            if usage_stats["total_tokens"] > 0:
                usage_stats["raw_usage"] = {
                    "prompt_tokens":     usage_stats["prompt_tokens"],
                    "completion_tokens": usage_stats["completion_tokens"],
                    "total_tokens":      usage_stats["total_tokens"],
                    "source_files":      [f.name for f in response_files],
                }
        except Exception as e:
            print(f"Warning: Could not extract usage from project: {e}")

        return usage_stats

    def __del__(self):
        """Cleanup when verifier is destroyed"""
        pass

    def cleanup(self):
        """Manual cleanup method"""
        pass
    
    def _setup_logger(self):
        """Setup logger for this verifier instance"""
        logger = logging.getLogger(f'verifier_{self._instance_id}')
        logger.setLevel(logging.DEBUG)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Only add handler if it doesn't exist
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        
        return logger
    
    def _create_verifier_directory(self, project_dir: Path) -> Path:
        """Create .verifier directory and return its path (does NOT remove existing)"""
        verifier_dir = project_dir / ".verifier"
        verifier_dir.mkdir(exist_ok=True)
        return verifier_dir
    
    def _log_verification_completion(self, project_path: Path, status: str, details: Dict[str, Any] = None, error: Exception = None, path_description: str = "unknown") -> None:
        """Log verification completion with duplicate detection"""
        # Assert no duplicate verification completion logging
        assert not self._verification_completed, f"[{self._instance_id}] Duplicate verification completion logging detected ({path_description})"
        self._verification_completed = True
        
        # Log the completion
        self._save_step_log(project_path, "verification", status, details, error)
    
    def _run_evaluations(self, handler, project_path: Path, query: str, agent_type: str) -> Dict[str, Any]:
        """Consolidated evaluation logic — delegates to agent strategies."""
        context = AgentContext(
            config=self.config,
            handler=handler,
            project_path=project_path,
            query=query,
            evaluators=self.evaluators,
        )
        strategy = self.agent_strategies[agent_type]
        eval_results = strategy.run(context)

        # Normalize scores from 0-8 scale to 0-2 scale.
        for key in ('aesthetics', 'functional'):
            raw = eval_results[key].get('score')
            eval_results[key]['score'] = (raw / 4) if raw is not None else None

        return eval_results

    def _save_step_log(self, project_dir: Path, step: str, status: str, details: Dict[str, Any] = None, error: Exception = None) -> None:
        """
        Save detailed step logs to .verifier directory for debugging.
        
        Args:
            project_dir: Project directory path
            step: Step name (e.g., 'installation', 'server_startup', 'evaluation')
            status: Status ('started', 'completed', 'failed', 'timeout')
            details: Additional details to log
            error: Exception object if step failed
        """
        try:
            verifier_dir = self._create_verifier_directory(project_dir)
            
            # Create step log entry
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "step": step,
                "status": status,
                "instance_id": self._instance_id,
                "details": details or {},
                "error": None
            }
            
            # Add error information if present
            if error:
                log_entry["error"] = {
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc()
                }
            
            # Save to step-specific log file
            log_file = verifier_dir / f"{step}_log.json"
            
            # Load existing logs
            existing_logs = []
            if log_file.exists():
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        existing_logs = json.load(f)
                except Exception as read_error:
                    self._logger.warning(f"Could not read existing log file {log_file}: {read_error}")
                    existing_logs = []
            
            # Append new log entry
            existing_logs.append(log_entry)
            
            # Save updated logs
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(existing_logs, f, indent=2, ensure_ascii=False)
            
            # Also save to combined log
            combined_log_file = verifier_dir / "combined_log.json"
            combined_logs = []
            if combined_log_file.exists():
                try:
                    with open(combined_log_file, 'r', encoding='utf-8') as f:
                        combined_logs = json.load(f)
                except Exception as read_error:
                    self._logger.warning(f"Could not read combined log file: {read_error}")
                    combined_logs = []
            
            combined_logs.append(log_entry)
            
            with open(combined_log_file, 'w', encoding='utf-8') as f:
                json.dump(combined_logs, f, indent=2, ensure_ascii=False)
            
            self._logger.info(f"Logged {step} {status} to {log_file}")
            
        except Exception as log_error:
            self._logger.error(f"Failed to save step log for {step}: {log_error}")
            print(f"Warning: Could not save step log for {step}: {log_error}")
    
    def _save_handler_output(self, project_dir: Path, step: str, result: Dict[str, Any]) -> None:
        """
        Save handler output (stdout, stderr, etc.) to .verifier directory.
        
        Args:
            project_dir: Project directory path
            step: Step name ('installation', 'startup', etc.)
            result: Handler result dictionary
        """
        try:
            verifier_dir = self._create_verifier_directory(project_dir)
            
            # Save detailed output
            output_file = verifier_dir / f"{step}_output.json"
            output_data = {
                "timestamp": datetime.now().isoformat(),
                "step": step,
                "result": result,
                "instance_id": self._instance_id
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            
            # Save stdout and stderr as separate text files for easy reading
            if result.get('stdout'):
                stdout_file = verifier_dir / f"{step}_stdout.txt"
                with open(stdout_file, 'w', encoding='utf-8') as f:
                    f.write(result['stdout'])
            
            if result.get('stderr'):
                stderr_file = verifier_dir / f"{step}_stderr.txt"
                with open(stderr_file, 'w', encoding='utf-8') as f:
                    f.write(result['stderr'])
            
            self._logger.info(f"Saved {step} output to {output_file}")
            
        except Exception as save_error:
            self._logger.error(f"Failed to save handler output for {step}: {save_error}")
            print(f"Warning: Could not save handler output for {step}: {save_error}")
    
    def _create_verification_summary(self, project_dir: Path, final_results: Dict[str, Any], language: str, query: str, agent_type: str) -> None:
        """
        Create a human-readable summary file in .verifier directory for easy debugging.
        
        Args:
            project_dir: Project directory path
            final_results: Final verification results
            language: Programming language
            query: User query
            agent_type: Agent type used
        """
        try:
            verifier_dir = self._create_verifier_directory(project_dir)
            
            # Extract evaluation scores for easy access
            evaluations = final_results.get('evaluations', {})
            
            summary = {
                "verification_summary": {
                    "timestamp": datetime.now().isoformat(),
                    "project_path": str(project_dir),
                    "language": language,
                    "query": query,
                    "agent_type": agent_type,
                    "instance_id": self._instance_id,
                    "overall_status": "success" if not final_results.get('error') else "failed",
                    "error": final_results.get('error'),
                    "scores": {
                        "installation": evaluations.get('installation', {}).get('score') or 0.0,
                        "running": evaluations.get('running', {}).get('score') or 0.0,
                        "aesthetics": evaluations.get('aesthetics', {}).get('score') or 0.0,
                        "functional": evaluations.get('functional', {}).get('score') or 0.0
                    },
                    "evaluation_details": evaluations
                }
            }
            
            # Save JSON summary
            summary_file = verifier_dir / "verification_summary.json"
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            
            # Create human-readable text summary
            text_summary = f"""Verification Summary
===================
Timestamp: {datetime.now().isoformat()}
Project: {project_dir}
Language: {language}
Query: {query}
Agent Type: {agent_type}
Instance ID: {self._instance_id}

Overall Status: {summary['verification_summary']['overall_status'].upper()}

Scores:
- Installation: {summary['verification_summary']['scores']['installation']:.1f}
- Running: {summary['verification_summary']['scores']['running']:.1f}
- Aesthetics: {summary['verification_summary']['scores']['aesthetics']:.1f}
- Functional: {summary['verification_summary']['scores']['functional']:.1f}

"""
            
            if final_results.get('error'):
                text_summary += f"Error: {final_results['error']}\n\n"
            
            text_summary += "Evaluation Details:\n"
            for eval_type, eval_result in evaluations.items():
                text_summary += f"\n{eval_type.upper()}:\n"
                text_summary += f"  Score: {(eval_result.get('score') or 0.0):.1f}\n"
                text_summary += f"  Reason: {eval_result.get('reason', 'N/A')}\n"
                if eval_result.get('error'):
                    text_summary += f"  Error: {eval_result['error']}\n"
            
            text_summary += "\nFor detailed logs, check the individual *_log.json files in this directory.\n"
            
            # Save text summary
            text_summary_file = verifier_dir / "verification_summary.txt"
            with open(text_summary_file, 'w', encoding='utf-8') as f:
                f.write(text_summary)
            
            self._logger.info(f"Created verification summary: {summary_file}")
            print(f"Verification summary saved to: {summary_file}")
            
        except Exception as summary_error:
            self._logger.error(f"Failed to create verification summary: {summary_error}")
            print(f"Warning: Could not create verification summary: {summary_error}")

    def clear_verifier_directory(self, project_dir: Path) -> None:
        """
        Clear the .verifier directory in the project directory to ensure a clean state.
        
        Args:
            project_dir: Path to the project directory
        """
        verifier_dir = project_dir / ".verifier"
        if verifier_dir.exists() and verifier_dir.is_dir():
            print(f"Clearing .verifier directory: {verifier_dir}")
            try:
                shutil.rmtree(verifier_dir)
                print(f"Successfully cleared .verifier directory")
            except Exception as e:
                print(f"Warning: Could not clear .verifier directory: {e}")

    def get_language_handler(self, language: str, project_dir: Path):
        """
        Gets the appropriate language handler for the given language.
        """
        if language not in LANGUAGE_HANDLER_REGISTRY:
            raise ValueError(f"Unsupported language: '{language}'. Supported languages: {list(LANGUAGE_HANDLER_REGISTRY.keys())}")
        
        handler_class = LANGUAGE_HANDLER_REGISTRY[language]
        lang_config = self.config['languages'][language]
        # Add language name to config for the handler's reference
        lang_config['language_name'] = language
        
        return handler_class(project_dir, lang_config, self.config)

    def test_project(self, project_dir: str, language: str, query: str, agent_type: str = "static", keep_existing_project: bool = False) -> Dict[str, Any]:
        """
        Tests a project by running a sequence of evaluations.

        Args:
            project_dir: Path to the project directory
            language: Programming language of the project
            query: User query describing the project requirements
            agent_type: Type of agent evaluation ("static", "agent_tars", or "interactive_video")
            keep_existing_project: If False (default), remove existing project directory to ensure clean state.
                                 If True, preserve existing project files and only clear .verifier directory.

        Returns:
            Dict with evaluation results. If `evaluators.return_usage` is True in config,
            includes 'usage' key with token usage statistics.
        """
        project_path = Path(project_dir).resolve()
        print(f"--- Starting verification for project: {project_path} ---")
        
        # Clear .verifier directory before starting (only if keeping existing project)
        # Note: Service layer (app.py) handles project directory removal, 
        # so this only handles .verifier cleanup when preserving project files
        if keep_existing_project:
            self.clear_verifier_directory(project_path)
        
        # Create .verifier directory and log the start of verification
        self._save_step_log(project_path, "verification", "started", {
            "project_path": str(project_path),
            "language": language,
            "query": query,
            "agent_type": agent_type,
            "keep_existing_project": keep_existing_project
        })
        
        handler = self.get_language_handler(language, project_path)
        final_results = {}
        
        # Initialize evaluation results with defaults
        eval_results = {
            'installation': {'score': 0.0, 'reason': 'Not evaluated', 'error': None},
            'running': {'score': 0.0, 'reason': 'Not evaluated', 'error': None},
            'aesthetics': {'score': 0.0, 'reason': 'Not evaluated', 'error': None},
            'functional': {'score': 0.0, 'reason': 'Not evaluated', 'error': None}
        }

        try:
            # 1. Install dependencies
            self._save_step_log(project_path, "installation", "started", {
                "language": language,
                "handler_type": type(handler).__name__
            })
            
            install_result = handler.install()
            
            # Save installation output to .verifier directory
            self._save_handler_output(project_path, "installation", install_result)
            
            if not install_result["success"]:
                self._save_step_log(project_path, "installation", "failed", install_result)
                
                eval_results['installation'] = {
                    'score': 0.0, 
                    'reason': install_result.get('reason', 'Installation failed'),
                    'error': install_result.get('stderr', install_result.get('error', 'Unknown error')),
                    'details': install_result
                }
                final_results["details"] = install_result
                final_results["evaluations"] = eval_results
                
                self._log_verification_completion(
                    project_path, "failed", 
                    {"reason": "Installation failed", "final_results": final_results},
                    path_description="installation failure path"
                )
                
                return final_results # Exit early with all scores
            else:
                self._save_step_log(project_path, "installation", "completed", install_result)
                
                # For successful installation, determine if it was skipped or performed
                if language in ['html']:
                    # For languages that don't require installation
                    eval_results['installation'] = {
                        'score': 1.0, 
                        'reason': 'No installation required for this project type',
                        'error': None,
                        'skipped': True
                    }
                else:
                    # For languages that performed actual installation
                    eval_results['installation'] = {
                        'score': 1.0, 
                        'reason': 'Dependencies installed successfully',
                        'error': None,
                        'skipped': False
                    }

            # 2. Start the dev server
            self._save_step_log(project_path, "server_startup", "started", {
                "handler_type": type(handler).__name__,
                "language": language
            })
            
            start_result = handler.start()
            
            # Save server startup output to .verifier directory
            self._save_handler_output(project_path, "server_startup", start_result)
            
            if not start_result["success"]:
                self._save_step_log(project_path, "server_startup", "failed", start_result)
                
                eval_results['running'] = {
                    'score': 0.0,
                    'reason': start_result.get('reason', 'Failed to start server'),
                    'error': start_result.get('stderr', start_result.get('error', 'Unknown error')),
                    'details': start_result
                }
                final_results["details"] = start_result
                final_results["evaluations"] = eval_results
                
                self._log_verification_completion(
                    project_path, "failed",
                    {"reason": "Server startup failed", "final_results": final_results},
                    path_description="server startup failure path"
                )
                
                return final_results # Exit early with all scores

            else:
                self._save_step_log(project_path, "server_startup", "completed", {
                    "server_url": getattr(handler, 'server_url', 'Unknown'),
                    "start_result": start_result
                })

            # 3. Run evaluations
            self._save_step_log(project_path, "evaluation", "started", {
                "agent_type": agent_type
            })
            
            eval_results['running'] = self.evaluators['running'].evaluate(handler)
            
            # Log running evaluation result
            self._save_step_log(project_path, "evaluation_running", "completed", {
                "running_result": eval_results['running']
            })
            
            if eval_results['running']['score'] == 1.0:
                # Only run other evaluations if the server is up
                eval_results.update(self._run_evaluations(handler, project_path, query, agent_type))
            else:
                self._save_step_log(project_path, "evaluation", "skipped", {
                    "reason": "Server not running",
                    "running_score": eval_results['running']['score']
                })
                
                eval_results['aesthetics'] = {"score": 0.0, "reason": "Server not running."}
                eval_results['functional'] = {"score": 0.0, "reason": "Server not running."}
                
            final_results['evaluations'] = eval_results

            # Set top-level error only for server-side issues (LLM call failures)
            aesthetics_err = eval_results.get('aesthetics', {}).get('error')
            functional_err = eval_results.get('functional', {}).get('error')
            if aesthetics_err and functional_err:
                final_results["error"] = "LLM evaluation failed after all retries."

            # Log successful completion
            self._save_step_log(project_path, "evaluation", "completed", {
                "eval_results": eval_results,
                "agent_type": agent_type
            })
            
            self._log_verification_completion(
                project_path, "completed",
                {"final_results": final_results},
                path_description="successful completion path"
            )
            
            # Create a summary file for easy debugging
            self._create_verification_summary(project_path, final_results, language, query, agent_type)
            
            # Cleanup after successful evaluations
            print("--- Cleaning up ---")
            handler.cleanup()

        except Exception as e:
            # Only log completion if we haven't already logged it
            if not self._verification_completed:
                self._log_verification_completion(
                    project_path, "failed", 
                    error=e,
                    path_description="exception handling path"
                )
            else:
                # Already completed - just log the error without completion
                print(f"[{self._instance_id}] Error after verification completion: {e}")
                self._save_step_log(project_path, "post_completion_error", "failed", error=e)
            
            print(f"An unexpected error occurred during verification: {e}")
            final_results['error'] = f"An unexpected error occurred: {e}"
            # Ensure evaluations are returned even on exception
            final_results['evaluations'] = eval_results
            # Cleanup in case of error
            print("--- Cleaning up after error ---")
            handler.cleanup()

        # Add usage statistics if configured
        if self._return_usage:
            usage = self._extract_usage_from_project(project_path)
            final_results['usage'] = usage

        print(f"--- Verification finished for project: {project_path} ---")
        return final_results

    def test_scaffold_project(self, project_dir: str, response: str, query: str, agent_type: str = "static") -> Dict[str, Any]:
        """
        Tests a scaffold project by parsing tool_calls from response and executing them.

        This method is specifically for webdev_scaffold_2 language type which takes
        a response containing <tool_call>...</tool_call> blocks.

        Args:
            project_dir: Path to the project directory
            response: LLM response containing tool_call blocks
            query: User query describing the project requirements
            agent_type: Type of agent evaluation ("static", "interactive", or "mixed")

        Returns:
            Dict with evaluation results
        """
        # Create a modified lang_config that includes the response
        language = "webdev_scaffold_2"
        lang_config = self.config['languages'].get(language, {}).copy()
        lang_config['response'] = response
        lang_config['language_name'] = language

        # Temporarily update config for this handler
        self.config['languages'][language] = lang_config

        # Use the standard test_project method
        return self.test_project(
            project_dir=project_dir,
            language=language,
            query=query,
            agent_type=agent_type
        )
