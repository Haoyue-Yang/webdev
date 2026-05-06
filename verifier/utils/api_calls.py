"""
API calls module for the model iteration project that handles interactions with various LLM providers.

This module provides functionality for:
1. Making API calls to different LLM providers (Anthropic, OpenAI, EB)
2. Handling retries and error cases
3. Converting between different message formats
4. Rotating between multiple OpenAI-compatible API endpoints

Key functions:
- call_llm_api: Main interface for making LLM API calls
- _call_anthropic: Anthropic-specific API implementation
- _call_openai_like: OpenAI-compatible API implementation
- _call_eb: EB-specific API implementation

Classes:
- OpenAIRotator: Manages rotation between multiple OpenAI API endpoints
  using round-robin or random strategies
"""

import requests
import random
import copy
import time
import functools
import threading
import traceback
import json
from json import JSONDecodeError
from typing import Dict, Any, List, Optional, Union
from urllib3.connection import HTTPConnection
from requests.adapters import HTTPAdapter
import socket
import base64
import datetime
from pathlib import Path

from .utils import display_timer, save_json
from .log_utils import log_wrapper, get_log_filepath
from .constants import CONTINUE_PROMPT, SYSTEM_PROMPT
from .config import config

# In a real implementation, these would be properly imported
# Stub implementations to maintain compatibility
class StepRecordManager:
    @staticmethod
    def save_record(*args, **kwargs):
        pass

# Metrics counter for tracking exceptions
class Counter:
    def add(self, value, labels=None):
        pass

exception_counter = Counter()

# Define function to save token usage
def save_usage(token_usage):
    """
    Save token usage statistics
    
    Args:
        token_usage: Dictionary with token usage information
    """
    log_wrapper.info(f"Token usage: {token_usage}")
    # Implement actual saving logic if needed

class OpenAIRotator:
    """
    A class to manage rotation among multiple OpenAI compatible endpoints.
    """
    
    def __init__(self):
        """Initialize the OpenAI endpoint rotator."""
        self.endpoints = []
        self.rotation_strategy = "round_robin"  # Default strategy
        self.current_index = 0
        self._load_endpoints()
    
    def _load_endpoints(self):
        """Load endpoint configurations from global config."""
        # Get endpoints from config
        endpoints_str = config.get("api", {}).get("openai_endpoints", "[]")
        
        # Parse the endpoints if they're a string
        if isinstance(endpoints_str, str):
            try:
                self.endpoints = json.loads(endpoints_str)
            except json.JSONDecodeError:
                log_wrapper.error(f"Failed to parse OpenAI endpoints: {endpoints_str}")
                self.endpoints = []
        else:
            # If already a list, use directly
            self.endpoints = endpoints_str if isinstance(endpoints_str, list) else []
        
        # Get rotation strategy
        self.rotation_strategy = config.get("api", {}).get("openai_rotation_strategy", "round_robin")
        
        # Log the loaded configuration
        enabled_endpoints = [ep for ep in self.endpoints if ep.get("enabled", True)]
        log_wrapper.info(f"Loaded {len(enabled_endpoints)} enabled OpenAI endpoints out of {len(self.endpoints)} total")
        log_wrapper.info(f"Using rotation strategy: {self.rotation_strategy}")
    
    def get_api_config(self) -> Dict[str, str]:
        """
        Get the next API configuration according to the rotation strategy.
        
        Returns:
            Dict containing api_key and base_url
        """
        # Filter enabled endpoints
        enabled_endpoints = [ep for ep in self.endpoints if ep.get("enabled", True)]
        
        # If no enabled endpoints, return empty config
        if not enabled_endpoints:
            log_wrapper.warning("No enabled endpoints found, using fallback endpoint")
            return {
                "api_key": config.get("api", {}).get("openai_api_key", ""),
                "base_url": config.get("api", {}).get("openai_base_url", ""),
                "name": "fallback"
            }
        
        # Choose an endpoint based on strategy
        if self.rotation_strategy == "random":
            endpoint = random.choice(enabled_endpoints)
        else:  # Default to round_robin
            # Get the next endpoint
            endpoint = enabled_endpoints[self.current_index % len(enabled_endpoints)]
            # Increment for next time
            self.current_index += 1
        
        return {
            "api_key": endpoint.get("api_key", ""),
            "base_url": endpoint.get("base_url", ""),
            "name": endpoint.get("name", "unnamed")
        }

def log_conversation(
        messages,
        response,
        api,
        model,
        max_tokens,
        temperature,
        timeout,
        query_type,
        system_message,
        root_path,
        top_p: float = None,
):
    """
    Log full conversation data

    Args:
        messages (list): Conversation messages
        response (str): Assistant response
        api (str): API type
        model (str): Model name
        max_tokens (int): Maximum tokens
        temperature (float): Temperature
        timeout (float): Timeout
        query_type (str): Query type: "user" or internal step name
        system_message (str): System message
        log_path (str): Log path
        write_to_db (bool): Whether to write to database
    """
    # Get online_mode from config when needed
    online_mode = config.get("online_mode", True)

    if not online_mode:
        save_json(
            {
                "messages": messages,
                "response": response,
                "api": api,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout": timeout,
                "query_type": query_type,
                "system_message": system_message,
            },
            get_log_filepath(root_path),
        )


class TCPKeepAliveAdapter(HTTPAdapter):
    """自定义 HTTPAdapter 以设置 Keep-Alive 参数"""

    def init_poolmanager(self, *args, **kwargs):
        """初始化连接池管理器"""
        # 检查系统支持的 Keep-Alive 参数并应用
        socket_options = HTTPConnection.default_socket_options + [
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),  # 启用 Keep-Alive
        ]
        # macOS 也支持 TCP_KEEPALIVE（与 TCP_KEEPIDLE 等价）
        if hasattr(socket, "TCP_KEEPALIVE"):
            socket_options.append(
                (socket.SOL_TCP, socket.TCP_KEEPALIVE, 45)
            )  # 设置空闲时间为 45 秒
        if hasattr(socket, "TCP_KEEPINTVL"):
            socket_options.append(
                (socket.SOL_TCP, socket.TCP_KEEPINTVL, 10)
            )  # 每隔 10 秒发送一次 Keep-Alive 包
        if hasattr(socket, "TCP_KEEPCNT"):
            socket_options.append(
                (socket.SOL_TCP, socket.TCP_KEEPCNT, 6)
            )  # 最多发送 6 次探测包

        kwargs["socket_options"] = socket_options
        super(TCPKeepAliveAdapter, self).init_poolmanager(*args, **kwargs)


def prompt_caching_process(message: Optional[Union[str, List[Dict[str, str]]]]):
    """
    add special process for prompt caching
    """
    new_messages = []
    if isinstance(message, str):
        new_messages.append({
            "type": "text",
            "text": message,
            "cache_control": {"type": "ephemeral"}
        })
    elif isinstance(message, list):
        limit = 3
        # because only define up to 4 cache block, so just use top4
        for idx, msg in enumerate(message):
            new_msg = copy.deepcopy(msg)
            if idx < limit:
                new_msg["content"] = [
                    {
                        "type": "text",
                        "text": msg["content"],
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
            else:
                new_msg["content"] = [
                    {
                        "type": "text",
                        "text": msg["content"],
                    }
                ]
            new_messages.append(new_msg)
    return new_messages


def _save_llm_interaction(
    verifier_dir: Path,
    messages: List[Dict[str, str]],
    system_message: str,
    response: str,
    api: str,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
    raw_response: Dict[str, Any] = None
) -> Dict[str, str]:
    """
    Save LLM interaction data to .verifier directory.
    Replaces base64 image data with image path for readability.

    Args:
        verifier_dir: Directory to save the interaction data
        messages: List of messages sent to the API
        system_message: System message sent to the API
        response: Extracted response content
        api: API type (anthropic, openai, etc.)
        model: Model name
        max_tokens: Max tokens setting
        temperature: Temperature setting
        timeout: Timeout setting
        raw_response: Full raw response from API including usage data

    Returns:
        Dict with paths to saved files
    """
    verifier_dir = Path(verifier_dir)
    verifier_dir.mkdir(exist_ok=True)

    timestamp = datetime.datetime.now().isoformat()

    # Find screenshot file in the verifier directory
    screenshot_path = None
    for file_path in verifier_dir.glob("screenshot_*.png"):
        screenshot_path = str(file_path)
        break

    if not screenshot_path:
        screenshot_path = "screenshot_not_found.png"

    # Process messages to replace base64 image data with paths
    processed_messages = []
    for msg in messages:
        processed_msg = msg.copy()
        if isinstance(msg.get('content'), list):
            processed_content = []
            for item in msg['content']:
                processed_item = item.copy()  # Always copy the item first

                if item.get('type') == 'image' and 'source' in item:
                    # Anthropic format - replace base64 data with screenshot path
                    processed_item['source'] = {
                        **item['source'],
                        'data': f"<replaced_with_screenshot_path: {screenshot_path}>"
                    }
                elif item.get('type') == 'image_url' and 'image_url' in item:
                    # OpenAI format - replace base64 data with screenshot path
                    processed_item['image_url'] = {
                        'url': f"<replaced_with_screenshot_path: {screenshot_path}>"
                    }

                processed_content.append(processed_item)
            processed_msg['content'] = processed_content
        processed_messages.append(processed_msg)

    # Prepare payload data
    payload_data = {
        "timestamp": timestamp,
        "api": api,
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": timeout,
        "system_message": system_message,
        "messages": processed_messages
    }

    # Prepare response data with usage info
    response_data = {
        "timestamp": timestamp,
        "api": api,
        "model": model,
        "response": response
    }

    # Extract and add usage data if raw_response is available
    if raw_response:
        usage = raw_response.get('usage', {})
        response_data["usage"] = usage
        # Also save full raw response for debugging
        response_data["raw_response"] = raw_response

    # Generate filesystem-safe timestamp for filenames (avoids overwriting on repeated calls)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:22]  # up to microseconds, 22 chars

    # Save payload
    payload_file = verifier_dir / f"llm_payload_{ts}.json"
    with open(payload_file, 'w', encoding='utf-8') as f:
        json.dump(payload_data, f, indent=2, ensure_ascii=False)

    # Save response
    response_file = verifier_dir / f"llm_response_{ts}.json"
    with open(response_file, 'w', encoding='utf-8') as f:
        json.dump(response_data, f, indent=2, ensure_ascii=False)

    print(f"LLM interaction saved to {verifier_dir} (ts={ts})")

    return {
        "payload_file": str(payload_file),
        "response_file": str(response_file)
    }


def call_llm_api(
        api_config: Optional[Dict[str, str]],
        messages: List[Dict[str, str]],
        system_message: str = None,
        model: str = None,
        api: str = "anthropic",
        max_tokens: int = 0,
        temperature: float = 0.5,
        timeout: int = 60 * 15,
        max_retries: int = 10,
        root_path: str = None,
        query_type: str = "user",
        save_to_verifier_dir: str = None,  # New parameter for .verifier directory path
        stream: bool = False,  # New parameter for streaming
        **kwargs
) -> str:
    """Call LLM API with the given messages"""
    if config["api"]["print_api_model"]:
        log_wrapper.info(f"Using API: {api} with model: {model}")
    
    
    if max_tokens == 0:
        # Get config safely with a default value
        api_config_dict = config.get("api", {})
        max_tokens = api_config_dict.get("max_tokens", 8192)
    if api == "eb":
        max_tokens = min(8000, max_tokens)
    if api == "anthropic":
        model = model or "claude-3-5-sonnet-20241022"
        max_tokens = min(max_tokens, 8192)

    if api_config is None:
        # Use the processed config from adaagent.common.config
        api_config = {
            "anthropic": {
                "api_key": config.get("api", {}).get("anthropic_api_key"),
                "base_url": config.get("api", {}).get("anthropic_base_url"),
            },
            "eb": {"base_url": config.get("api", {}).get("eb_base_url")},
            "openai": {
                "api_key": config.get("api", {}).get("openai_api_key"),
                "base_url": config.get("api", {}).get("openai_base_url"),
            },
        }[api]

    api_functions = {
        "anthropic": _call_anthropic,
        "eb": _call_eb,
        "openai": _call_openai_like,
    }

    if api not in api_functions:
        raise ValueError(f"Unsupported API type: {api}")

    # Start timer display
    if not config.get("close_display_timer", True):
        timer_stop = threading.Event()
        timer_thread = threading.Thread(target=display_timer, args=(timer_stop,))
        timer_thread.start()

    try:
        start_time = time.time()
        for attempt in range(max_retries):
            try:
                response_data = api_functions[api](
                    api_config,
                    messages,
                    system_message,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    stream=stream,
                    **kwargs,
                )
                if root_path:
                    log_conversation(
                        messages,
                        extract_response(api, response_data),
                        api,
                        model,
                        max_tokens,
                        temperature,
                        timeout,
                        query_type,
                        system_message,
                        root_path,
                    )

                if response_data:
                    elapsed_time = time.time() - start_time
                    log_wrapper.info(f"\nAPI call completed in {elapsed_time:.1f} seconds")
                    response_content = extract_response(api, response_data).strip()
                    messages.append({"role": "assistant", "content": response_content})
                    _save_usage(response_data, api)
                else:
                    log_wrapper.info("Getting None response, retrying")
                    continue

                # Handle continuation for all API types when max tokens is reached
                last_response = response_content
                
                # Check if max tokens was reached based on API type
                continue_condition = False
                if api == "anthropic":
                    continue_condition = "stop_reason" in response_data \
                        and response_data["stop_reason"] == "max_tokens"
                elif api == "openai":
                    continue_condition = response_data.get('choices', [{}])[0]\
                        .get('finish_reason', None) == "length"
                
                # Continue generating if needed
                while continue_condition:
                    log_wrapper.info("Max tokens reached, continue to the next round")
                    prefill_content = response_content[max(0, len(response_content) - 100):]
                    messages.append({"role": "user", "content": CONTINUE_PROMPT})
                    messages.append({"role": "assistant", "content": prefill_content})
                    
                    response_data = api_functions[api](
                        api_config,
                        messages,
                        system_message,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=timeout,
                        stream=stream,
                        **kwargs,
                    )
                    messages.pop() # pop prefill message
                    last_response = extract_response(api, response_data).strip()
                    messages.append({"role": "assistant", "content": prefill_content + last_response})
                    
                    if root_path:
                        log_conversation(
                            messages,
                            last_response,
                            api,
                            model,
                            max_tokens,
                            temperature,
                            timeout,
                            query_type,
                            system_message,
                            root_path,
                        )
                    
                    response_content += last_response
                    
                    # Update the continue condition for the next iteration
                    continue_condition = False
                    if api == "anthropic":
                        continue_condition = "stop_reason" in response_data \
                            and response_data["stop_reason"] == "max_tokens"
                    elif api == "openai":
                        continue_condition = response_data.get('choices', [{}])[0]\
                            .get('finish_reason', None) == "length"
                
                if save_to_verifier_dir:
                    _save_llm_interaction(
                        save_to_verifier_dir,
                        messages,
                        system_message,
                        response_content,
                        api,
                        model,
                        max_tokens,
                        temperature,
                        timeout,
                        raw_response=response_data
                    )

                return response_content
            except Exception as e:
                # Print response details for debugging
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Response status: {e.response.status_code}")
                    safe_headers = {k: ("***" if k.lower() in ("authorization", "x-api-key", "api-key") else v) for k, v in dict(e.response.headers).items()}
                    print(f"Response headers: {safe_headers}")
                    try:
                        print(f"Response JSON: {e.response.json()}")
                    except:
                        print(f"Response text: {e.response.text}")
                else:
                    print(f"Exception without response: {type(e).__name__}: {e}")
                
                if isinstance(e, JSONDecodeError):
                    log_wrapper.error(f"JSONDecodeError: {e}")
                    if hasattr(e, 'doc') and e.doc:
                        # Log first 200 chars to avoid huge logs
                        log_wrapper.error(f"Invalid JSON content: {e.doc[:200]}...")
                    log_wrapper.error(f"Error: {traceback.format_exc()}")
                
                # Track exception for each retry with API details
                exception_counter.add(1, {
                    "type": f"{api}-{model}-base_url:\
                        {api_config.get('base_url', 'unknown') if api_config else 'unknown'}",
                })
                
                if attempt == max_retries - 1:
                    log_wrapper.error(f"API call failed (attempt {attempt + 1}/{max_retries})."
                                    #   f"messages: {messages}"
                                    #   f"system_message: {system_message if config.get('online_mode', True) else ''}"
                                      f"model: {model}\t"
                                      f"max_tokens: {max_tokens}\t"
                                      f"temperature: {temperature}\t"
                                      f"timeout: {timeout}\t"
                                      f"Error: {e}")
                    raise e

                wait_time = (
                    90 + random.randint(-29, 29)
                    if attempt == max_retries - 2
                    else 4 ** (attempt + 3)
                )
                log_wrapper.info(
                    f"API call failed (attempt {attempt + 1}/{max_retries}). Retrying after {wait_time} seconds..."
                )
                log_wrapper.info(f"Error: {traceback.format_exc()}")
                time.sleep(wait_time)

        # Save payload files even when API call fails
        if save_to_verifier_dir:
            _save_llm_interaction(
                save_to_verifier_dir,
                messages,
                system_message,
                None,  # No response since API failed
                api,
                model,
                max_tokens,
                temperature,
                timeout
            )
        
        return None

    finally:
        # Always stop the timer
        if not config.get("close_display_timer", True):
            timer_stop.set()
            timer_thread.join()
        pass


def _call_anthropic(
        api_config: Dict[str, str],
        messages: List[Dict[str, str]],
        system_message: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 8000,
        temperature: float = 0.5,
        timeout: int = 180,
        conversation_id: str = None,
        turn_round: int = 0,
        stream: bool = False,
        **kwargs
) -> Dict[str, Any]:
    """Make API call to Anthropic's Claude API"""
    headers = {
        "x-api-key": api_config["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    if config["api"]["prompt_caching"]:
        try:
            system_message_, messages_ = system_message, messages
            if system_message:
                system_message = prompt_caching_process(system_message)
            if messages:
                messages = prompt_caching_process(messages)
        except Exception as e:
            log_wrapper.error(f"error in prompt caching:{traceback.format_exc()}\n exception: {e}")

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "temperature": temperature,
    }
    
    # Add streaming if enabled
    if stream:
        payload["stream"] = True
    
    # Only add system field if system_message is provided
    if system_message:
        payload["system"] = system_message
    url = f"{api_config['base_url']}/v1/messages"
    
    # Create a session with keep-alive and proxy settings
    session = requests.Session()
    session.mount("http://", TCPKeepAliveAdapter())
    session.mount("https://", TCPKeepAliveAdapter())

    # Setup proxies - prefer claude-specific proxy if configured, fall back to general http_proxy
    claude_proxy = config["api"].get("claude_proxy", "")
    http_proxy = config["api"].get("http_proxy", "")
    
    # Use the appropriate proxy
    proxy_url = claude_proxy or http_proxy
    
    if proxy_url:
        try:
            log_wrapper.info(f"Using proxy for Claude API call: {proxy_url}")
            
            # Set up proxies dictionary
            proxies = {
                "http": proxy_url,
                "https": proxy_url
            }
            
            # Make API call with proxy
            response = session.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
                proxies=proxies,
                stream=stream,
            )
            
        except requests.exceptions.RequestException as e:
            log_wrapper.error(f"Proxy connection failed: {e}")
            log_wrapper.error("Attempting direct connection without proxy...")
            response = session.post(url, headers=headers, json=payload, timeout=timeout, stream=stream)
    else:
        # Make direct API call without proxy
        response = session.post(url, headers=headers, json=payload, timeout=timeout, stream=stream)

    response.raise_for_status()
    
    if stream:
        # Handle streaming response
        content = ""
        try:
            for line in response.iter_lines(decode_unicode=True):
                if line:
                    if line.startswith("data: "):
                        data_str = line[6:]  # Remove "data: " prefix
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                            if chunk_data.get("type") == "content_block_delta":
                                delta = chunk_data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    content += text
                                    if config["api"].get("print_stream", False):
                                        print(text, end="", flush=True)
                        except json.JSONDecodeError:
                            continue
            
            # Return a response-like structure for compatibility
            return {
                "content": [{"text": content}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0}  # Placeholder usage
            }
        except Exception as e:
            log_wrapper.error(f"Error processing streaming response: {e}")
            # Fall back to non-streaming
            response = session.post(url, headers=headers, json={**payload, "stream": False}, timeout=timeout, proxies=proxies if proxy_url else None)
            response.raise_for_status()
            resp_json = response.json()
    else:
        resp_json = response.json()
    
    if "error" in resp_json:
        log_wrapper.error(f"Error in Claude API response: {resp_json}")
        raise Exception(f"Error response: {resp_json}")
    return resp_json


def _save_usage(resp, api_function):
    try:
        log_wrapper.info(f"api usage:{resp.get('usage', {})}")
        usage = resp.get('usage', {})

        if api_function == "anthropic":
            token_usage = {
                "prompt_tokens": usage.get('input_tokens', 0),
                "completion_tokens": usage.get('output_tokens', 0),
                "total_tokens": usage.get('input_tokens', 0) + usage.get('output_tokens', 0)
            }
        elif api_function == "openai":
            token_usage = {
                "prompt_tokens": usage.get('prompt_tokens', 0),
                "completion_tokens": usage.get('completion_tokens', 0),
                "total_tokens": usage.get('total_tokens', 0),
            }
        else:
            return
        save_usage(token_usage)
    except:
        pass


def process_thinking_content(content):
    """特殊推理token内容"""
    if "</think>" in content:
        think_content, new_content = content.rsplit("</think>", 1)
    else:
        think_content, new_content = "", content
    # R1特殊处理patch
    if new_content.count("&lt;") >= 4:
        new_content = new_content.replace("&lt;", "<")
    if new_content.count("&rt;") >= 4:
        new_content = new_content.replace("&rt;", ">")
    return new_content.strip()


def extract_response(api: str, response_data: Dict) -> str:
    """
    Extract the response text from different API response formats.

    Args:
        api (str): The API provider name
        response_data (Dict): Raw API response data

    Returns:
        str: Extracted response text

    Raises:
        ValueError: If unsupported API is specified

    Example:
        >>> response_data = {"content": [{"text": "Hello"}]}
        >>> text = extract_response("anthropic", response_data)
    """
    try:
        if api == "anthropic":
            return response_data["content"][0]["text"]
        elif api == "openai":
            return process_thinking_content(response_data["choices"][0]["message"]["content"])
        elif api == "eb":
            return response_data.get("data", {}).get("result", "")
        raise ValueError(f"Unsupported API: {api}")
    except Exception as e:
        log_wrapper.error(f"Error extracting response: {e}")
        log_wrapper.error(f"Response data: {response_data}")
        raise e

def _call_openai_like(
        api_config: Dict[str, str],
        messages: List[Dict[str, str]],
        system_message: Optional[str] = None,
        model: str = None,
        max_tokens: int = 8192,
        temperature: float = 0.5,
        timeout: int = 180,
        stream: bool = False,
        **kwargs
) -> Dict:
    """
    Call the OpenAI API or compatible API endpoint.
    
    Args:
        api_config: API configuration containing api_key and base_url
        messages: List of conversation messages
        system_message: System prompt
        model: Model name
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        timeout: Request timeout in seconds
        stream: Whether to stream the response
        
    Returns:
        Response data from API
    """
    try:
        rotation_enabled = config.get("api", {}).get("openai_rotation_enabled", False)
        
        # Determine which API config to use
        if rotation_enabled:
            # Use the OpenAIRotator if rotation is enabled
            rotator = OpenAIRotator()
            endpoint_config = rotator.get_api_config()
            api_key = endpoint_config.get("api_key", api_config.get("api_key", ""))
            base_url = endpoint_config.get("base_url", api_config.get("base_url", ""))
            endpoint_name = endpoint_config.get("name", "unnamed")
            log_wrapper.info(f"Using OpenAI endpoint: {endpoint_name} with URL: {base_url}")
        else:
            # Use the provided API config
            api_key = api_config.get("api_key", "")
            base_url = api_config.get("base_url", "")
        
        # Validate base_url
        if not base_url:
            raise ValueError("OpenAI base URL is empty. Please set a valid base_url in the configuration.")
        
        # Ensure base_url has a scheme
        if not base_url.startswith(('http://', 'https://')):
            base_url = 'https://' + base_url
        
        # Prepare the request payload
        payload = {
            "model": model,
            "messages": [],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        # Add streaming if enabled
        if stream:
            payload["stream"] = True
        
        # Add json_mode if provided in kwargs
        if kwargs.get('json_mode', False):
            payload["response_format"] = {"type": "json_object"}
        
        # Add system message if provided
        if system_message:
            payload["messages"].append({"role": "system", "content": system_message})
        
        # Add conversation messages
        payload["messages"].extend(messages)
        
        # Set up headers
        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        # Set up proxies if configured
        proxies = None
        http_proxy = config.get("api", {}).get("http_proxy", "")
        if http_proxy:
            log_wrapper.info(f"Using HTTP proxy for OpenAI API call: {http_proxy}")
            proxies = {
                "http": http_proxy,
                "https": http_proxy
            }
        
        # Create a session with keep-alive
        session = requests.Session()
        session.mount("http://", TCPKeepAliveAdapter())
        # session.mount("https://", TCPKeepAliveAdapter())
        
        # Make the API call
        response = session.post(
            base_url,
            json=payload,
            headers=headers,
            timeout=timeout,
            proxies=proxies,
            stream=stream
        )
        
        # Handle error responses
        if response.status_code != 200:
            error_msg = f"HTTP {response.status_code}"
            try:
                error_data = response.json()
                if "error" in error_data:
                    error_msg = f"{error_msg}: {error_data['error'].get('message', 'Unknown error')}"
            except:
                error_msg = f"{error_msg}: {response.text[:100]}..."
            masked_key = f"{api_key[:8]}...{api_key[-4:]}" if api_key and len(api_key) > 12 else "(empty)"
            masked_headers = {k: ("***" if k.lower() in ("authorization", "x-api-key", "api-key") else v) for k, v in headers.items()} if headers else {}
            log_wrapper.error(f"Error calling OpenAI API: headers: {masked_headers}")
            log_wrapper.error(f"Error calling OpenAI API: payload: {str(payload)[:1000]}...")
            log_wrapper.error(f"Error calling OpenAI API: base_url:{base_url} api_key:{masked_key}")
            log_wrapper.error(f"Error calling OpenAI API: {error_msg}")
            log_wrapper.error(f"Error calling OpenAI API: [response_data] - {response.text[:1500]}")
            return {}
        
        if stream:
            # Handle streaming response
            content = ""
            try:
                for line in response.iter_lines(decode_unicode=True):
                    if line:
                        if line.startswith("data: "):
                            data_str = line[6:]  # Remove "data: " prefix
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_str)
                                if chunk_data.get("choices") and len(chunk_data["choices"]) > 0:
                                    delta = chunk_data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        text = delta["content"]
                                        content += text
                                        if config["api"].get("print_stream", False):
                                            print(text, end="", flush=True)
                            except json.JSONDecodeError:
                                continue
                
                # Return a response-like structure for compatibility
                return {
                    "choices": [{
                        "message": {"content": content},
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}  # Placeholder usage
                }
            except Exception as e:
                log_wrapper.error(f"Error processing streaming response: {e}")
                # Fall back to non-streaming
                response = session.post(
                    base_url,
                    json={**payload, "stream": False},
                    headers=headers,
                    timeout=timeout,
                    proxies=proxies
                )
                if response.status_code != 200:
                    return {}
                return response.json()
        
        # Parse and return response
        return response.json()
        
    except Exception as e:
        log_wrapper.error(f"Error calling OpenAI API: {str(e)}")
        log_wrapper.error(f"Error calling OpenAI API: {traceback.format_exc()}")
        log_wrapper.error(f"Error calling OpenAI API: [response_data] - {{}}")
        return {}


def _call_eb(
        api_config: Dict[str, str],
        messages: List[Dict[str, str]],
        system_message: Optional[str] = None,
        model: str = None,
        max_tokens: int = 8192,
        temperature: float = 0.5,
        timeout: int = 180,
        stream: bool = False,
        **kwargs
) -> str:
    """
    Internal function for making calls to the EB API.

    Args:
        api_config: Configuration dictionary with base URL
        messages: List of conversation messages
        system_message: System prompt for the model
        model: Model identifier
        max_tokens: Maximum response length
        temperature: Sampling temperature
        timeout: Request timeout in seconds
        stream: Whether to stream the response (EB may not support streaming)

    Returns:
        str: Raw API response data

    Raises:
        requests.exceptions.RequestException: For API call failures
    """
    log_wrapper.info(f">>>>>request eb:{model}")
    history = convert_messages_to_eb_history(messages[:-1])
    prompt = messages[-1]["content"]

    if not history and system_message:
        prompt = f"""Here is the system prompt: 
{system_message}
---
Below is the user prompt. Please fulfill the user's request based on the system prompt above: {prompt}"""
    elif not history:
        prompt = prompt  # Just use the prompt as is
    else:
        prompt = f"Here is the user prompt. \
            Please fulfill the user's request based on the system prompt above: {prompt}"

    headers = {"Content-Type": "application/json"}
    session_id = str(int(time.time() * 1000))

    payload = {
        "text": prompt,
        "history": history,
        "session_id": session_id,
        "userId": "2513059349",
        "model_id": model,
        "max_output_tokens": 8192,
        "model_max_input_tokens": "128k",
        "temperature": temperature,
    }
    
    # Note: EB API may not support streaming, but we add the parameter for consistency
    if stream:
        log_wrapper.warning("EB API may not support streaming. Falling back to non-streaming mode.")
    
    # Create a session with keep-alive
    session = requests.Session()
    session.mount("http://", TCPKeepAliveAdapter())
    session.mount("https://", TCPKeepAliveAdapter())
    
    # Set up proxies if configured
    proxies = None
    http_proxy = config.get("api", {}).get("http_proxy", "")
    if http_proxy:
        log_wrapper.info(f"Using HTTP proxy for EB API call: {http_proxy}")
        proxies = {
            "http": http_proxy,
            "https": http_proxy
        }
    
    # Make the API call
    response = session.post(
        api_config["base_url"],
        json=payload,
        headers=headers,
        timeout=1800,  # Use the longer timeout for EB
        proxies=proxies
    )
    
    response.raise_for_status()
    return response.json()


def convert_messages_to_eb_history(messages):
    """
    Convert standard message format to EB history format.

    Args:
        messages: List of messages in standard format

    Returns:
        List[List[str]]: Messages in EB format [[Query2, Response2], [Query1, Response1]]
    """
    history = []
    # Skip the last message as it will be the current query
    for i in range(len(messages) - 1, 0, -2):
        if i - 1 >= 0:  # Ensure we have both query and response
            query = messages[i - 1]["content"]
            response = messages[i]["content"]
            history.append([query, response])
    return history


if __name__ == "__main__":
    print("Running tests for Claude and EB APIs...")
    
    # Check if proxies are enabled
    http_proxy = config.get("api", {}).get("http_proxy", "")
    claude_proxy = config.get("api", {}).get("claude_proxy", "")
    
    print(f"HTTP Proxy Configuration:")
    print(f"- General HTTP Proxy: {'Enabled: ' + http_proxy if http_proxy else 'Disabled'}")
    print(f"- Claude-specific Proxy: {'Enabled: ' + claude_proxy if claude_proxy else 'Disabled'}")
    
    test_models = [
        ("anthropic", "claude-3-5-sonnet-20241022"),
        ("eb", "EB_SPV3_128K_V4T04_V111_code"),
        ("coze", "7397053977973522440"),
        ("openai", "deepseek-r1")
    ]

    # Test messages
    test_messages = [{"role": "user", "content": "how many charactor 'r' in worry"}]
    test_system = "You are a helpful AI assistant. Be concise."

    # Run tests for each model
    for api, model in test_models:
        print(f"\nTesting {api} with model {model}")
        try:
            response = call_llm_api(
                api_config=None,
                messages=test_messages,
                system_message=test_system,
                api=api,
                model=model,
                temperature=0.0,
                max_tokens=100,
            )
            print(f"Response received: {response}")
            print("✓ Test passed")
        except Exception as e:
            print(f"✗ Test failed: {str(e)}")

    print("\nAll tests completed.")
