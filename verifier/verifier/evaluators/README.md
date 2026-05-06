# Verifier Evaluators

This directory contains the evaluators used by the verifier system.

## Available Evaluators

1. **RunningEvaluator** (`running_evaluator.py`)
   - Always enabled
   - Checks if the development server is running
   - Returns 1.0 if successful, 0.0 if failed

2. **AestheticsFunctionalEvaluator** (`aesthetics_functional_evaluator.py`)
   - Combined evaluator that evaluates both aesthetics and functionality in a single LLM call
   - Takes screenshot AND reads source code
   - Optionally runs axe-core accessibility testing
   - More efficient than running separate evaluators
   - Returns structured results for both aspects
   - Configurable via `prompt_version` (default: `v3`) and `css_reset_check_enabled`

## Configuration

In `conf/config.yaml`:

```yaml
evaluators:
  return_usage: true

  aesthetics_functional:
    enabled: true
    model: "gemini-3-flash-preview"
    api: "openai"
    api_key: ${AESTHETICS_FUNCTIONAL_OPENAI_API_KEY||}
    base_url: ${AESTHETICS_FUNCTIONAL_OPENAI_BASE_URL||}
    prompt_version: "v3"           # Loads static_vlm_scoring_{version}.txt
    css_reset_check_enabled: true  # Toggle CSS reset issue detection
    pure_llm_scoring: true
    use_full_chromium: true
    axe_core_enabled: false

  agent_tars:
    server_url: ${AGENT_TARS_SERVER_URL||http://localhost:8890}
    max_steps: 25
    timeout: 1800
```

## Per-request Config Override

All API endpoints accept an optional `evaluator_config` dict that deep-merges into the loaded config:

```json
{
  "code": {"index.html": "..."},
  "query": "Build a todo app",
  "language": "html",
  "agent_type": "static",
  "evaluator_config": {
    "aesthetics_functional": {
      "prompt_version": "without_screenshot",
      "model": "claude-sonnet-4-20250514-v1:0"
    }
  }
}
```

## Output Format

### AestheticsFunctionalEvaluator

Returns:
```python
{
    "aesthetics": {
        "score": float,     # 0.0, 0.5, 1.0, 1.5, or 2.0
        "reason": str,
        "error": str|None
    },
    "functional": {
        "score": float,     # 0.0-2.0
        "reason": str,
        "error": str|None,
        "details": dict|None  # Accessibility results if axe_core_enabled
    }
}
```

## Prompts

Evaluator prompts are stored in `verifier/prompts/`:
- `static_vlm_scoring_v3.txt` - Default combined prompt (used when `prompt_version=v3`)
- `static_vlm_scoring_without_screenshot.txt` - Prompt without screenshot attachment
- `static_vlm_scoring_python_v1.txt` - Python functional evaluation prompt
- `agent_tars_scoring_v1.txt` - Agent-TARS scoring prompt