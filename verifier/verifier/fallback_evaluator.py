"""
Fallback evaluator for when agent-tars retries are exhausted.
Uses LLM to evaluate based on saved browser actions and screenshots.
"""

import json
import base64
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import aiohttp
import asyncio


class FallbackEvaluator:
    """
    Fallback evaluator that uses LLM to score based on browser_actions and screenshots
    when all agent-tars attempts fail.
    """
    
    def __init__(self, model_config: Dict[str, Any]):
        """
        Initialize fallback evaluator with model configuration.
        
        Args:
            model_config: Model configuration from YAML (evaluators.agent_tars)
        """
        # Handle both old format (strings) and new format (dict with provider/model_id)
        model_value = model_config.get("model", "gpt-4.1")
        if isinstance(model_value, dict):
            # New format: {"provider": "volcengine", "model_id": "doubao-seed-1-8-251228", ...}
            self.api = model_value.get("provider", "openai")
            self.model = model_value.get("model_id", "gpt-4.1")
            self.api_key = model_value.get("api_key")
            self.base_url = model_value.get("base_url")
        else:
            # Old format: simple strings
            self.model = model_value
            self.api = model_config.get("api", "openai")
            self.api_key = model_config.get("api_key")
            self.base_url = model_config.get("base_url")
        
        # Load prompt template
        prompt_file = Path(__file__).parent / "prompts" / "fallback_evaluation.prompt"
        with open(prompt_file, 'r', encoding='utf-8') as f:
            self.prompt_template = f.read()
    
    def _find_best_attempt(
        self, 
        project_dir: Path, 
        project_id: str, 
        max_retries: int
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        """
        Find the best attempt based on step count and timestamp.
        
        Returns:
            (attempt_number, history_data) or (None, None) if no valid attempts found
        """
        attempts = []
        
        for i in range(1, max_retries + 1):
            history_file = project_dir / ".verifier" / f"official_tars_history_{project_id}_attempt{i}.json"
            
            if not history_file.exists():
                continue
            
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                events = data.get("events", [])
                # Count browser tool_call events
                browser_steps = sum(
                    1 for e in events 
                    if e.get("type") == "tool_call" and 
                    isinstance(e.get("name", ""), str) and 
                    e.get("name", "").startswith("browser_")
                )
                
                attempts.append({
                    "attempt": i,
                    "browser_steps": browser_steps,
                    "total_events": len(events),
                    "timestamp": data.get("timestamp", ""),
                    "history_data": data
                })
            except Exception as e:
                print(f"⚠️ 无法读取 attempt{i} 历史文件: {e}")
                continue
        
        if not attempts:
            return None, None
        
        # Sort by: browser_steps (desc), timestamp (desc)
        attempts.sort(key=lambda x: (-x["browser_steps"], x["timestamp"]), reverse=False)
        best = attempts[0]
        
        print(f"✅ 选择最佳attempt: attempt{best['attempt']} "
              f"(browser_steps={best['browser_steps']}, "
              f"total_events={best['total_events']})")
        
        return best["attempt"], best["history_data"]
    
    def _load_browser_actions(
        self, 
        project_dir: Path, 
        project_id: str, 
        attempt: int
    ) -> Optional[List[Dict[str, Any]]]:
        """Load browser_actions JSON file."""
        actions_file = project_dir / ".verifier" / f"browser_actions_{project_id}_attempt{attempt}.json"
        
        if not actions_file.exists():
            print(f"⚠️ 未找到 browser_actions 文件: {actions_file}")
            return None
        
        try:
            with open(actions_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ 读取 browser_actions 失败: {e}")
            return None
    
    def _load_screenshots(
        self, 
        project_dir: Path, 
        attempt: int
    ) -> List[Dict[str, Any]]:
        """
        Load all screenshots from attempt directory.
        
        Returns:
            List of {"path": str, "base64": str, "index": int}
        """
        screenshots_dir = project_dir / ".verifier" / "agent_screenshots" / f"attempt{attempt}"
        
        if not screenshots_dir.exists():
            print(f"⚠️ 未找到截图目录: {screenshots_dir}")
            return []
        
        screenshots = []
        
        # Find all image files and sort by name
        image_files = sorted(screenshots_dir.glob("screenshot_*.*"))
        
        for i, img_path in enumerate(image_files, 1):
            try:
                with open(img_path, 'rb') as f:
                    img_data = f.read()
                    b64 = base64.b64encode(img_data).decode('utf-8')
                    
                    # Determine image format from file extension
                    ext = img_path.suffix.lower().lstrip('.')
                    if ext == 'jpg':
                        ext = 'jpeg'
                    
                    screenshots.append({
                        "index": i,
                        "path": str(img_path.name),
                        "format": ext,
                        "base64": b64
                    })
            except Exception as e:
                print(f"⚠️ 读取截图失败 {img_path.name}: {e}")
        
        print(f"✅ 加载了 {len(screenshots)} 张截图")
        return screenshots
    
    def _load_code_files(self, project_dir: Path) -> str:
        """
        Load main code file from project directory (typically index.html).
        
        Returns:
            String with complete code file content (no truncation)
        """
        # Common code file patterns to look for (in priority order)
        patterns = [
            "index.html",
            "index.htm", 
            "main.html",
            "app.html",
            "src/index.html",
            "public/index.html"
        ]
        
        # Find the first matching file
        for pattern in patterns:
            file_path = project_dir / pattern
            if file_path.exists():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()  # Read complete file, no truncation
                    
                    return f"{file_path.name}\n```html\n{content}\n```"
                    
                except Exception as e:
                    print(f"⚠️ 读取代码文件失败 {file_path.name}: {e}")
                    return f"{file_path.name}\n(读取失败: {e})"
        
        # Fallback: no file found
        return "(未找到主代码文件)"
    
    def _format_actions_as_json(self, actions: List[Dict[str, Any]]) -> str:
        """
        Format browser actions as JSON string.
        Directly embed the complete JSON array.
        """
        summary = f"## 浏览器操作记录（共{len(actions)}步）\n\n"
        summary += "```json\n"
        summary += json.dumps(actions, indent=2, ensure_ascii=False)
        summary += "\n```\n"
        return summary
    
    def _upload_screenshots_to_tos(self, screenshots: List[Dict[str, Any]], project_dir: Path) -> List[str]:
        """
        Upload screenshots to Volcengine TOS and return URLs.
        
        For now, this is a placeholder. If you have TOS credentials,
        implement the actual upload logic here.
        
        Returns:
            List of public URLs for screenshots
        """
        # TODO: Implement actual TOS upload
        # For now, return empty list to fall back to base64
        return []
    
    def _build_prompt(
        self,
        task_description: str,
        actions: List[Dict[str, Any]],
        screenshots: List[Dict[str, Any]],
        project_dir: Path
    ) -> List[Dict[str, Any]]:
        """
        Build prompt messages with actions and screenshots.
        
        Returns:
            List of message dicts for LLM API
        """
        # Load code files
        code_files = self._load_code_files(project_dir)
        
        # Replace placeholders in template
        prompt_text = self.prompt_template.replace("{task_description}", task_description)
        prompt_text = prompt_text.replace("{code_files}", code_files)
        
        # Build complete actions as JSON (directly embed the JSON array)
        actions_summary = self._format_actions_as_json(actions)
        
        # Build messages list
        content_items = [
            {"type": "input_text", "text": prompt_text + "\n\n" + actions_summary}
        ]
        
        # Add screenshots
        if screenshots:
            content_items.append({
                "type": "input_text",
                "text": f"\n## 截图序列（共{len(screenshots)}张）\n"
            })
            
            # Try to upload to TOS for URL-based access (more efficient)
            screenshot_urls = self._upload_screenshots_to_tos(screenshots, project_dir)
            
            if screenshot_urls:
                # Use URLs (preferred for Volcengine)
                for i, url in enumerate(screenshot_urls, 1):
                    content_items.append({
                        "type": "input_image",
                        "image_url": url
                    })
                    content_items.append({
                        "type": "input_text",
                        "text": f"**截图 {i}**\n"
                    })
            else:
                # Fallback to base64 (works for all providers)
                for screenshot in screenshots:
                    content_items.append({
                        "type": "input_image",
                        "image_url": f"data:image/{screenshot['format']};base64,{screenshot['base64']}"
                    })
                    content_items.append({
                        "type": "input_text",
                        "text": f"**截图 {screenshot['index']}**: {screenshot['path']}\n"
                    })
        
        # Volcengine ARK API format
        messages = [
            {
                "role": "user",
                "content": content_items
            }
        ]
        
        return messages
    
    async def _call_llm(self, messages: List[Dict[str, Any]]) -> str:
        """
        Call LLM API to get evaluation.
        
        Returns:
            Raw response text
        """
        # Build API request based on provider
        if self.api == "volcengine":
            # Volcengine ARK API - uses /responses endpoint with "input" field
            # base_url must be provided in YAML config
            if not self.base_url:
                raise ValueError("base_url is required for volcengine API")
            # Append /responses to base_url
            url = f"{self.base_url.rstrip('/')}/responses"
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            # Volcengine uses "input" instead of "messages"
            # Note: Volcengine API only accepts "model" and "input", no other parameters
            payload = {
                "model": self.model,
                "input": messages
            }
        elif self.api == "openai":
            # OpenAI standard chat completions API
            url = f"{self.base_url}/chat/completions" if self.base_url else "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": messages
                # "temperature": 0.3,
                # "max_tokens": 2000
            }
        else:
            raise ValueError(f"Unsupported API provider: {self.api}")
        
        # Debug: print payload structure
        print(f"🔍 调试信息 - 发送的 payload:")
        print(f"  URL: {url}")
        print(f"  Model: {payload.get('model')}")
        print(f"  Input length: {len(payload.get('input', []))}")
        if payload.get('input') and len(payload['input']) > 0:
            first_msg = payload['input'][0]
            content = first_msg.get('content', [])
            print(f"  Content items: {len(content)}")
            for i, item in enumerate(content[:3], 1):  # 只打印前3个
                item_type = item.get('type', 'unknown')
                print(f"    [{i}] type={item_type}")
                if item_type == 'input_image':
                    img_url = item.get('image_url', '')
                    if isinstance(img_url, str):
                        print(f"        image_url: {img_url[:50]}... (len={len(img_url)})")
                    else:
                        print(f"        ❌ image_url is not string: {type(img_url)}")
        
        # Make request
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                # If bad request, print response body for debugging
                if response.status == 400:
                    error_body = await response.text()
                    print(f"❌ 400 Bad Request 响应体: {error_body[:500]}")
                
                response.raise_for_status()
                result = await response.json()
                
                # Extract content from response (handle both formats)
                if self.api == "volcengine":
                    # Volcengine response format: {"output": [{"type": "message", "content": [...]}]}
                    if "output" in result and isinstance(result["output"], list):
                        # Find the message item in output array
                        for item in result["output"]:
                            if item.get("type") == "message":
                                content = item.get("content", [])
                                # Extract text from content array
                                for content_item in content:
                                    if content_item.get("type") == "output_text":
                                        return content_item.get("text", "")
                        
                        # If no message found, raise error
                        raise ValueError(f"No message found in Volcengine response output")
                    else:
                        raise ValueError(f"Unexpected Volcengine API response format: {result}")
                else:
                    # OpenAI response format: {"choices": [{"message": {"content": "..."}}]}
                    if "choices" in result and len(result["choices"]) > 0:
                        return result["choices"][0]["message"]["content"]
                    else:
                        raise ValueError(f"Unexpected API response format: {result}")
    
    def _parse_evaluation(self, llm_response: str) -> Dict[str, Any]:
        """
        Parse evaluation JSON from LLM response.
        
        Returns:
            Dict with functional_score, functional_reason, aesthetics_score, aesthetics_reason
        """
        # Try to extract JSON from response
        import re
        
        # Try markdown code block first
        json_pattern = r'```json\s*\n(.*?)\n```'
        matches = re.findall(json_pattern, llm_response, re.DOTALL)
        
        if matches:
            try:
                return json.loads(matches[0].strip())
            except Exception:
                pass
        
        # Try raw JSON
        try:
            return json.loads(llm_response.strip())
        except Exception:
            pass
        
        # Fallback: return error
        return {
            "functional_score": -1.0,
            "functional_reason": f"⚠️ 无法解析LLM响应: {llm_response[:200]}",
            "aesthetics_score": -1.0,
            "aesthetics_reason": f"⚠️ 无法解析LLM响应: {llm_response[:200]}"
        }
    
    async def evaluate(
        self,
        project_dir: Path,
        project_id: str,
        task_description: str,
        max_retries: int
    ) -> Dict[str, Any]:
        """
        Perform fallback evaluation using LLM.
        
        Args:
            project_dir: Project directory path
            project_id: Project identifier
            task_description: Original task description
            max_retries: Number of retries that were attempted
            
        Returns:
            Evaluation result dict
        """
        print(f"\n{'='*60}")
        print(f"🔄 启动兜底评估机制 (Fallback Evaluation)")
        print(f"{'='*60}\n")
        
        # 1. Find best attempt
        best_attempt, history_data = self._find_best_attempt(project_dir, project_id, max_retries)
        
        if best_attempt is None:
            return {
                "functional_score": -1.0,
                "functional_reason": "⚠️ 未找到任何有效的测试记录",
                "aesthetics_score": -1.0,
                "aesthetics_reason": "⚠️ 未找到任何有效的测试记录",
                "error": "No valid attempts found for fallback evaluation",
                "fallback_used": True,
                "best_attempt": None
            }
        
        # 2. Load browser actions
        actions = self._load_browser_actions(project_dir, project_id, best_attempt)
        
        if not actions:
            return {
                "functional_score": -1.0,
                "functional_reason": "⚠️ 未找到浏览器操作记录",
                "aesthetics_score": -1.0,
                "aesthetics_reason": "⚠️ 未找到浏览器操作记录",
                "error": "No browser actions found",
                "fallback_used": True,
                "best_attempt": best_attempt
            }
        
        # 3. Load screenshots
        screenshots = self._load_screenshots(project_dir, best_attempt)
        
        if not screenshots:
            print("⚠️ 未找到截图，将仅基于操作记录评估")
        
        # 4. Build prompt
        messages = self._build_prompt(task_description, actions, screenshots, project_dir)
        
        # 5. Call LLM
        try:
            print(f"📞 调用 LLM ({self.api}/{self.model}) 进行评估...")
            llm_response = await self._call_llm(messages)
            print(f"✅ LLM 响应成功")
            
            # 6. Parse evaluation
            evaluation = self._parse_evaluation(llm_response)
            
            # 7. Add metadata
            evaluation["fallback_used"] = True
            evaluation["best_attempt"] = best_attempt
            evaluation["llm_raw_response"] = llm_response
            evaluation["actions_count"] = len(actions)
            evaluation["screenshots_count"] = len(screenshots)
            evaluation["task_description"] = task_description
            
            # 8. Save fallback evaluation result with input messages
            self._save_fallback_result(project_dir, project_id, best_attempt, evaluation, messages)
            
            print(f"\n{'='*60}")
            print(f"✅ 兜底评估完成")
            print(f"  功能性得分: {evaluation.get('functional_score')}")
            print(f"  美观性得分: {evaluation.get('aesthetics_score')}")
            print(f"{'='*60}\n")
            
            return evaluation
            
        except Exception as e:
            print(f"❌ 兜底评估失败: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "functional_score": -1.0,
                "functional_reason": f"⚠️ 兜底评估异常: {str(e)}",
                "aesthetics_score": -1.0,
                "aesthetics_reason": f"⚠️ 兜底评估异常: {str(e)}",
                "error": str(e),
                "fallback_used": True,
                "best_attempt": best_attempt
            }
    
    def _save_fallback_result(
        self,
        project_dir: Path,
        project_id: str,
        attempt: int,
        evaluation: Dict[str, Any],
        input_messages: List[Dict[str, Any]] = None
    ):
        """Save fallback evaluation result to .verifier directory."""
        verifier_dir = project_dir / ".verifier"
        verifier_dir.mkdir(exist_ok=True)
        
        # Save evaluation result
        result_file = verifier_dir / f"fallback_evaluation_{project_id}_attempt{attempt}.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(evaluation, f, indent=2, ensure_ascii=False)
        print(f"📄 兜底评估结果已保存: {result_file}")
        
        # Save complete input/output for debugging
        if input_messages:
            io_log = {
                "timestamp": datetime.now().isoformat(),
                "project_id": project_id,
                "attempt": attempt,
                "input": {
                    "messages": input_messages,
                    "task_description": evaluation.get("task_description", ""),
                    "actions_count": evaluation.get("actions_count", 0),
                    "screenshots_count": evaluation.get("screenshots_count", 0)
                },
                "output": {
                    "functional_score": evaluation.get("functional_score"),
                    "functional_reason": evaluation.get("functional_reason"),
                    "aesthetics_score": evaluation.get("aesthetics_score"),
                    "aesthetics_reason": evaluation.get("aesthetics_reason"),
                    "raw_response": evaluation.get("llm_raw_response", "")
                },
                "metadata": {
                    "model": self.model,
                    "api": self.api,
                    "fallback_used": True
                }
            }
            
            io_file = verifier_dir / f"fallback_io_log_{project_id}_attempt{attempt}.json"
            with open(io_file, 'w', encoding='utf-8') as f:
                json.dump(io_log, f, indent=2, ensure_ascii=False)
            print(f"📄 兜底输入输出日志已保存: {io_file}")