"""
Aesthetics and Functional Evaluator for the verifier.

This evaluator uses a vision-language model to evaluate both:
1. Aesthetics: Visual design quality based on a screenshot
2. Functionality: Code correctness and implementation quality

The evaluator combines both evaluations in a single LLM call for consistency.
"""

import json
import re
from pathlib import Path
from typing import Dict, Any

from .base_evaluator import BaseEvaluator


from ..utils.screenshot_utils import take_screenshot_with_logs, is_screenshot_blank_or_minimal
from utils.api_calls import call_llm_api
from ..css_reset_check import check_css_reset_issue


def fix_json_trailing_commas(json_str: str) -> str:
    """Fix trailing commas in JSON strings (common LLM output issue)"""
    # Remove trailing commas before } or ]
    # Handles: {"key": "value",} -> {"key": "value"}
    return re.sub(r',(\s*[}\]])', r'\1', json_str)

# Use the latest axe-core version
AXE_CORE_SOURCE = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.3/axe.min.js"


def load_static_prompt(version: str) -> str:
    """Load a static scoring prompt by version identifier."""
    prompt_path = Path(__file__).parent.parent / "prompts" / f"static_vlm_scoring_{version}.txt"
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read().strip()


def encode_image_to_base64(image_path: Path) -> str:
    """Encode an image file to base64 string."""
    import base64
    with open(image_path, 'rb') as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def run_axe_accessibility_test(server_url: str) -> Dict[str, Any]:
    """
    Run axe-core accessibility test on the given URL.
    
    Args:
        server_url: The URL to test for accessibility
        
    Returns:
        Dictionary containing accessibility test results and score
    """
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            
            # Navigate to the page
            page.goto(server_url, timeout=15000)
            page.wait_for_load_state('networkidle', timeout=15000)
            
            # Inject axe-core
            page.add_script_tag(url="https://unpkg.com/axe-core@4.7.0/axe.min.js")
            
            # Run axe-core analysis
            axe_results = page.evaluate("""
                () => {
                    return new Promise((resolve) => {
                        axe.run((err, results) => {
                            if (err) {
                                resolve({ error: err.message });
                            } else {
                                resolve(results);
                            }
                        });
                    });
                }
            """)
            
            browser.close()
            
            if 'error' in axe_results:
                return {
                    "score": 0.0,
                    "reason": f"Axe-core test failed: {axe_results['error']}",
                    "error": axe_results['error']
                }
            
            # Process results
            violations = axe_results.get('violations', [])
            passes = axe_results.get('passes', [])
            incomplete = axe_results.get('incomplete', [])
            
            # Categorize violations by impact
            critical_violations = [v for v in violations if v.get('impact') == 'critical']
            serious_violations = [v for v in violations if v.get('impact') == 'serious']
            moderate_violations = [v for v in violations if v.get('impact') == 'moderate']
            minor_violations = [v for v in violations if v.get('impact') == 'minor']
            
            # Calculate a score based on violations (simple scoring method)
            total_violations = len(violations)
            if total_violations == 0:
                score = 2.0
                reason = "No accessibility violations found."
            elif len(critical_violations) > 0:
                score = 0.0
                reason = f"Found {len(critical_violations)} critical accessibility violations."
            elif len(serious_violations) > 0:
                score = 0.5
                reason = f"Found {len(serious_violations)} serious accessibility violations."
            elif len(moderate_violations) > 0:
                score = 1.0
                reason = f"Found {len(moderate_violations)} moderate accessibility violations."
            else:
                score = 1.5
                reason = f"Found only {len(minor_violations)} minor accessibility violations."
            
            # Create detailed violation information for top violations
            violation_details = []
            for violation in violations[:5]:  # Top 5 violations
                violation_details.append({
                    "rule": violation.get('id', 'unknown'),
                    "impact": violation.get('impact', 'unknown'),
                    "help": violation.get('help', 'No description'),
                    "helpUrl": violation.get('helpUrl', ''),
                    "nodes_count": len(violation.get('nodes', []))
                })
                
                return {
                    "score": score, 
                    "reason": reason, 
                    "error": None,
                    "accessibility_details": {
                        "violations_summary": {
                            "total": len(violations),
                            "critical": len(critical_violations),
                            "serious": len(serious_violations), 
                            "moderate": len(moderate_violations),
                            "minor": len(minor_violations)
                        },
                        "top_violations": violation_details,
                        "passes": len(passes),
                        "incomplete": len(incomplete)
                    }
                }
                
    except Exception as e:
        error_msg = f"Accessibility test failed: {e}"
        return {
            "score": 0.0, 
            "reason": "Accessibility test failed due to an exception.", 
            "error": error_msg
        }


class AestheticsFunctionalEvaluator(BaseEvaluator):
    """
    Combined evaluator that evaluates both aesthetics and functionality in a single LLM call.
    Takes a screenshot, reads the code, collects runtime information, and sends all to an LLM for evaluation.
    """

    def __init__(self, config: Dict[str, Any], temp_dir: Path):
        self.config = config
        self.temp_dir = temp_dir

    def evaluate(self, language_handler, **kwargs) -> Dict[str, Any]:
        """
        Takes a screenshot, reads the code, collects runtime info, and evaluates both aesthetics and functionality.
        """
        print("Running enhanced 'Aesthetics + Functional' evaluation...")
        
        query = kwargs.get('query')

        if not query:
            return {
                "aesthetics": {"score": None, "reason": "A 'query' is required for evaluation.", "error": "Missing arguments."},
                "functional": {"score": None, "reason": "A 'query' is required for evaluation.", "error": "Missing arguments."}
            }

        server_url = language_handler.server_url

        if not server_url:
            verifier_dir = language_handler.project_dir / ".verifier"
            verifier_dir.mkdir(exist_ok=True)
            screenshot_path = verifier_dir / f"screenshot_{language_handler.project_dir.name}.png"
            return {
                "aesthetics": {"score": None, "reason": "Dev server is not running, cannot take screenshot.", "error": "Server not available."},
                "functional": {"score": None, "reason": "Dev server is not running, cannot evaluate functionality.", "error": "Server not available."}
            }
            
        # Create .verifier folder under project directory for screenshots
        verifier_dir = language_handler.project_dir / ".verifier"
        verifier_dir.mkdir(exist_ok=True)
        screenshot_path = verifier_dir / f"screenshot_{language_handler.project_dir.name}.png"

        # Check if runtime logging is enabled
        runtime_logging_enabled = self.config.get('runtime_logging_enabled', True)
        
        # Handle screenshot collection for web projects
        screenshot_success, error_msg, console_logs = take_screenshot_with_logs(
            server_url, str(screenshot_path), save_logs=runtime_logging_enabled
        )
        if not screenshot_success:
            return {
                "aesthetics": {"score": None, "reason": "Failed to take screenshot.", "error": error_msg},
                "functional": {"score": None, "reason": "Failed to take screenshot for evaluation.", "error": error_msg}
            }

        # Check if the screenshot shows a blank page (common issue)
        is_blank_page = False
        blank_page_context = ""
        if screenshot_path:
            is_blank_page = is_screenshot_blank_or_minimal(screenshot_path)
            if is_blank_page:
                print(f"WARNING: Screenshot shows blank/minimal content for {server_url}")
                blank_page_context = "\n\nIMPORTANT: The screenshot shows a blank or nearly blank page, which may indicate rendering issues, missing assets, compilation errors, or the application failing to mount properly. "
                blank_page_context +="This means 0.0 score for aesthetics and at most 0.5 score for functionality."

        # 2. Collect dev server output after page access (bugs might be triggered by opening the URL)
        dev_server_output = []
        if runtime_logging_enabled and hasattr(language_handler, 'get_dev_server_output_after_ready'):
            print("Collecting dev server output after screenshot...")
            ready_indicators = self.config.get('dev_server_ready_indicators', ["➜  press h + enter to show help", "ready in", "Local:", "ready at"])
            dev_server_output = language_handler.get_dev_server_output_after_ready(
                wait_seconds=3, save_logs=runtime_logging_enabled, ready_indicators=ready_indicators
            )

        # 3. Run axe-core accessibility test (if enabled and not pygame or python)
        accessibility_result = None
        use_axe_core = self.config.get('axe_core_enabled', True)
        if use_axe_core:
            print("Running axe-core accessibility test...")
            accessibility_result = run_axe_accessibility_test(server_url)
        else:
            print("Axe-core accessibility test disabled")

        # 4. Read all code from the project directory
        project_dir = language_handler.project_dir
        code_content = []
        # Filter to avoid including heavy files or irrelevant directories
        ignore_dirs = {'node_modules', '.git', 'dist', 'build', '.verifier'}
        ignore_files = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', ".DS_Store"}
        binary_extensions = {
            '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg', '.webp', '.avif',
            '.mp4', '.webm', '.ogg', '.mp3', '.wav', '.flac',
            '.woff', '.woff2', '.ttf', '.eot', '.otf',
            '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
            '.pdf', '.doc', '.docx', '.xls', '.xlsx',
            '.pyc', '.pyo', '.so', '.dll', '.dylib', '.exe',
            '.bin', '.dat', '.db', '.sqlite',
        }
        for file_path in project_dir.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() not in binary_extensions and not any(part in ignore_dirs for part in file_path.parts) and not file_path.name in ignore_files:
                try:
                    code_content.append(f"--- FILE: {file_path.relative_to(project_dir)} ---\n\n{file_path.read_text(errors='ignore')}")
                except Exception:
                    continue  # Skip files that can't be read
        
        full_code = "\n\n".join(code_content)
        if not full_code:
            return {
                "aesthetics": {"score": None, "reason": "No code files found for evaluation.", "error": "No code files found."},
                "functional": {"score": None, "reason": "No code files found in the project directory.", "error": "No code files found."}
            }

        # 5. Format runtime information
        runtime_info = self._format_runtime_info(console_logs, dev_server_output, runtime_logging_enabled)

        # 6. Prepare the LLM request
        try:
            # Choose prompt based on runtime logging configuration
            prompt_version = self.config.get('prompt_version', 'v3')
            skip_screenshot_take = self.config.get('skip_screenshot_take', False)

            if skip_screenshot_take:
                # Ablation: skip screenshot, evaluate only with code + runtime logs
                system_prompt = load_static_prompt(prompt_version)
                base64_image = None
            elif runtime_logging_enabled:
                system_prompt = load_static_prompt(prompt_version)
                base64_image = encode_image_to_base64(screenshot_path)
            else:
                system_prompt = load_static_prompt("without_screenshot")
                base64_image = encode_image_to_base64(screenshot_path)

            # Include accessibility test results if available
            accessibility_info = ""
            if use_axe_core and accessibility_result:
                accessibility_info = f"""
Accessibility Test Results (axe-core):
- Score: {accessibility_result['score']:.2f}
- Reason: {accessibility_result['reason']}
"""
                if accessibility_result.get('accessibility_details'):
                    details = accessibility_result['accessibility_details']
                    if 'violations_summary' in details:
                        summary = details['violations_summary']
                        accessibility_info += f"- Total violations: {summary['total']} (Critical: {summary['critical']}, Serious: {summary['serious']}, Moderate: {summary['moderate']}, Minor: {summary['minor']})\n"
                    if 'top_violations' in details and details['top_violations']:
                        accessibility_info += "- Top violations:\n"
                        for v in details['top_violations']:
                            accessibility_info += f"  • {v['rule']} ({v['impact']}): {v['help']}\n"
            
            # Prepare message content based on API type
            api_type = self.config['api']
            
            # Prepare user content with actual data only
            user_content_parts = [
                f"User Query: {query}"
            ]
            
            if runtime_logging_enabled:
                user_content_parts.append(runtime_info)
                
            if accessibility_info:
                user_content_parts.append(accessibility_info)
            
            # Add blank page context if detected
            if blank_page_context:
                user_content_parts.append(blank_page_context)
                
            user_content_parts.append(f"\nCode:\n{full_code}")
            
            user_text = "\n\n".join(part for part in user_content_parts if part.strip())

            # Build messages — skip image if skip_screenshot_take or no image available
            if skip_screenshot_take or base64_image is None:
                messages = [
                    {"role": "user", "content": user_text}
                ]
            elif api_type == 'anthropic':
                # Claude format
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64_image
                                }
                            },
                        ],
                    }
                ]
            else:
                # OpenAI format
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                            },
                        ],
                    }
                ]

            # 7. Call the LLM
            api_config = {
                "api_key": self.config['api_key'],
                "base_url": self.config['base_url']
            }
            
            # Save LLM interaction to .verifier directory
            verifier_dir = language_handler.project_dir / ".verifier"
            
            # Get stream setting from config
            stream_enabled = self.config.get('stream', False)
            
            response_str = call_llm_api(
                api_config=api_config,
                messages=messages,
                system_message=system_prompt,  # Now using system message
                model=self.config['model'],
                api=api_type,
                json_mode=True if api_type == 'openai' else False,
                save_to_verifier_dir=str(verifier_dir),
                stream=stream_enabled
            )
            
            # 8. Parse the response
            if not response_str:
                return {
                    "aesthetics": {"score": None, "reason": "LLM returned empty response.", "error": "Empty response"},
                    "functional": {"score": None, "reason": "LLM returned empty response.", "error": "Empty response"}
                }
            
            try:
                # Fix trailing commas (common LLM output issue)
                fixed_response = fix_json_trailing_commas(response_str)
                result = json.loads(fixed_response)
            except json.JSONDecodeError as e:
                return {
                    "aesthetics": {"score": None, "reason": f"Failed to parse LLM response: {e}", "error": f"JSON decode error: {e}"},
                    "functional": {"score": None, "reason": f"Failed to parse LLM response: {e}", "error": f"JSON decode error: {e}"}
                }
            
            aesthetics_result = {
                "score": float(result.get("aesthetics_score", 0.0)),
                "reason": result.get("aesthetics_reason", "No reason provided."),
                "error": None
            }
            # CSS reset布局问题检测
            try:
                if check_css_reset_issue(language_handler.project_dir):
                    original_score = aesthetics_result["score"]
                    aesthetics_result["score"] = min(aesthetics_result["score"], 1.0)
                    aesthetics_result["reason"] = (
                        f"[CSS Reset Issue] src/index.css contains a universal * {{margin:0;padding:0}} reset "
                        f"while TSX files use Tailwind spacing classes and have semantic HTML elements relying "
                        f"on browser default spacing, causing layout issues. "
                        f"Aesthetics score capped from {original_score:.1f} to 1.0 (max 8). "
                        f"Original reason: {aesthetics_result['reason']}"
                    )
                    print(f"[CSS Reset Check] ISSUE detected in {language_handler.project_dir.name}, "
                          f"aesthetics score capped: {original_score:.1f} -> 1.0")
                else:
                    print(f"[CSS Reset Check] OK for {language_handler.project_dir.name}")
            except Exception as css_check_err:
                print(f"[CSS Reset Check] Error during check: {css_check_err}")

            llm_functional_score = float(result.get("functional_score", 0.0))
            functional_reason = result.get("functional_reason", "No reason provided.")
            
            # Check if pure LLM scoring is enforced
            use_pure_llm_scoring = self.config.get('pure_llm_scoring', True)
            
            # Add LLM interaction paths info
            llm_interaction_info = {
                "payload_file": str(verifier_dir / "llm_payload_<timestamp>.json"),
                "response_file": str(verifier_dir / "llm_response_<timestamp>.json")
            }
            
            if use_axe_core and accessibility_result:
                # Include accessibility info in reason
                if accessibility_result['reason']:
                    functional_reason += f" Accessibility: {accessibility_result['reason']}"
                
                # Determine final functional score based on configuration
                if use_pure_llm_scoring:
                    # Pure LLM scoring: use LLM score directly
                    final_functional_score = llm_functional_score
                    scoring_method = "pure_llm_with_accessibility_context"
                else:
                    # Legacy combined scoring (if needed)
                    final_functional_score = llm_functional_score  # Keep pure for now
                    scoring_method = "llm_only_with_accessibility_context"
                
                functional_result = {
                    "score": final_functional_score,
                    "reason": functional_reason,
                    "error": None,
                    "details": {
                        "llm_score": llm_functional_score,
                        "accessibility_score": accessibility_result['score'],
                        "accessibility_details": accessibility_result.get('accessibility_details'),
                        "scoring_method": scoring_method,
                        "pure_llm_scoring": use_pure_llm_scoring,
                        "runtime_info": {
                            "console_logs": console_logs,
                            "dev_server_output": dev_server_output
                        },
                        "llm_interaction": llm_interaction_info
                    }
                }
            else:
                # Use only LLM score when axe-core is disabled
                functional_result = {
                    "score": llm_functional_score,
                    "reason": functional_reason,
                    "error": None,
                    "details": {
                        "llm_score": llm_functional_score,
                        "scoring_method": "pure_llm_scoring_with_runtime_info",
                        "pure_llm_scoring": use_pure_llm_scoring,
                        "runtime_info": {
                            "console_logs": console_logs,
                            "dev_server_output": dev_server_output
                        },
                        "llm_interaction": llm_interaction_info
                    }
                }
            return {
                "aesthetics": aesthetics_result,
                "functional": functional_result
            }
            
        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse LLM response as JSON: {e}. Response: {response_str}"
            return {
                "aesthetics": {"score": None, "reason": "Invalid response format from LLM.", "error": error_msg},
                "functional": {"score": None, "reason": "Invalid response format from LLM.", "error": error_msg}
            }
        except Exception as e:
            error_msg = f"Evaluation failed: {e}"
            return {
                "aesthetics": {"score": None, "reason": "Evaluation failed due to an exception.", "error": error_msg},
                "functional": {"score": None, "reason": "Evaluation failed due to an exception.", "error": error_msg}
            }

    def _format_runtime_info(self, console_logs, dev_server_output, runtime_logging_enabled):
        """
        Format runtime information (console logs and dev server output) for inclusion in the prompt.
        """
        runtime_parts = []
        
        # Get console log filtering configuration
        allowed_log_types = self.config.get('console_log_types', ['error', 'warning', 'assert'])
        
        # Format browser console logs
        if console_logs:
            # Filter console logs based on configuration
            filtered_logs = [log for log in console_logs if log['type'] in allowed_log_types]
            
            runtime_parts.append("\n\n=== BROWSER CONSOLE LOGS ===")
            runtime_parts.append(f"Console messages captured while accessing the webpage (filtered for types: {', '.join(allowed_log_types)}):")
            
            if filtered_logs:
                # Group filtered logs by type
                error_logs = [log for log in filtered_logs if log['type'] == 'error']
                warning_logs = [log for log in filtered_logs if log['type'] == 'warning']
                assert_logs = [log for log in filtered_logs if log['type'] == 'assert']
                other_important_logs = [log for log in filtered_logs if log['type'] not in ['error', 'warning', 'assert']]
                
                if error_logs:
                    runtime_parts.append("\nERRORS:")
                    for log in error_logs[:5]:  # Limit to top 5 errors
                        runtime_parts.append(f"  {log['type'].upper()}: {log['text']} (at {log['location']})")
                
                if warning_logs:
                    runtime_parts.append("\nWARNINGS:")
                    for log in warning_logs[:5]:  # Limit to top 5 warnings
                        runtime_parts.append(f"  {log['type'].upper()}: {log['text']} (at {log['location']})")
                
                if assert_logs:
                    runtime_parts.append("\nASSERTION FAILURES:")
                    for log in assert_logs[:3]:  # Limit to top 3 assertions
                        runtime_parts.append(f"  {log['type'].upper()}: {log['text']} (at {log['location']})")
                
                if other_important_logs:
                    runtime_parts.append("\nOTHER IMPORTANT LOGS:")
                    for log in other_important_logs[:3]:  # Limit to top 3 other logs
                        runtime_parts.append(f"  {log['type'].upper()}: {log['text']} (at {log['location']})")
                        
                total_filtered = len(filtered_logs)
                total_shown = min(16, total_filtered)  # 5+5+3+3
                if total_filtered > total_shown:
                    runtime_parts.append(f"... and {total_filtered - total_shown} more important console messages")
                    
                # Add info about filtered out logs
                total_logs = len(console_logs)
                filtered_out = total_logs - total_filtered
                if filtered_out > 0:
                    runtime_parts.append(f"(Note: {filtered_out} console messages of other types were filtered out)")
            else:
                runtime_parts.append("No important console messages found (errors, warnings, assertions).")
                total_logs = len(console_logs)
                if total_logs > 0:
                    runtime_parts.append(f"(Note: {total_logs} console messages of other types were filtered out)")
        else:
            runtime_parts.append("\n\n=== BROWSER CONSOLE LOGS ===")
            runtime_parts.append("No console messages were captured.")
        
        # Format dev server output
        if dev_server_output:
            runtime_parts.append("\n\n=== DEV SERVER OUTPUT ===")
            runtime_parts.append("Output captured from the development server after ready indicators:")
            runtime_parts.append("(This may include error messages, warnings, or other diagnostic information)")
            
            for line in dev_server_output[-10:]:  # Show last 10 lines
                runtime_parts.append(f"  {line}")
                
            if len(dev_server_output) > 10:
                runtime_parts.append(f"... and {len(dev_server_output) - 10} more lines of server output")
        else:
            runtime_parts.append("\n\n=== DEV SERVER OUTPUT ===")
            runtime_parts.append("No development server output was captured after ready indicators.")
        
        return "\n".join(runtime_parts) 