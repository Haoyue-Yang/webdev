# verifier/vlm.py

import base64
import json
import random
import re
import os
from pathlib import Path
from typing import Dict, Any
from abc import ABC, abstractmethod

from utils.api_calls import call_llm_api
from utils.config import config


def fix_json_trailing_commas(json_str: str) -> str:
    """Fix trailing commas in JSON strings (common LLM output issue)"""
    return re.sub(r',(\s*[}\]])', r'\1', json_str)

def load_aesthetics_prompt():
    """Load the aesthetics scoring prompt from file."""
    prompt_path = Path(__file__).parent / "prompts" / "static_vlm_aesthetics_only_v1.txt"
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        # Fallback prompt if file not found
        return "You are an expert UI/UX designer. Rate the aesthetics of the provided webpage screenshot on a scale of 0.0 to 1.0, where 0 is poor and 1 is excellent. Explain your reasoning. Respond in JSON format with 'score' and 'reason' keys ONLY."

# --- Base Class ---
class BaseVLM(ABC):
    """
    Abstract base class for all Visual Language Models.
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def evaluate_image(self, image_path: Path, query: str) -> Dict[str, Any]:
        """
        Evaluates an image based on a query.
        """
        pass

# --- Concrete Implementations ---

class FastVLM(BaseVLM):
    """
    A placeholder implementation for FastVLM that returns a random score.
    """
    def evaluate_image(self, image_path: Path, query: str) -> Dict[str, Any]:
        print(f"  (Mock FastVLM) Evaluating {image_path.name}...")
        mock_score = random.uniform(0.1, 1.0)
        mock_reason = "This is a mock response from the placeholder FastVLM."
        return {
            "score": mock_score,
            "reason": mock_reason,
            "error": None,
        }

def encode_image_to_base64(image_path: Path) -> str:
    """Encodes an image file to a base64 string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

class OpenAIVLM(BaseVLM):
    """
    VLM handler for OpenAI's vision models.
    """
    def __init__(self, vlm_config: Dict[str, Any]):
        super().__init__(vlm_config)

    def evaluate_image(self, image_path: Path, query: str) -> Dict[str, Any]:
        base64_image = encode_image_to_base64(image_path)
        aesthetics_prompt = load_aesthetics_prompt()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": aesthetics_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                    },
                ],
            }
        ]
        try:
            # Use VLM-specific API configuration
            api_config = {
                "api_key": self.config.get('api_key'),
                "base_url": self.config.get('base_url')
            }
            api_type = self.config.get('api', 'openai')
            
            response_str = call_llm_api(
                api_config=api_config,
                messages=messages,
                model=self.config['model'],
                api=api_type,
                json_mode=True
            )
            # Fix trailing commas (common LLM output issue)
            fixed_response = fix_json_trailing_commas(response_str)
            result = json.loads(fixed_response)
            return {
                "score": float(result.get("score", 0.0)),
                "reason": result.get("reason", "No reason provided."),
                "error": None,
            }
        except json.JSONDecodeError:
            error_msg = f"VLM response was not valid JSON: {response_str}"
            return {"score": 0.0, "reason": "Invalid response format from VLM.", "error": error_msg}
        except Exception as e:
            error_msg = f"An error occurred during OpenAI VLM call: {e}"
            return {"score": 0.0, "reason": "Exception during VLM call.", "error": error_msg}

class ClaudeVLM(BaseVLM):
    """
    VLM handler for Claude's vision models.
    """
    def __init__(self, vlm_config: Dict[str, Any]):
        super().__init__(vlm_config)

    def evaluate_image(self, image_path: Path, query: str) -> Dict[str, Any]:
        base64_image = encode_image_to_base64(image_path)
        aesthetics_prompt = load_aesthetics_prompt()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": aesthetics_prompt},
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
        try:
            # Use VLM-specific API configuration
            api_config = {
                "api_key": self.config.get('api_key'),
                "base_url": self.config.get('base_url')
            }
            api_type = self.config.get('api', 'anthropic')
            
            response_str = call_llm_api(
                api_config=api_config,
                messages=messages,
                model=self.config['model'],
                api=api_type,
                json_mode=True if api_type == 'openai' else False
            )
            # Fix trailing commas (common LLM output issue)
            fixed_response = fix_json_trailing_commas(response_str)
            result = json.loads(fixed_response)
            return {
                "score": float(result.get("score", 0.0)),
                "reason": result.get("reason", "No reason provided."),
                "error": None,
            }
        except json.JSONDecodeError:
            error_msg = f"VLM response was not valid JSON: {response_str}"
            return {"score": 0.0, "reason": "Invalid response format from VLM.", "error": error_msg}
        except Exception as e:
            error_msg = f"An error occurred during Claude VLM call: {e}"
            return {"score": 0.0, "reason": "Exception during VLM call.", "error": error_msg}

# --- VLM Handler/Router ---

VLM_REGISTRY = {
    "fastvlm": FastVLM,
    "openai-vlm": OpenAIVLM,
    "claude-vlm": ClaudeVLM,
}

class VLMHandler:
    """
    Handles routing requests to the correct VLM model.
    """
    def __init__(self, vlm_config: Dict[str, Any]):
        self.config = vlm_config
        self.loaded_vlms: Dict[str, BaseVLM] = {}

    def get_vlm(self, model_name: str) -> BaseVLM:
        if model_name in self.loaded_vlms:
            return self.loaded_vlms[model_name]

        if model_name not in VLM_REGISTRY:
            raise ValueError(f"Unknown VLM model: {model_name}. Available models: {list(VLM_REGISTRY.keys())}")

        vlm_class = VLM_REGISTRY[model_name]
        model_config = self.config['models'].get(model_name, {})
        
        instance = vlm_class(model_config)
        self.loaded_vlms[model_name] = instance
        return instance

    def evaluate_image(self, image_path: Path, query: str, model_name: str = None) -> Dict[str, Any]:
        if model_name is None:
            model_name = self.config.get('default_model')
            if not model_name:
                raise ValueError("No default VLM model specified in the configuration.")

        print(f"Evaluating image with VLM model: '{model_name}'...")
        vlm_instance = self.get_vlm(model_name)
        return vlm_instance.evaluate_image(image_path, query) 