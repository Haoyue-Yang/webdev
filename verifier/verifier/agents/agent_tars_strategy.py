import asyncio
import os
import traceback
from pathlib import Path
from typing import Dict, Any, Tuple

from .base_agent_strategy import BaseAgentStrategy, AgentContext


class AgentTarsStrategy(BaseAgentStrategy):
    def run(self, context: AgentContext) -> Dict[str, Any]:
        eval_results = {
            'aesthetics': {'score': 0.0, 'reason': 'Not evaluated', 'error': None},
            'functional': {'score': 0.0, 'reason': 'Not evaluated', 'error': None}
        }

        print("Running Agent-TARS evaluation...")
        project_id = context.project_path.name
        server_url = context.handler.server_url

        try:
            agent_result, status = asyncio.run(
                self._run_browser_agent_tars(server_url, context.query, project_id, context.project_path, context.config)
            )

            if status in ("SUCCESS", "FALLBACK_SUCCESS"):
                eval_results['functional'] = {
                    "score": agent_result["functional_score"],
                    "reason": agent_result["functional_reason"],
                    "error": None,
                    "fallback_used": status == "FALLBACK_SUCCESS"
                }
                eval_results['aesthetics'] = {
                    "score": agent_result["aesthetics_score"],
                    "reason": agent_result["aesthetics_reason"],
                    "error": None,
                    "fallback_used": status == "FALLBACK_SUCCESS"
                }
            else:
                eval_results['aesthetics'] = {
                    "score": agent_result.get("aesthetics_score", None),
                    "reason": agent_result.get("aesthetics_reason", f"Agent-TARS evaluation failed: {status}"),
                    "error": agent_result.get("error", "Agent-TARS evaluation failed")
                }
                eval_results['functional'] = {
                    "score": agent_result.get("functional_score", None),
                    "reason": agent_result.get("functional_reason", f"Agent-TARS evaluation failed: {status}"),
                    "error": agent_result.get("error", "Agent-TARS evaluation failed")
                }
        except Exception as e:
            print(f"Agent-TARS evaluation failed: {e}")
            eval_results['aesthetics'] = {"score": None, "reason": f"Agent-TARS evaluation error: {e}", "error": str(e)}
            eval_results['functional'] = {"score": None, "reason": f"Agent-TARS evaluation error: {e}", "error": str(e)}

        return eval_results

    def _load_agent_tars_prompt(self, url: str, task_prompt: str, max_steps: int, config: Dict[str, Any]) -> str:
        agent_config = (config.get("evaluators") or {}).get("agent_tars") or {}
        prompt_path_str = str(agent_config.get("agent_prompt_path", "")).strip()

        if prompt_path_str:
            prompt_file = Path(prompt_path_str)
            if not prompt_file.is_absolute():
                root_dir = Path(__file__).parent.parent.parent
                prompt_file = root_dir / prompt_path_str
        else:
            prompt_file = Path(__file__).parent.parent / "prompts" / "agent_tars_scoring_v1.txt"

        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                template = f.read()
            template = template.replace('{url}', url)
            template = template.replace('{task_prompt}', task_prompt)
            template = template.replace('{max_steps}', str(max_steps))
            return template
        except FileNotFoundError:
            print(f"Warning: Agent-TARS prompt file not found at {prompt_file}. Using fallback prompt.")
            return f"Go to {url} and test the functionality described as: '{task_prompt}'. Provide a score from 0.0 to 8.0."

    async def _run_browser_agent_tars(self, url: str, task_prompt: str, project_id: str, project_dir: Path, config: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        try:
            from ..tars_agent_client import TARSAgentClient
        except ImportError:
            print(f"[{project_id}] Agent-TARS client not found")
            return {
                "functional_score": -4.0,
                "functional_reason": "Agent-TARS client not found",
                "aesthetics_score": -4.0,
                "aesthetics_reason": "Agent-TARS client not found",
                "error": "Missing Agent-TARS client"
            }, "FAILED"

        from ..fallback_evaluator import FallbackEvaluator

        agent_config = (config.get("evaluators") or {}).get("agent_tars", {}) or {}

        server_url = (
            os.environ.get("AGENT_TARS_SERVER_URL")
            or agent_config.get("server_url", "http://localhost:8890")
        )
        max_concurrent = int(agent_config.get("max_concurrent_sessions", 1))
        timeout     = int(agent_config.get("timeout", 1800))
        max_retries = int(agent_config.get("max_retries", 1))
        max_steps   = int(agent_config.get("max_steps", 40))
        max_steps_grace  = int(agent_config.get("max_steps_grace", 5))
        step_count_mode  = str(agent_config.get("step_count_mode", "tool_call"))

        client_config = {
            "use_streaming": bool(agent_config.get("use_streaming", True)),
            "log_show_timectl_pid_url": bool(agent_config.get("log_show_timectl_pid_url", False)),
            "log_show_raw_tool_result": bool(agent_config.get("log_show_raw_tool_result", False)),
            "log_show_duplicate_thought": bool(agent_config.get("log_show_duplicate_thought", False)),
            "log_suppress_repeated_args_start": bool(agent_config.get("log_suppress_repeated_args_start", True)),
            "max_steps": max_steps,
            "max_steps_grace": max_steps_grace,
            "step_count_mode": step_count_mode,
            "cdp_port": int(agent_config.get("cdp_port", 9225)),
            "close_tabs_on_finish": str(agent_config.get("close_tabs_on_finish", "true")).lower() in ("true", "1", "yes"),
            "screencast_enabled": str(agent_config.get("screencast_enabled", "false")).lower() in ("true", "1", "yes"),
            "screencast_output": str(agent_config.get("screencast_output", "")).strip(),
            "llm_log_enabled": str(agent_config.get("llm_log_enabled", "true")).lower() in ("true", "1", "yes"),
            "llm_log_dir": str(agent_config.get("llm_log_dir", "")).strip(),
        }

        print(f"[{project_id}] Using Agent-TARS Server at {server_url}")
        print(f"[{project_id}] Step limit: {max_steps} (grace={max_steps_grace}, mode={step_count_mode})")
        print(f"[{project_id}] Timeout: {timeout}s, max_retries: {max_retries}, streaming: {client_config['use_streaming']}")

        prompt = self._load_agent_tars_prompt(url, task_prompt, max_steps, config)

        try:
            async with TARSAgentClient(
                server_url=server_url,
                max_concurrent=max_concurrent,
                timeout=timeout,
                config=client_config,
            ) as client:
                result, status = await client.run_evaluation(
                    prompt=prompt,
                    project_id=project_id,
                    project_dir=project_dir,
                    max_retries=max_retries,
                )

                if status not in ("SUCCESS", "FALLBACK_SUCCESS") and project_dir:
                    print(f"[{project_id}] Agent-TARS all retries failed, attempting FallbackEvaluator...")
                    try:
                        fallback = FallbackEvaluator(agent_config)
                        fallback_result = await fallback.evaluate(
                            project_dir=project_dir,
                            project_id=project_id,
                            task_description=task_prompt,
                            max_retries=max_retries,
                        )
                        if fallback_result.get("functional_score", -1.0) != -1.0:
                            print(f"[{project_id}] FallbackEvaluator succeeded")
                            return fallback_result, "FALLBACK_SUCCESS"
                        else:
                            print(f"[{project_id}] FallbackEvaluator also failed, returning original failure")
                            return result, status
                    except Exception as fallback_error:
                        print(f"[{project_id}] FallbackEvaluator exception: {fallback_error}")
                        return result, status

                return result, status

        except Exception as e:
            print(f"[{project_id}] Agent-TARS evaluation exception: {e}")
            traceback.print_exc()
            return {
                "functional_score": -4.0,
                "functional_reason": f"Agent-TARS exception: {str(e)}",
                "aesthetics_score": -4.0,
                "aesthetics_reason": f"Agent-TARS exception: {str(e)}",
                "error": str(e),
            }, "FAILED"