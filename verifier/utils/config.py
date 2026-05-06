"""
Configuration module for the model iteration project.

This module loads configuration from YAML files and environment variables.
"""

import os
import yaml
import json
import re
from typing import Dict, Any, Optional, List
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def expand_env_variables(value: Any) -> Any:
    """
    Parse ${ENV_VAR||default_value} pattern and replace with environment variable or default.
    Handles all types of values, including complex strings with multiple env vars.
    
    Args:
        value: Value that may contain environment variable references
        
    Returns:
        Resolved value after replacing environment variables
    """
    # Handle non-string values directly
    if not isinstance(value, str):
        return value
        
    # Match pattern ${VAR||default}
    pattern = r'\${([^}|]+)(?:\|\|([^}]*))?}'
    
    # Check if the string contains any environment variable patterns
    if not re.search(pattern, value):
        return value
        
    # Function to replace a single match
    def replace_env_var(match):
        env_var = match.group(1)
        default_val = match.group(2) if match.group(2) is not None else ""
        
        # Get value from environment or use default
        env_value = os.environ.get(env_var)
        result = env_value if env_value is not None and env_value != "" else default_val
        
        return result
    
    # Replace all environment variables in the string
    processed_value = re.sub(pattern, replace_env_var, value)
    
    # If the entire string is an environment variable reference, try to convert it to proper type
    if value.strip() == processed_value.strip():
        return processed_value
    
    # Check if the processed value is a special format (after all env vars are replaced)
    try:
        # Try as JSON if it looks like it
        if (processed_value.strip().startswith('{') and processed_value.strip().endswith('}')) or \
           (processed_value.strip().startswith('[') and processed_value.strip().endswith(']')):
            try:
                return json.loads(processed_value)
            except json.JSONDecodeError:
                pass
        
        # Try as boolean
        if processed_value.lower() == 'true':
            return True
        elif processed_value.lower() == 'false':
            return False
            
        # Try as number
        if processed_value.isdigit():
            return int(processed_value)
        elif processed_value.replace('.', '', 1).isdigit() and processed_value.count('.') == 1:
            return float(processed_value)
    except Exception as e:
        # If any conversion fails, return the string value
        pass
        
    return processed_value


def update_env_config(config_dict: Dict[str, Any]) -> None:
    """
    Recursively update configuration values from environment variables.
    
    Args:
        config_dict: Configuration dictionary to update
    """
    for key, value in config_dict.items():
        if isinstance(value, dict):
            update_env_config(value)
        else:
            config_dict[key] = expand_env_variables(value)


def load_config(config_path: Path = Path('conf/config.yaml')) -> dict:
    """Loads the YAML configuration file and replaces environment variable placeholders."""
    if not config_path.exists():
        # This allows scripts in subdirectories (like /scripts) to find the config
        if Path('../conf/config.yaml').exists():
            config_path = Path('../conf/config.yaml')
        else:
            raise FileNotFoundError(f"Configuration file not found at: {config_path.resolve()}")
    
    with open(config_path, 'r') as f:
        content = f.read()
        
        # Regex to find all ${VAR||default} or ${VAR} patterns
        # It handles quoted and unquoted default values
        pattern = re.compile(r'\$\{(.*?)\}')
        
        def replace_env(match):
            # Split VAR||default
            parts = match.group(1).split('||')
            var_name = parts[0]
            default_value = parts[1] if len(parts) > 1 else ""
            
            # Strip quotes from default value if they exist
            if default_value.startswith(('"', "'")) and default_value.endswith(('"', "'")):
                default_value = default_value[1:-1]
            
            return os.environ.get(var_name, default_value)
    
        # Substitute all environment variable placeholders
        substituted_content = pattern.sub(replace_env, content)
        return yaml.safe_load(substituted_content)


def _deep_update(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """
    Recursively update a nested dictionary.
    
    Args:
        target: Target dictionary to update
        source: Source dictionary with new values
    """
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


# Global config object for easy access from other modules
config = load_config() 