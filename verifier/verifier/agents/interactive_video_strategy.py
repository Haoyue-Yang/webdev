import asyncio
import json
import os
import re
import time
import traceback
import base64
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

from utils.api_calls import call_llm_api
from .base_agent_strategy import BaseAgentStrategy, AgentContext
from ..css_reset_check import check_css_reset_issue


class InteractiveVideoStrategy(BaseAgentStrategy):
    def run(self, context: AgentContext) -> Dict[str, Any]:
        eval_results = {
            'aesthetics': {'score': 0.0, 'reason': 'Not evaluated', 'error': None},
            'functional': {'score': 0.0, 'reason': 'Not evaluated', 'error': None}
        }

        print("Running interactive_video evaluation (static VLM + Agent-TARS interaction + VLM problem detection + score adjustment)...")

        config = context.config
        handler = context.handler
        project_path = context.project_path
        query = context.query
        evaluators = context.evaluators

        # Step 0: Run STATIC evaluation with retry on invalid responses
        print(f"\n[Interactive Video - Step 0] Running static evaluation for baseline scores...")

        _agent_tars_cfg = config.get('evaluators', {}).get('agent_tars', {})
        _skip_initial_static = str(_agent_tars_cfg.get('iv_skip_initial_static', 'false')).lower() in ('true', '1', 'yes')

        if _skip_initial_static:
            _skip_reason = (
                "Static evaluation skipped (iv_skip_initial_static=true). "
                "Please discover all problems autonomously from the video/screenshots "
                "and score directly according to the evaluation rubric."
            )
            static_aesthetics = {"score": None, "reason": _skip_reason}
            static_functional = {"score": None, "reason": _skip_reason}
            print(f"  [Step 0 skipped] iv_skip_initial_static=true, proceeding directly to TARS interaction.")
        else:
            max_static_retries = int(_agent_tars_cfg.get('iv_static_max_retries', 3))
            static_aesthetics = None
            static_functional = None
            for static_attempt in range(1, max_static_retries + 1):
                try:
                    print(f"  Static evaluation attempt {static_attempt}/{max_static_retries}...")

                    if 'aesthetics_functional' in evaluators:
                        static_combined_result = evaluators['aesthetics_functional'].evaluate(
                            handler, query=query
                        )
                        _cand_aes = static_combined_result.get('aesthetics', {"score": 0.0, "reason": "Static evaluation failed."})
                        _cand_func = static_combined_result.get('functional', {"score": 0.0, "reason": "Static evaluation failed."})

                        if self._is_static_result_valid(_cand_aes, _cand_func):
                            static_aesthetics = _cand_aes
                            static_functional = _cand_func
                            print(f"  ✅ Static evaluation succeeded on attempt {static_attempt}")
                            break
                        else:
                            print(f"  ⚠️ Static attempt {static_attempt} returned invalid/error result, retrying...")
                            if static_attempt < max_static_retries:
                                time.sleep(3)
                    else:
                        static_aesthetics = {"score": None, "reason": "Aesthetics_functional evaluator not available."}
                        static_functional = {"score": None, "reason": "Aesthetics_functional evaluator not available."}
                        break

                except Exception as _se:
                    print(f"  Static evaluation attempt {static_attempt} failed: {_se}")
                    if static_attempt < max_static_retries:
                        time.sleep(3)
                    else:
                        static_aesthetics = {"score": None, "reason": f"Static evaluation failed after {max_static_retries} attempts. Error: {_se}"}
                        static_functional = {"score": None, "reason": f"Static evaluation failed after {max_static_retries} attempts. Error: {_se}"}

            if static_aesthetics is None:
                static_aesthetics = {"score": None, "reason": "Static evaluation failed - all retries exhausted"}
            if static_functional is None:
                static_functional = {"score": None, "reason": "Static evaluation failed - all retries exhausted"}

        print(f"  Static scores: aesthetics={static_aesthetics['score']}/8  functional={static_functional['score']}/8")

        static_scores = {
            'aesthetics_score': static_aesthetics['score'],
            'aesthetics_reason': static_aesthetics['reason'],
            'functional_score': static_functional['score'],
            'functional_reason': static_functional['reason'],
        }

        project_id = project_path.name
        server_url = handler.server_url

        # Step 0.5: Planner
        print(f"\n[{project_id}] Step 0.5: Running planner over project source...")
        planner_plan = self._run_interactive_planner(query, project_path, project_id, config)
        if planner_plan:
            n_core = len(planner_plan.get('core_features', []) or [])
            n_secondary = len(planner_plan.get('secondary_features', []) or [])
            print(f"  ✅ Planner produced plan: {n_core} core + {n_secondary} secondary features")
        else:
            print(f"  ⚠️ Planner unavailable/failed — TARS will run without plan")

        try:
            # Step 1: Agent-TARS interaction-only (no scoring)
            print(f"\n[{project_id}] Step 1: Agent-TARS interaction (interaction-only mode)...")
            interaction_summary, tars_status, video_path, screenshots_dir = asyncio.run(
                self._run_browser_agent_tars_interaction(
                    server_url, query, project_id, project_path, config,
                    planner_plan=planner_plan,
                )
            )

            if tars_status not in ("SUCCESS", "PARTIAL"):
                warning_message = f"[Agent-TARS interaction failed ({tars_status}), falling back to static scores.]"
                print(f"[{project_id}]{warning_message}")
                eval_results['aesthetics'] = static_aesthetics
                eval_results['functional'] = static_functional
            else:
                # Step 2: VLM problem detection using TARS screenshots + interaction report
                print(f"\n[{project_id}] Step 2: VLM problem detection from TARS interaction artifacts...")

                verifier_dir = project_path / ".verifier"

                initial_screenshot_path = None
                if verifier_dir.exists():
                    _shots = sorted(verifier_dir.glob("screenshot_*.png"))
                    if _shots:
                        initial_screenshot_path = _shots[-1]
                        print(f"  Found initial static screenshot: {initial_screenshot_path.name}")

                try:
                    detected_problems = asyncio.run(
                        self._evaluate_tars_interaction_with_vlm(
                            config=config,
                            query=query,
                            interaction_summary=interaction_summary,
                            static_scores=static_scores,
                            screenshots_dir=screenshots_dir,
                            initial_screenshot=initial_screenshot_path,
                            project_dir=project_path,
                            video_path=video_path,
                        )
                    )

                    if detected_problems.get("_skip_vlm"):
                        print(f"[{project_id}] VLM problem detection skipped (no visual media) — scores set to None.")
                        eval_results['aesthetics'] = {"score": None, "reason": "VLM problem detection skipped: no visual media available.", "error": "skip_vlm"}
                        eval_results['functional'] = {"score": None, "reason": "VLM problem detection skipped: no visual media available.", "error": "skip_vlm"}
                    elif detected_problems.get("_detection_failed"):
                        print(f"[{project_id}] VLM problem detection failed after all retries — scores set to None.")
                        eval_results['aesthetics'] = {"score": None, "reason": "VLM problem detection failed after all retries.", "error": "detection_failed"}
                        eval_results['functional'] = {"score": None, "reason": "VLM problem detection failed after all retries.", "error": "detection_failed"}
                        problems_file = verifier_dir / "detected_problems.json"
                        with open(problems_file, 'w', encoding='utf-8') as _f:
                            json.dump(detected_problems, _f, indent=2, ensure_ascii=False)
                    else:
                        problems_file = verifier_dir / "detected_problems.json"
                        with open(problems_file, 'w', encoding='utf-8') as _f:
                            json.dump(detected_problems, _f, indent=2, ensure_ascii=False)
                        print(f"[{project_id}] Detected problems saved to {problems_file.name}")

                        # Step 3: Adjust scores based on detected problems
                        print(f"\n[{project_id}] Step 3: Adjusting scores based on detected problems...")

                        adjusted_scores = asyncio.run(
                            self._adjust_scores_based_on_problems(
                                config=config,
                                query=query,
                                static_scores=static_scores,
                                detected_problems=detected_problems,
                            )
                        )

                        adjusted_scores_file = verifier_dir / "adjusted_scores.json"
                        with open(adjusted_scores_file, 'w', encoding='utf-8') as _f:
                            json.dump({
                                "static_scores": static_scores,
                                "detected_problems": detected_problems,
                                "adjusted_scores": adjusted_scores,
                            }, _f, indent=2, ensure_ascii=False)
                        print(f"[{project_id}] Adjusted scores saved to {adjusted_scores_file.name}")

                        adj_func = adjusted_scores.get("adjusted_functional_score")
                        adj_aes  = adjusted_scores.get("adjusted_aesthetics_score")

                        # Re-apply CSS reset cap if enabled
                        css_reset_enabled = config.get('evaluators', {}).get('aesthetics_functional', {}).get('css_reset_check_enabled', True)
                        if css_reset_enabled and adj_aes is not None and static_aesthetics.get('score') is not None:
                            if static_aesthetics['score'] <= 1.0 and check_css_reset_issue(project_path):
                                if adj_aes > 1.0:
                                    print(f"[CSS Reset Check] Re-capping adjusted aesthetics score: {adj_aes:.1f} -> 1.0")
                                    adj_aes = 1.0
                                    adjusted_scores['adjusted_aesthetics_score'] = 1.0
                                    adjusted_scores['aesthetics_reason'] = (
                                        f"[CSS Reset Issue] Aesthetics score re-capped to 1.0 after score adjustment. "
                                        f"Original adjusted reason: {adjusted_scores.get('aesthetics_reason', '')}"
                                    )

                        eval_results['functional'] = {
                            "score": adj_func,
                            "reason": adjusted_scores.get('functional_reason', 'No adjustment reason provided'),
                            "error": "adjustment_failed" if adj_func is None else None,
                            "static_score": static_functional['score'],
                            "static_reason": static_functional['reason'],
                            "adjustment": (adj_func - static_functional['score']) if (adj_func is not None and static_functional['score'] is not None) else None,
                            "adjustment_summary": adjusted_scores.get('adjustment_summary', 'N/A'),
                        }
                        eval_results['aesthetics'] = {
                            "score": adj_aes,
                            "reason": adjusted_scores.get('aesthetics_reason', 'No adjustment reason provided'),
                            "error": "adjustment_failed" if adj_aes is None else None,
                            "static_score": static_aesthetics['score'],
                            "static_reason": static_aesthetics['reason'],
                            "adjustment": (adj_aes - static_aesthetics['score']) if (adj_aes is not None and static_aesthetics['score'] is not None) else None,
                            "adjustment_summary": adjusted_scores.get('adjustment_summary', 'N/A'),
                        }

                        if adj_func is not None and adj_aes is not None:
                            print(f"\n[{project_id}] Final adjusted scores:")
                            print(f"  Aesthetics: {static_aesthetics['score']}/8 → {adj_aes}/8 ({eval_results['aesthetics']['adjustment']:+.1f})")
                            print(f"  Functional: {static_functional['score']}/8 → {adj_func}/8 ({eval_results['functional']['adjustment']:+.1f})")
                        else:
                            print(f"[{project_id}] Score adjustment failed after all retries — scores set to None.")

                except Exception as _vlm_err:
                    print(f"[{project_id}] VLM problem detection/adjustment raised exception: {_vlm_err} — scores set to None.")
                    traceback.print_exc()
                    eval_results['aesthetics'] = {"score": None, "reason": f"VLM evaluation error: {_vlm_err}", "error": str(_vlm_err)}
                    eval_results['functional'] = {"score": None, "reason": f"VLM evaluation error: {_vlm_err}", "error": str(_vlm_err)}

        except Exception as _e:
            print(f"Interactive_video evaluation failed: {_e}")
            traceback.print_exc()
            eval_results['aesthetics'] = {
                "score": static_aesthetics.get('score'),
                "reason": f"Interactive_video evaluation error: {_e}. Using static score.",
                "error": str(_e),
            }
            eval_results['functional'] = {
                "score": static_functional.get('score'),
                "reason": f"Interactive_video evaluation error: {_e}. Using static score.",
                "error": str(_e),
            }

        return eval_results

    # ──────────────────────────────────────────────────────────────────────────
    # interactive_video helper methods
    # ──────────────────────────────────────────────────────────────────────────

    def _is_static_result_valid(self, aesthetics: Dict, functional: Dict) -> bool:
        """Check if static evaluation result is valid (no known error indicators)."""
        _ERROR_INDICATORS = [
            "Failed to take screenshot",
            "LLM returned empty response",
            "Server failed to respond",
        ]
        for result in (aesthetics, functional):
            reason = (result or {}).get("reason", "") or ""
            for indicator in _ERROR_INDICATORS:
                if indicator in reason:
                    return False
        return True

    def _load_tars_interaction_prompt(
        self,
        url: str,
        task_prompt: str,
        max_steps: int,
        planner_plan: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Load the TARS interaction-only prompt template."""
        prompt_file = Path(__file__).parent.parent / "prompts" / "tars_interact_only.txt"

        if planner_plan:
            planner_block = "```json\n" + json.dumps(planner_plan, ensure_ascii=False, indent=2) + "\n```"
        else:
            planner_block = "(no plan available)"

        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                template = f.read()
            template = template.replace('{url}', url)
            template = template.replace('{task_prompt}', task_prompt)
            template = template.replace('{max_steps}', str(max_steps))
            template = template.replace('{planner_plan}', planner_block)
            return template
        except FileNotFoundError:
            print(f"Warning: TARS interaction prompt not found at {prompt_file}. Using fallback.")
            return (
                f"Go to {url} and interact with the application described as: '{task_prompt}'. "
                "Document your interactions in JSON format with keys: 'actions_performed' (list of strings), "
                "'console_errors' (list of strings), 'overall_observation' (string)."
            )

    def _bundle_project_code(self, project_path: Path, max_chars: int) -> str:
        """Read project source into a single string with file markers. Truncates at max_chars."""
        ignore_dirs = {'node_modules', '.git', 'dist', 'build', '.verifier', '.next', '.cache'}
        ignore_files = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml'}
        text_exts = {'.js', '.jsx', '.ts', '.tsx', '.html', '.htm', '.css', '.scss', '.sass',
                     '.json', '.md', '.py', '.svg', '.vue', '.txt', '.yml', '.yaml'}

        parts: list = []
        total = 0
        truncated = False
        for file_path in sorted(project_path.rglob('*')):
            if not file_path.is_file():
                continue
            if any(part in ignore_dirs for part in file_path.parts):
                continue
            if file_path.name in ignore_files:
                continue
            if file_path.suffix.lower() not in text_exts:
                continue
            try:
                content = file_path.read_text(errors='ignore')
            except Exception:
                continue
            header = f"--- FILE: {file_path.relative_to(project_path)} ---\n\n"
            remaining = max_chars - total - len(header)
            if remaining <= 0:
                truncated = True
                break
            if len(content) > remaining:
                content = content[:remaining] + "\n\n[... truncated ...]"
                truncated = True
                parts.append(header + content)
                total += len(header) + len(content)
                break
            parts.append(header + content)
            total += len(header) + len(content)

        bundled = "\n\n".join(parts)
        if truncated:
            bundled += "\n\n[... additional files omitted: source exceeded planner char budget ...]"
        return bundled

    def _run_interactive_planner(
        self, query: str, project_path: Path, project_id: str, config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Step 0.5 for interactive_video: read project source, ask LLM for a testing plan.
        Returns parsed plan dict, or None if disabled/failed/empty."""
        agent_cfg = config.get('evaluators', {}).get('agent_tars', {})
        if not agent_cfg.get('iv_planner_enabled', True):
            print(f"[{project_id}] Planner disabled via iv_planner_enabled=false")
            return None

        max_retries = int(agent_cfg.get('iv_planner_max_retries', 2))
        max_code_chars = int(agent_cfg.get('iv_planner_max_code_chars', 200000))

        code_files = self._bundle_project_code(project_path, max_code_chars)
        if not code_files.strip():
            print(f"[{project_id}] Planner: no readable source files found, skipping")
            return None

        prompt_file = Path(__file__).parent.parent / "prompts" / "tars_planner.txt"
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            print(f"[{project_id}] Planner prompt not found at {prompt_file}, skipping")
            return None

        user_prompt = (prompt_template
            .replace('{query}', query or "")
            .replace('{code_files}', code_files))

        af_config = config['evaluators']['aesthetics_functional']
        api_config = {
            "api_key": af_config['api_key'],
            "base_url": af_config['base_url'],
        }
        api_type = af_config.get('api', 'openai')

        messages = [{"role": "user", "content": user_prompt}]

        for attempt in range(1, max_retries + 1):
            try:
                response_str = call_llm_api(
                    api_config=api_config,
                    messages=messages,
                    model=af_config['model'],
                    api=api_type,
                    json_mode=True if api_type == 'openai' else False,
                    save_to_verifier_dir=str(project_path / ".verifier"),
                )
                if not response_str:
                    raise ValueError("Planner LLM returned empty response")

                cleaned = re.sub(r',(\s*[}\]])', r'\1', response_str)
                code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned, re.DOTALL)
                if code_match:
                    cleaned = code_match.group(1)
                else:
                    start = cleaned.find('{')
                    end = cleaned.rfind('}')
                    if start != -1 and end > start:
                        cleaned = cleaned[start:end + 1]

                plan = json.loads(cleaned)
                plan_file = project_path / ".verifier" / "planner_plan.json"
                plan_file.parent.mkdir(parents=True, exist_ok=True)
                with open(plan_file, 'w', encoding='utf-8') as f:
                    json.dump(plan, f, indent=2, ensure_ascii=False)
                print(f"[{project_id}] Planner succeeded on attempt {attempt}")
                return plan
            except (json.JSONDecodeError, ValueError) as parse_err:
                print(f"[{project_id}] Planner attempt {attempt}/{max_retries} parse error: {parse_err}")
                if attempt < max_retries:
                    time.sleep(2)
            except Exception as e:
                print(f"[{project_id}] Planner attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    time.sleep(2)

        print(f"[{project_id}] Planner failed after {max_retries} attempts, TARS will run without plan")
        return None

    def _parse_tars_interaction_result(self, raw_result: str, project_id: str) -> Optional[Dict]:
        """Parse TARS raw result string as interaction summary JSON."""
        if not raw_result:
            return None
        try:
            parsed = json.loads(raw_result.strip())
            if isinstance(parsed, dict) and ("overall_observation" in parsed or "actions_performed" in parsed):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        # Try markdown code block extraction
        code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_result, re.DOTALL)
        if code_match:
            try:
                parsed = json.loads(code_match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        # Try outermost brace extraction
        start = raw_result.find('{')
        end = raw_result.rfind('}')
        if start != -1 and end > start:
            try:
                parsed = json.loads(raw_result[start:end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        print(f"[{project_id}] Could not parse TARS result as JSON, treating as text observation")
        return {"overall_observation": raw_result[:3000]}

    async def _run_browser_agent_tars_interaction(
        self, url: str, task_prompt: str, project_id: str, project_dir: Path,
        config: Dict[str, Any],
        planner_plan: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], str, Optional[Path], Optional[Path]]:
        """
        Run Agent-TARS in interaction-only mode (document findings, no scoring).

        Returns:
            Tuple of (interaction_summary_dict, status, video_path, screenshots_dir_path)
            status is "SUCCESS", "PARTIAL", or "FAILED"
            video_path is the path to screencast MP4 if available, else None
            screenshots_dir_path is the path to the screenshots directory if available, else None
        """
        try:
            from ..tars_agent_client import TARSAgentClient
        except ImportError:
            print(f"[{project_id}] Agent-TARS client not found")
            return {}, "FAILED", None, None

        agent_config = (config.get("evaluators") or {}).get("agent_tars", {}) or {}

        server_url = (
            os.environ.get("AGENT_TARS_SERVER_URL")
            or agent_config.get("server_url", "http://localhost:8890")
        )
        max_steps       = int(agent_config.get("max_steps", 40))
        max_steps_grace = int(agent_config.get("max_steps_grace", 5))
        step_count_mode = str(agent_config.get("step_count_mode", "tool_call"))
        timeout         = int(agent_config.get("timeout", 1800))
        max_concurrent  = int(agent_config.get("max_concurrent_sessions", 1))

        client_config = {
            "use_streaming":                  bool(agent_config.get("use_streaming", True)),
            "log_show_timectl_pid_url":        bool(agent_config.get("log_show_timectl_pid_url", False)),
            "log_show_raw_tool_result":        bool(agent_config.get("log_show_raw_tool_result", False)),
            "log_show_duplicate_thought":      bool(agent_config.get("log_show_duplicate_thought", False)),
            "log_suppress_repeated_args_start": bool(agent_config.get("log_suppress_repeated_args_start", True)),
            "max_steps":        max_steps,
            "max_steps_grace":  max_steps_grace,
            "step_count_mode":  step_count_mode,
            "cdp_port":         int(agent_config.get("cdp_port", 9225)),
            "close_tabs_on_finish": str(agent_config.get("close_tabs_on_finish", "true")).lower() in ("true", "1", "yes"),
            "screencast_enabled":   str(agent_config.get("screencast_enabled", "false")).lower() in ("true", "1", "yes"),
            "screencast_output":    str(agent_config.get("screencast_output", "")).strip(),
            "llm_log_enabled":      str(agent_config.get("llm_log_enabled", "true")).lower() in ("true", "1", "yes"),
            "llm_log_dir":          str(agent_config.get("llm_log_dir", "")).strip(),
        }

        prompt = self._load_tars_interaction_prompt(url, task_prompt, max_steps, planner_plan=planner_plan)
        print(f"[{project_id}] Starting TARS interaction-only mode (max_steps={max_steps}, plan={'yes' if planner_plan else 'no'})...")

        # Early check: project_dir is required for artifact reading
        if project_dir is None:
            print(f"[{project_id}] No project_dir provided, cannot read TARS artifacts")
            return {}, "FAILED", None, None

        verifier_dir = project_dir / ".verifier"
        interaction_summary: Dict[str, Any] = {}
        screenshots_dir = None

        max_interaction_retries = int(agent_config.get('iv_interaction_max_retries', 2))
        for interaction_attempt in range(1, max_interaction_retries + 1):
            if interaction_attempt > 1:
                print(f"[{project_id}] Retrying TARS interaction "
                      f"(attempt {interaction_attempt}/{max_interaction_retries})...")

            try:
                async with TARSAgentClient(
                    server_url=server_url,
                    max_concurrent=max_concurrent,
                    timeout=timeout,
                    config=client_config,
                ) as client:
                    _result, _status = await client.run_interaction(
                        prompt=prompt,
                        project_id=project_id,
                        project_dir=project_dir,
                        max_retries=1,
                    )
                print(f"[{project_id}] TARS interaction run returned status: {_status} "
                      f"(attempt {interaction_attempt})")
            except Exception as e:
                print(f"[{project_id}] TARS interaction-only exception "
                      f"(attempt {interaction_attempt}): {e}")
                traceback.print_exc()
                if interaction_attempt < max_interaction_retries:
                    continue
                return {}, "FAILED", None, None

            # Read artifacts from .verifier directory for this attempt
            interaction_summary = {}

            # Parse browser_actions as the primary source of interaction data.
            ba_file = verifier_dir / f"browser_actions_{project_id}_attempt1.json"
            if not ba_file.exists():
                ba_candidates = sorted(verifier_dir.glob(f"browser_actions_{project_id}_*.json"))
                if ba_candidates:
                    ba_file = ba_candidates[-1]

            if ba_file.exists():
                try:
                    with open(ba_file, 'r', encoding='utf-8') as f:
                        actions_data = json.load(f)
                    browser_actions = []
                    for action in (actions_data if isinstance(actions_data, list) else []):
                        if isinstance(action, dict):
                            browser_actions.append({
                                "step":      action.get("step"),
                                "tool":      action.get("tool"),
                                "arguments": action.get("arguments", {}),
                            })
                    if browser_actions:
                        interaction_summary = {"browser_actions": browser_actions}
                        print(f"[{project_id}] Parsed {len(browser_actions)} browser actions "
                              f"from {ba_file.name}")
                except Exception as e:
                    print(f"[{project_id}] Could not read browser_actions file: {e}")

            # Extract agent observations from the TARS agent's final JSON response.
            if _result and _result not in ("__STEP_LIMIT_ABORT__", "__TIMEOUT__"):
                parsed_result = self._parse_tars_interaction_result(_result, project_id)
                if parsed_result:
                    for key in ("issues_found", "intuitiveness_notes", "features_tested", "features_not_tested"):
                        if key in parsed_result:
                            interaction_summary[key] = parsed_result[key]
                            print(f"[{project_id}] Extracted '{key}' from TARS response "
                                  f"({len(parsed_result[key])} items)")
                    if parsed_result.get("overall_observation"):
                        interaction_summary["overall_observation"] = parsed_result["overall_observation"]

            # Locate screenshots directory for this interaction attempt
            screenshots_dir = verifier_dir / "agent_screenshots" / "attempt1"
            if not screenshots_dir.exists():
                screenshots_parent = verifier_dir / "agent_screenshots"
                if screenshots_parent.exists():
                    attempt_dirs = sorted(d for d in screenshots_parent.iterdir() if d.is_dir())
                    if attempt_dirs:
                        screenshots_dir = attempt_dirs[-1]

            if interaction_summary:
                break  # Successfully extracted interaction data

            if interaction_attempt < max_interaction_retries:
                print(f"[{project_id}] No interaction data extracted "
                      f"(attempt {interaction_attempt}), retrying...")

        if not interaction_summary:
            print(f"[{project_id}] No interaction data extracted from TARS artifacts "
                  f"after {max_interaction_retries} attempts")
            return {}, "FAILED", None, None

        # Locate screencast video
        video_path: Optional[Path] = None
        video_file = verifier_dir / "screencast_attempt1.mp4"
        if not video_file.exists():
            video_file = verifier_dir / "agent-tars" / "screencast_attempt1.mp4"
        if not video_file.exists():
            video_candidates = sorted(
                list(verifier_dir.glob("screencast_*.mp4")) +
                list(verifier_dir.glob("agent-tars/screencast_*.mp4"))
            )
            if video_candidates:
                video_file = video_candidates[-1]
        if video_file.exists():
            video_path = video_file
            print(f"[{project_id}] Found screencast video: {video_file.relative_to(verifier_dir)}")
        else:
            print(f"[{project_id}] No screencast video found (screencast may be disabled)")

        tars_status = "SUCCESS" if interaction_summary.get("browser_actions") else "PARTIAL"
        print(f"[{project_id}] Interaction summary extracted (status={tars_status}, "
              f"actions={len(interaction_summary.get('browser_actions', []))})")
        return interaction_summary, tars_status, video_path, (screenshots_dir if screenshots_dir.exists() else None)

    async def _evaluate_tars_interaction_with_vlm(
        self,
        config: Dict[str, Any],
        query: str,
        interaction_summary: Dict[str, Any],
        static_scores: Dict[str, Any],
        screenshots_dir: Optional[Path],
        initial_screenshot: Optional[Path],
        project_dir: Path,
        video_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Use VLM to detect problems from TARS interaction artifacts.

        Visual media priority (highest → lowest):
          1. Complete screencast video (MP4) — passed as video content type
          2. Agent interaction screenshots (agent_screenshots/attempt*)
          3. Initial static screenshot only (baseline fallback)
          4. No media at all → return {"_skip_vlm": True} to skip this step

        Returns:
            detected_problems dict, or {"_skip_vlm": True} to signal caller to use static scores.
        """
        af_config = config['evaluators']['aesthetics_functional']
        api_config = {
            "api_key": af_config['api_key'],
            "base_url": af_config['base_url'],
        }
        api_type = af_config.get('api', 'openai')

        # Load problem detection prompt template
        prompt_file = Path(__file__).parent.parent / "prompts" / "vlm_video_problem_detection.txt"
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            raise RuntimeError(f"Problem detection prompt not found: {prompt_file}")

        interaction_summary_str = json.dumps(interaction_summary, ensure_ascii=False, indent=2)

        user_prompt = (prompt_template
            .replace('{game_description}',        query)
            .replace('{static_aesthetics_score}', str(static_scores.get('aesthetics_score', 0.0)))
            .replace('{static_aesthetics_reason}', str(static_scores.get('aesthetics_reason', '')))
            .replace('{static_functional_score}',  str(static_scores.get('functional_score', 0.0)))
            .replace('{static_functional_reason}', str(static_scores.get('functional_reason', '')))
            .replace('{interaction_summary}',      interaction_summary_str))

        # ── Collect visual media (priority: video > screenshots > initial only > skip) ──
        has_video = video_path and Path(video_path).exists()
        has_screenshots = screenshots_dir and Path(screenshots_dir).exists() and bool(sorted(Path(screenshots_dir).glob("*.webp")))
        has_initial = initial_screenshot and Path(initial_screenshot).exists()

        if not has_video and not has_screenshots:
            print("  No agent media available — skipping VLM problem detection")
            return {"_skip_vlm": True}

        # Build messages based on available media
        content_parts = []

        if has_video:
            # Priority 1: send the complete screencast video as a video content type
            try:
                video_bytes = Path(video_path).read_bytes()
                video_b64 = base64.b64encode(video_bytes).decode('utf-8')
                video_size_kb = len(video_bytes) // 1024
                print(f"  Using complete screencast video ({video_size_kb} KB)")
                if api_type == 'anthropic':
                    content_parts.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png",
                                   "data": base64.b64encode(Path(initial_screenshot).read_bytes()).decode('utf-8')}
                    } if has_initial else None)
                    content_parts = [c for c in content_parts if c]
                    raise NotImplementedError("Anthropic does not support video; falling back to screenshots")
                else:
                    # OpenAI-compatible: include initial static screenshot + video
                    if has_initial:
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{base64.b64encode(Path(initial_screenshot).read_bytes()).decode('utf-8')}"}
                        })
                    content_parts.append({
                        "type": "video_url",
                        "video_url": {"url": f"data:video/mp4;base64,{video_b64}"}
                    })
            except NotImplementedError:
                has_video = False
                content_parts = []
            except Exception as _ve:
                print(f"  Could not encode video ({_ve}); falling back to screenshots")
                has_video = False
                content_parts = []

        if not has_video:
            # Priority 2: interaction step screenshots; Priority 3: initial screenshot only
            screenshot_paths: list = []
            if has_initial:
                screenshot_paths.append(Path(initial_screenshot))
            if has_screenshots:
                interaction_shots = sorted(Path(screenshots_dir).glob("*.webp"))[:15]
                screenshot_paths.extend(interaction_shots)
                print(f"  Using initial screenshot + {len(interaction_shots)} agent screenshots")
            else:
                print("  No interaction screenshots — using initial screenshot only")

            if api_type == 'anthropic':
                for shot_path in screenshot_paths:
                    try:
                        img_b64 = base64.b64encode(shot_path.read_bytes()).decode('utf-8')
                        content_parts.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/webp", "data": img_b64}
                        })
                    except Exception as e:
                        print(f"Warning: Could not encode screenshot {shot_path.name}: {e}")
            else:
                for shot_path in screenshot_paths:
                    try:
                        img_b64 = base64.b64encode(shot_path.read_bytes()).decode('utf-8')
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/webp;base64,{img_b64}"}
                        })
                    except Exception as e:
                        print(f"Warning: Could not encode screenshot {shot_path.name}: {e}")

        # Fail if all screenshots failed to encode
        if not has_video:
            image_count = len([c for c in content_parts if c.get('type') in ('image', 'image_url')])
            if screenshot_paths and image_count == 0:
                raise RuntimeError(f"All {len(screenshot_paths)} screenshots failed to encode — cannot run VLM problem detection")

        content_parts.append({"type": "text", "text": user_prompt})
        messages = [{"role": "user", "content": content_parts}]

        media_desc = f"video ({Path(video_path).name})" if has_video else f"{len([c for c in content_parts if c.get('type') in ('image', 'image_url')])} screenshots"
        print(f"  Problem detection VLM call: {media_desc}, api={api_type}")

        max_retries = int(config.get('evaluators', {}).get('agent_tars', {}).get('iv_detection_max_retries', 5))
        for attempt in range(1, max_retries + 1):
            try:
                response_str = call_llm_api(
                    api_config=api_config,
                    messages=messages,
                    model=af_config['model'],
                    api=api_type,
                    json_mode=True if api_type == 'openai' else False,
                    save_to_verifier_dir=str(project_dir / ".verifier"),
                )
                if not response_str:
                    raise ValueError("VLM returned empty response")

                # Fix trailing commas and extract JSON
                cleaned = re.sub(r',(\s*[}\]])', r'\1', response_str)
                code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned, re.DOTALL)
                if code_match:
                    cleaned = code_match.group(1)
                else:
                    start = cleaned.find('{')
                    end = cleaned.rfind('}')
                    if start != -1 and end > start:
                        cleaned = cleaned[start:end + 1]

                result = json.loads(cleaned)
                print(f"  Problem detection succeeded on attempt {attempt}")
                return result

            except (json.JSONDecodeError, ValueError) as parse_err:
                print(f"  Problem detection attempt {attempt}/{max_retries} parse error: {parse_err}")
                if attempt < max_retries:
                    time.sleep(2)
            except Exception as e:
                print(f"  Problem detection attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    time.sleep(2)

        print("  Problem detection: all retries failed, returning empty problem list")
        return {
            "functional_problems": [],
            "aesthetic_problems": [],
            "dismissed_static_problems": [],
            "overall_assessment": "Problem detection VLM call failed after all retries.",
            "_detection_failed": True,
        }

    async def _adjust_scores_based_on_problems(
        self,
        config: Dict[str, Any],
        query: str,
        static_scores: Dict[str, Any],
        detected_problems: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Call LLM to adjust static scores based on problems detected during TARS interaction.

        Returns:
            dict with adjusted scores; falls back to original static scores if all retries fail.
        """
        af_config = config['evaluators']['aesthetics_functional']
        api_config = {
            "api_key": af_config['api_key'],
            "base_url": af_config['base_url'],
        }
        api_type = af_config.get('api', 'openai')

        # Load score adjustment prompt template
        prompt_file = Path(__file__).parent.parent / "prompts" / "vlm_video_score_adjustment.txt"
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            raise RuntimeError(f"Score adjustment prompt not found: {prompt_file}")

        detected_problems_str = json.dumps(detected_problems, ensure_ascii=False, indent=2)

        user_prompt = (prompt_template
            .replace('{game_description}',        query)
            .replace('{static_aesthetics_score}', str(static_scores.get('aesthetics_score', 0.0)))
            .replace('{static_aesthetics_reason}', str(static_scores.get('aesthetics_reason', '')))
            .replace('{static_functional_score}',  str(static_scores.get('functional_score', 0.0)))
            .replace('{static_functional_reason}', str(static_scores.get('functional_reason', '')))
            .replace('{detected_problems}',        detected_problems_str))

        messages = [{"role": "user", "content": user_prompt}]

        max_retries = int(config.get('evaluators', {}).get('agent_tars', {}).get('iv_adjustment_max_retries', 5))
        for attempt in range(1, max_retries + 1):
            try:
                response_str = call_llm_api(
                    api_config=api_config,
                    messages=messages,
                    model=af_config['model'],
                    api=api_type,
                    json_mode=True if api_type == 'openai' else False,
                )
                if not response_str:
                    raise ValueError("LLM returned empty response")

                # Fix trailing commas and extract JSON
                cleaned = re.sub(r',(\s*[}\]])', r'\1', response_str)
                code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned, re.DOTALL)
                if code_match:
                    cleaned = code_match.group(1)
                else:
                    start = cleaned.find('{')
                    end = cleaned.rfind('}')
                    if start != -1 and end > start:
                        cleaned = cleaned[start:end + 1]

                result = json.loads(cleaned)

                # Validate required keys
                if 'adjusted_functional_score' not in result or 'adjusted_aesthetics_score' not in result:
                    raise ValueError(f"Missing required score fields: {list(result.keys())}")

                # Clamp to valid 0-8 range
                result['adjusted_functional_score']  = max(0.0, min(8.0, float(result['adjusted_functional_score'])))
                result['adjusted_aesthetics_score'] = max(0.0, min(8.0, float(result['adjusted_aesthetics_score'])))

                # Floor the functional score if configured.
                # Skip when iv_skip_initial_static=true (no static baseline to compare against).
                functional_score_floor = config["evaluators"]["agent_tars"].get("functional_score_floor", True)
                _skip_static = str(config.get('evaluators', {}).get('agent_tars', {}).get('iv_skip_initial_static', 'false')).lower() in ('true', '1', 'yes')
                if functional_score_floor and not _skip_static:
                    static_func = static_scores.get('functional_score', 0.0)
                    llm_func    = result['adjusted_functional_score']
                    result['adjusted_functional_score'] = min(static_func, llm_func)
                    result['functional_reason'] = (
                        f'[Score floor applied: min(static={static_func:.2f}, '
                        f'LLM-adjusted={llm_func:.2f}) = {result["adjusted_functional_score"]:.2f}]'
                        f'Adjusted functional reason based on video content: {result.get("functional_reason", "")}'
                    )

                print(f"  Score adjustment succeeded on attempt {attempt}: "
                      f"func={result['adjusted_functional_score']}/8 "
                      f"aes={result['adjusted_aesthetics_score']}/8")
                return result

            except (json.JSONDecodeError, ValueError, KeyError) as parse_err:
                print(f"  Score adjustment attempt {attempt}/{max_retries} parse error: {parse_err}")
                if attempt < max_retries:
                    time.sleep(2)
            except Exception as e:
                print(f"  Score adjustment attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    time.sleep(2)

        # All retries failed
        print("Score adjustment: all retries failed — returning None scores.")
        return {
            "adjusted_functional_score":  None,
            "functional_reason":          "Score adjustment failed after all retries.",
            "adjusted_aesthetics_score":  None,
            "aesthetics_reason":          "Score adjustment failed after all retries.",
            "adjustment_summary":         "Score adjustment failed after all retries.",
        }