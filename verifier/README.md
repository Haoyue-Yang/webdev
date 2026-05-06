# COOKIE: Continuous On-screen Key Interaction Evaluation for Web Generation

This directory contains the implementation of **COOKIE**, a reference-free, autonomously driven, holistically reasoned evaluation system for interactive web generation. COOKIE instantiates a new evaluation regime grounded in Flavell's metacognitive monitoring, separating evidence accumulation from judgment across three stages: **Static Perception**, **Agent-Driven Interaction**, and **Dynamic Scoring**.

For the overall project (including the COOKIE-Bench dataset and paper figures), see the [root README](../README.md).

---

## Table of Contents

- [Overview](#overview)
- [Evaluation Pipeline](#evaluation-pipeline)
- [Evaluation Dimensions](#evaluation-dimensions)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Starting the Server](#starting-the-server)
- [API Endpoints](#api-endpoints)
- [Evaluation Types](#evaluation-types)
- [Response Format](#response-format)
- [Error Handling](#error-handling)
- [Debug Artifacts](#debug-artifacts)
- [Architecture](#architecture)

---

## Overview

COOKIE evaluates whether LLM-generated Web projects correctly implement user requirements. It takes project code as input, deploys it locally, and runs multi-dimensional evaluation through screenshots, LLM analysis, and browser-based interactive testing.

The core insight is that evaluating interactive web artifacts requires **continuous on-screen observation** — not just a single static screenshot. COOKIE captures the full evidence chain (video, screenshots, interaction traces) before any scoring judgment is issued.

### Key Features

- **Reference-free** — No ground-truth implementations or pre-authored checklists
- **Autonomously driven** — The COOKIE Agent plans and executes interaction trajectories on the fly via an Observe→Think→Act loop
- **Holistically reasoned** — VLM judges synthesize continuous video, screenshots, and interaction traces into calibrated scores
- **Multi-modal evidence** — Captures screen recordings, audio, per-step screenshots, and interaction logs

---

## Evaluation Pipeline

COOKIE executes a five-stage pipeline from code to score:

| Stage | Description | Output |
|-------|-------------|--------|
| **1. Install & Start** | Deploy generated code, start dev server, health check | Running web artifact |
| **2. Static Evaluation** | Full-page screenshot, runtime logs, structural inventory | VLM-scored provisional priors |
| **3. Agent-Driven Interaction** | COOKIE Agent explores via Observe→Think→Act loop with human-like clicks | Trajectory, keyframes, screencast, audio, problem summary |
| **4. Score Adjustment** | Grade issues at Critical / Major / Minor severity across Functional and Aesthetic dimensions | Adjusted per-dimension scores with structured failure attribution |
| **5. Overall Scoring** | Aggregate into final calibrated scores | Final functionality and aesthetics scores |

### Three Evaluation Strategies

COOKIE supports three `agent_type` strategies, trading off evaluation depth for latency:

| Strategy | Description | Typical Latency |
|----------|-------------|-----------------|
| `static` | Single screenshot + VLM scoring | ~10–30 s |
| `agent_tars` | Browser-based autonomous interaction + scoring | ~1–5 min |
| `interactive_video` | Static baseline + interaction + VLM problem detection + score adjustment | ~3–10 min |

The `interactive_video` strategy is the most thorough and is recommended for production evaluation. It mirrors the paper's full metacognitive pipeline: static priors are formed first, then the COOKIE Agent gathers interaction evidence, and finally a VLM judge issues calibrated scores only after the full evidence chain is complete.

---

## Evaluation Dimensions

- **Functionality** — Semantic correctness of interactive logic, state management, event handling, and cross-component coordination
- **Aesthetics** — Visual composition, transition naturalness, layout, typography, and perceptual quality

Both dimensions are scored on a continuous 0–2 scale.

---

## Prerequisites

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | 3.12 (recommended) | |
| Node.js | >= 22.15 | Required for Agent-TARS and React projects |
| pnpm | 9.x | `npm install -g pnpm@9` |
| Playwright Chromium | latest | Installed via `playwright install chromium` |
| ffmpeg | any | For screencast video encoding; auto-installed by setup.sh |

---

## Setup

1. Enter the verifier directory and copy `.env.example` to `.env`:

```bash
cd verifier
cp .env.example .env
```

Fill in your API keys and base URLs. When using only OpenAI (or OpenAI-compatible) API, the Anthropic section can be left empty; likewise, when using only Anthropic (or Anthropic-compatible) API, the OpenAI section can be left empty.

Key URL format notes:

| Variable | Format | Example |
|----------|--------|---------|
| `ANTHROPIC_BASE_URL` | Without `/v1` suffix | `https://api.anthropic.com` |
| `OPENAI_BASE_URL` | With `/v1/chat/completions` suffix | `https://api.openai.com/v1/chat/completions` |
| `AGENT_TARS_BASE_URL` | With `/v1` suffix | `https://api.openai.com/v1` |

2. Run setup:

```bash
bash setup.sh
```

This installs npm dependencies, Python packages, Playwright Chromium, and builds TARS patches. For corporate/internal npm registry, set `NPM_REGISTRY` in `.env`.

---

## Starting the Server

```bash
PORT=8325 WORKERS=10 AUTO_START_AGENT_TARS=True python run_verifier_server.py
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PORT` | 5001 | Listen port |
| `WORKERS` | cpu_count | Number of gunicorn workers (also determines TARS instance count) |
| `AUTO_START_AGENT_TARS` | False | Auto-start Chrome CDP + Agent-TARS instances for each worker |
| `HOST` | 127.0.0.1 | Bind address |
| `DEBUG` | False | Flask debug mode (single worker, no gunicorn) |
| `TARS_BASE_PORT` | 8890 | First TARS serve port |
| `CDP_BASE_PORT` | 9225 | First Chrome CDP port |

Successful startup should show output like:

```
Starting gunicorn with 10 workers ...
[2026-05-05 17:39:06 +0800] [3318896] [INFO] Starting gunicorn 25.0.1
[2026-05-05 17:39:06 +0800] [3318896] [INFO] Listening at: http://0.0.0.0:8325 (3318896)
[2026-05-05 17:39:06 +0800] [3318896] [INFO] Using worker: sync
[worker 3321042] assigned TARS slot 0 → http://127.0.0.1:8890  CDP:9225
[worker 3321045] assigned TARS slot 1 → http://127.0.0.1:8891  CDP:9226
...
```

If you see `AgentTARS failed to start after 300 seconds`, check that Node.js >= 22.15 and pnpm are installed, and the `UI-TARS-desktop/multimodal` directory has valid dependencies.

---

## API Endpoints

### POST /verify

Submit code as a file-path-to-content dictionary for evaluation.

```json
{
  "code": {
    "src/App.jsx": "// React code...",
    "package.json": "{...}"
  },
  "query": "Build a todo list application",
  "language": "react",
  "project_id": "my-project",
  "agent_type": "interactive_video",
  "keep_existing_project": false,
  "evaluator_config": {
    "evaluators": {
      "aesthetics_functional": {
        "model": "gemini-3-flash-preview"
      }
    }
  }
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `code` | dict | Yes | — | File path → content mapping, must be non-empty |
| `query` | string | Yes | — | Project requirement description |
| `language` | string | No | auto-inferred | `"react"`, `"html"`, `"webdev_scaffold_2"` |
| `project_id` | string | No | auto-generated UUID | Project identifier |
| `agent_type` | string | No | `"static"` | `"static"`, `"agent_tars"`, `"interactive_video"` |
| `keep_existing_project` | bool | No | `false` | Preserve existing project files |
| `evaluator_config` | dict | No | `null` | Deep-merged override into config.yaml |

Language auto-inference rules:
- `code` contains `package.json` and deps include `react` → `"react"`
- `code` contains `.html` file and no `package.json` → `"html"`
- Otherwise → must be specified explicitly

### POST /verify-scaffold

Verify scaffold projects (`webdev_scaffold_2` language), typically used for React scaffold evaluations with a pre-built tar archive.

```json
{
  "response": "[tar_url]:https://example.com/project.tar.gz",
  "query": "Build an interactive dashboard with charts and filters",
  "project_id": "scaffold-dashboard",
  "agent_type": "interactive_video"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `response` | string | Yes | `tool_call` blocks or `[tar_url]:<url>` |
| `query` | string | Yes | Project requirement description |

`response` parsing rules:
- Starts with `[tar_url]:` → download and extract the tar archive
- Otherwise → parse as `tool_call` file blocks

### Other Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check → `{"status": "healthy", "service": "verifier"}` |
| GET | `/projects` | List all projects |
| GET | `/projects/{project_id}` | Get project info |
| DELETE | `/projects/{project_id}` | Delete project |

---

## Evaluation Types

### agent_type: static

Fast visual evaluation without interaction.

**Flow:** Install → Start dev server → Playwright screenshot → VLM analyzes screenshot + source code + query → CSS reset check → Score

**Time:** ~10–30 seconds (mainly VLM call)

**Recommended models** (from `conf/config.yaml`):
- Aesthetics/Functional: `gemini-3-flash-preview` (OpenAI-compatible API)

### agent_type: agent_tars

Browser-based interactive evaluation via the COOKIE Agent (powered by Agent-TARS).

**Flow:** Install → Start dev server → COOKIE Agent browses and interacts with the page → Score → FallbackEvaluator on failure

**Prerequisite:** Agent-TARS service must be running (set `AUTO_START_AGENT_TARS=True` on startup)

**Time:** ~1–5 minutes

**Recommended models:**
- Agent-TARS: `doubao-seed-2-0-lite-260215` (configured in `evaluators.agent_tars.model`)

**Config overrides:**

```json
{
  "evaluator_config": {
    "evaluators": {
      "agent_tars": {
        "max_steps": 25,
        "timeout": 1800,
        "max_retries": 1
      }
    }
  }
}
```

### agent_type: interactive_video

Most thorough evaluation, implementing the full COOKIE metacognitive pipeline: static baseline + interaction + VLM problem detection + score adjustment.

**Flow:**

| Step | Description |
|------|-------------|
| 0 | Static evaluation (provisional priors / baseline scores) |
| 0.5 | Planner — LLM analyzes source code to generate a test plan |
| 1 | COOKIE Agent interaction only (no scoring, collects artifacts: browser_actions, screenshots, video) |
| 2 | VLM problem detection (analyzes interaction screenshots/video for functional/aesthetic issues) |
| 3 | Score adjustment (adjusts baseline based on detected problems, with functional score floor) |

**Prerequisite:** Agent-TARS service + VLM API (supports image/video input)

**Time:** ~3–10 minutes

**Recommended models:**
- VLM: `gemini-3-flash-preview`
- Agent-TARS: `doubao-seed-2-0-lite-260215`

**Config overrides:**

```json
{
  "evaluator_config": {
    "evaluators": {
      "agent_tars": {
        "iv_skip_initial_static": false,
        "iv_interaction_max_retries": 2,
        "iv_detection_max_retries": 5,
        "iv_adjustment_max_retries": 5
      }
    }
  }
}
```

---

## Response Format

All verification endpoints return a unified structure:

```json
{
  "project_id": "string",
  "project_path": "string",
  "evaluations": {
    "installation": {
      "score": 1.0,
      "reason": "Dependencies installed successfully",
      "error": null,
      "skipped": false
    },
    "running": {
      "score": 1.0,
      "reason": "Server is running",
      "error": null
    },
    "aesthetics": {
      "score": 1.5,
      "reason": "..."
    },
    "functional": {
      "score": 1.25,
      "reason": "...",
      "details": {}
    }
  },
  "error": null,
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

### Score Ranges

| Evaluation | Scale | Normal Range |
|------------|-------|-------------|
| installation | 0 or 1 | binary |
| running | 0 or 1 | binary |
| aesthetics | 0–2 (float) | continuous |
| functional | 0–2 (float) | continuous |

- `null` — evaluation step failed
- `0.0` — short-circuited (server not running or installation failed)
- `-1.0` — Agent-TARS complete system failure

### Interactive Video Extra Fields

When using `agent_type="interactive_video"`, the `functional` and `aesthetics` objects include adjustment details:

```json
{
  "functional": {
    "score": 1.6,
    "reason": "adjusted reason",
    "static_score": 2.0,
    "static_reason": "baseline reason",
    "adjustment": -0.4,
    "adjustment_summary": "missing button interaction"
  }
}
```

---

## Error Handling

### HTTP 400 — Validation Errors

| Error message | Trigger |
|---------------|---------|
| `"code must be a non-empty dictionary"` | Empty or non-dict `code` |
| `"query must be a non-empty string"` | Empty `query` |
| `"Invalid agent_type: ..."` | Unsupported agent_type |
| `"language must be specified"` | Cannot auto-infer language |

### HTTP 500 — Internal Errors

```json
{
  "error": "Internal server error: ...",
  "traceback": "..."
}
```

### Evaluation-Level Errors

Errors are embedded in each evaluation item's `error` field, with `score` as `null` or `0.0`:

| `error` value | Trigger |
|---------------|---------|
| `"Server not running"` | Dev server startup failed |
| `"Server failed to respond"` | curl health check failed |
| `"Failed to take screenshot"` | Playwright error |
| `"LLM returned empty response"` | VLM no response |
| `"No code files found"` | Empty project directory |
| `"Agent-TARS evaluation failed"` | All TARS retries exhausted |
| `"VLM problem detection skipped: no visual media"` | No video/screenshot available |
| `"VLM problem detection failed after all retries"` | VLM detection retries exhausted |
| `"adjustment_failed"` | Score adjustment retries exhausted |
| `"[CSS Reset Issue] ..."` | CSS reset issue caused score cap |

### Short-Circuit Behavior

When installation or running fails, downstream evaluations are skipped with `score: 0.0`:

```json
{
  "evaluations": {
    "installation": { "score": 0.0, "reason": "Installation failed", "error": "stderr..." },
    "running":      { "score": 0.0, "reason": "Not evaluated" },
    "aesthetics":   { "score": 0.0, "reason": "Not evaluated" },
    "functional":   { "score": 0.0, "reason": "Not evaluated" }
  }
}
```

When running fails but installation succeeded:

```json
{
  "evaluations": {
    "installation": { "score": 1.0, "reason": "Dependencies installed successfully" },
    "running":      { "score": 0.0, "reason": "Server failed to respond" },
    "aesthetics":   { "score": 0.0, "reason": "Server not running." },
    "functional":   { "score": 0.0, "reason": "Server not running." }
  }
}
```

---

## Debug Artifacts

Each project creates a `.verifier/` directory with intermediate data for inspection and reproducibility:

| File | Description |
|------|-------------|
| `verification_summary.json` | Result summary |
| `verification_summary.txt` | Human-readable summary |
| `combined_log.json` | All steps' logs |
| `installation_log.json` | Installation step logs |
| `installation_output.json` | Installation output (stdout/stderr) |
| `server_startup_log.json` | Server startup logs |
| `screenshot_*.png` | Page screenshots |
| `llm_response_*.json` | LLM responses and token usage |
| `browser_actions_*.json` | Agent-TARS / IRIS interaction sequence |
| `agent_screenshots/` | Interaction screenshots (per attempt) |
| `screencast_*.mp4` | Screen recordings |
| `planner_plan.json` | Generated test plan |
| `detected_problems.json` | VLM-detected issues |
| `adjusted_scores.json` | Score adjustment details |
| `verifier_response.json` | Final API response |

Logs are stored in `logs/` with daily rotation (7-day retention).

---

## Architecture

```
verifier/
  verifier.py                    # Main orchestrator, implements the 5-stage pipeline
  app.py                         # Flask REST API
  __main__.py                    # CLI entry point
  css_reset_check.py             # CSS reset detection (aesthetics score capping)
  fallback_evaluator.py          # LLM fallback when Agent-TARS fails
  tars_agent_client.py           # Agent-TARS MCP client (drives the COOKIE Agent)

  agents/                        # Evaluation strategies
    static_strategy.py           # Screenshot + LLM (Stage 2 only)
    agent_tars_strategy.py       # Browser interaction + scoring
    interactive_video_strategy.py # Full 5-stage COOKIE pipeline

  evaluators/                    # Core evaluators
    running_evaluator.py         # Dev server health check (Stage 1)
    aesthetics_functional_evaluator.py # Screenshot + VLM scoring (Stage 2)

  languages/                     # Language handlers
    react_handler.py             # React/Vite projects
    html_handler.py              # Static HTML
    webdev_scaffold_2_handler.py # Scaffold tool_call projects

  prompts/                       # Prompt templates
  utils/                         # Internal utilities
```