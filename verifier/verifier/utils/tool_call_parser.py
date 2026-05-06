# verifier/utils/tool_call_parser.py

import re
import json
from typing import List, Dict, Any


def parse_tool_calls(response: str) -> List[Dict[str, Any]]:
    """
    Parse <tool_call>...</tool_call> blocks from LLM response.

    Args:
        response: The LLM response text containing tool_call blocks

    Returns:
        List of parsed tool calls, each containing 'name' and 'arguments'

    Example:
        >>> response = '''
        ... <tool_call>
        ... {"name": "create_file", "arguments": {"path": "src/App.tsx", "content": "..."}}
        ... </tool_call>
        ... '''
        >>> parse_tool_calls(response)
        [{'name': 'create_file', 'arguments': {'path': 'src/App.tsx', 'content': '...'}}]
    """
    pattern = r'<tool_call>\s*(.*?)\s*</tool_call>'
    matches = re.findall(pattern, response, re.DOTALL)

    tool_calls = []
    for match in matches:
        try:
            tool_data = json.loads(match.strip())
            tool_calls.append({
                'name': tool_data['name'],
                'arguments': tool_data.get('arguments', {})
            })
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse tool_call JSON: {e}")
            print(f"Raw content: {match[:200]}...")
            continue
        except KeyError as e:
            print(f"Warning: Tool call missing required field: {e}")
            print(f"Parsed data: {tool_data}")
            continue

    return tool_calls


def extract_tool_calls_with_positions(response: str) -> List[Dict[str, Any]]:
    """
    Parse tool_call blocks and return their positions in the response.

    Useful for debugging and understanding the order of tool calls.

    Args:
        response: The LLM response text containing tool_call blocks

    Returns:
        List of tool calls with position information
    """
    pattern = r'<tool_call>\s*(.*?)\s*</tool_call>'

    tool_calls = []
    for i, match in enumerate(re.finditer(pattern, response, re.DOTALL)):
        try:
            tool_data = json.loads(match.group(1).strip())
            tool_calls.append({
                'index': i,
                'start': match.start(),
                'end': match.end(),
                'name': tool_data['name'],
                'arguments': tool_data.get('arguments', {})
            })
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to parse tool_call at position {match.start()}: {e}")
            continue

    return tool_calls
