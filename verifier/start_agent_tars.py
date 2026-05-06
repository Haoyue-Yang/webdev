#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
import requests

ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?:\|\|([^}]*))?\}")

def _resolve_env_in_str(s: str) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        default = m.group(2) or ""
        return os.environ.get(key, default)

    return ENV_PATTERN.sub(repl, s)


def resolve_env_vars(obj: Any) -> Any:
    """Recursively resolve ${ENV||default} in YAML-loaded structures."""
    if isinstance(obj, str):
        return _resolve_env_in_str(obj)
    if isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_vars(v) for v in obj]
    return obj


def load_cfg(config_path: Path) -> Dict[str, Any]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return resolve_env_vars(cfg)


def _health_url(server_url: str) -> str:
    return server_url.rstrip("/") + "/api/v1/health"


def is_tars_alive(server_url: str, timeout_s: float = 1.0) -> bool:
    """Headless server doesn't serve '/', use /api/v1/health instead."""
    try:
        r = requests.get(_health_url(server_url), timeout=timeout_s)
        return r.status_code == 200
    except Exception:
        return False


def _validate_base_url(base_url: str) -> str:
    """Prevent the common mistake that causes /chat/completions/chat/completions."""
    base_url = base_url.strip()
    if not base_url:
        return base_url
    # Don't allow endpoint suffix in baseURL
    if "chat/completions" in base_url:
        raise ValueError(
            f"Invalid model.base_url: baseURL should point to API root (usually ends with /v1), "
            f"do NOT include '/chat/completions'. Got: {base_url}"
        )
    return base_url.rstrip("/")


def start_agent_tars(
    config_path: str = "conf/config.yaml",
    *,
    wait_ready: bool = True,
    ready_timeout_s: int = 60,
) -> subprocess.Popen:
    """
    Start Agent TARS headless server from LOCAL SOURCE repo (pnpm workspace).

    Requires YAML:
      evaluators.agent_tars.local_repo: /path/to/UI-TARS-desktop/multimodal
    """
    root = Path(__file__).resolve().parent
    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path

    if not cfg_path.exists():
        raise FileNotFoundError(f"config.yaml not found: {cfg_path}")

    cfg = load_cfg(cfg_path)
    agent_cfg: Dict[str, Any] = (cfg.get("evaluators") or {}).get("agent_tars") or {}
    model_cfg: Dict[str, Any] = agent_cfg.get("model") or {}

    # ---- server ----
    port = int(agent_cfg.get("serve_port", 8888))
    host = str(agent_cfg.get("serve_host", "127.0.0.1")).strip()  # used to build server_url only
    server_url = str(agent_cfg.get("server_url", f"http://{host}:{port}")).rstrip("/")

    # ---- browser ----
    cdp_endpoint = str(agent_cfg.get("cdp_endpoint", "http://127.0.0.1:9222/json/version")).strip()
    browser_control = str(agent_cfg.get("browser_control", "hybrid")).strip()

    # ---- SQLite storage (per-instance, avoids lock contention) ----
    # 默认路径：/tmp/agent-tars-storage-{port}-{unix_ts}
    # 每次启动生成唯一路径，防止相同端口重启时旧 SQLite 锁文件残留导致冲突。
    # 可通过 config.yaml storage_dir（或 AGENT_TARS_STORAGE_DIR 环境变量）手动指定；
    # 留空则自动规则生成。
    storage_dir = str(agent_cfg.get("storage_dir", "")).strip()
    if not storage_dir:
        ts = int(time.time())
        storage_dir = f"/tmp/agent-tars-storage-{port}-{ts}"
    # 若手动指定了相对路径，则相对于项目根目录转为绝对路径
    storage_dir_path = Path(storage_dir)
    if not storage_dir_path.is_absolute():
        storage_dir_path = root / storage_dir_path
    storage_dir = str(storage_dir_path)

    # ---- pause/resume (patch-dist.js 运行时配置) ----
    page_pause_enabled = str(agent_cfg.get("page_pause_enabled", "true")).strip().lower()
    page_pause_wait_ms = str(agent_cfg.get("page_pause_wait_ms", "500")).strip()
    press_duration_ms = str(agent_cfg.get("press_duration_ms", "30")).strip()
    click_wait_ms = str(agent_cfg.get("click_wait_ms", "300")).strip()
    navigate_wait_ms = str(agent_cfg.get("navigate_wait_ms", "1000")).strip()
    
    # ---- 工具过滤配置（二选一：include 优先于 exclude）----
    tool_include = str(agent_cfg.get("tool_include", "")).strip()
    tool_exclude = str(agent_cfg.get("tool_exclude", "")).strip()
    
    # ---- LLM 日志配置 (patch-dist.js 运行时配置) ----
    llm_log_enabled = str(agent_cfg.get("llm_log_enabled", "true")).strip().lower()
    # LLM日志输出目录：
    # - 如果配置了 llm_log_dir，则使用配置值
    # - 否则为空，让 patch-dist.js 自动使用 AGENT_RECORD_OUTPUT 同级的 llm_logs/
    llm_log_dir = str(agent_cfg.get("llm_log_dir", "")).strip()
    # 如果是相对路径，转为绝对路径
    if llm_log_dir and not os.path.isabs(llm_log_dir):
        llm_log_dir = str(root / llm_log_dir)
    
    # ---- 录屏配置 (patch-dist.js 运行时配置) ----
    screencast_enabled = str(agent_cfg.get("screencast_enabled", "false")).strip().lower()
    screencast_fps = str(agent_cfg.get("screencast_fps", "10")).strip()
    screencast_quality = str(agent_cfg.get("screencast_quality", "80")).strip()
    screencast_strategy = str(agent_cfg.get("screencast_strategy", "cdp_legacy")).strip()
    # 录屏输出目录：默认放到 logs/recordings/ 下
    log_dir = str((cfg.get("log") or {}).get("dir", "logs")).strip()
    screencast_output_default = os.path.join(root, log_dir, "recordings")
    screencast_output = str(agent_cfg.get("screencast_output", screencast_output_default)).strip()
    # 如果是相对路径，转为绝对路径
    if not os.path.isabs(screencast_output):
        screencast_output = str(root / screencast_output)
    screencast_keep_frames = str(agent_cfg.get("screencast_keep_frames", "false")).strip().lower()
    screencast_record_from_init = str(agent_cfg.get("screencast_record_from_init", "false")).strip().lower()
    # 注意：screencast_module_path 在 multimodal_dir 确定后再计算    
    # ---- 日志配置 ----
    verbose_log = str(agent_cfg.get("verbose_log", "false")).strip().lower() in ("true", "1", "yes")

    # ---- model ----
    provider = str(model_cfg.get("provider", "volcengine")).strip()
    base_url = str(model_cfg.get("base_url", os.environ.get("OPENAI_BASE_URL", "")))
    api_key = str(model_cfg.get("api_key", os.environ.get("OPENAI_API_KEY", ""))).strip()
    model_id = str(model_cfg.get("model_id", os.environ.get("OPENAI_MODEL", ""))).strip()

    base_url = _validate_base_url(base_url)

    if not base_url:
        raise ValueError("Missing evaluators.agent_tars.model.base_url (or OPENAI_BASE_URL env).")
    if not api_key:
        raise ValueError("Missing evaluators.agent_tars.model.api_key (or OPENAI_API_KEY env).")
    if not model_id:
        raise ValueError("Missing evaluators.agent_tars.model.model_id (or OPENAI_MODEL env).")

    # ---- local repo path (multimodal workspace) ----
    local_repo = str(agent_cfg.get("local_repo", "")).strip()
    if not local_repo:
        raise ValueError(
            "Missing evaluators.agent_tars.local_repo (path to UI-TARS-desktop/multimodal)."
        )

    multimodal_dir = Path(local_repo).expanduser().resolve()
    if not multimodal_dir.exists():
        raise FileNotFoundError(f"multimodal dir not found: {multimodal_dir}")

    # ---- 录屏模块路径（在 multimodal_dir 确定后计算）----
    # 使用 CDP 非阻塞版本（screencast-recorder-cdp.js）
    screencast_module_path = str(multimodal_dir / "screencast-recorder-cdp.js")

    # If already alive, stop here (caller can decide to reuse)
    if is_tars_alive(server_url):
        print(f"✅ Agent-TARS already alive at {server_url}, skip start.")
        raise RuntimeError(f"Agent-TARS already running at {server_url}")

    # ---- 生成 per-instance agent-tars 配置文件（解决多实例 SQLite 锁冲突）----
    # 每个实例写到独立目录，避免所有实例共享 ~/.agent-tars/agent-tars.db
    instance_config_dir = root / "tmp"
    instance_config_dir.mkdir(parents=True, exist_ok=True)
    instance_config_path = instance_config_dir / f"agent-tars-config-{port}.json"
    instance_config = {
        "server": {
            # exclusive=true: 同一实例同时只允许一个 session 运行，
            # 防止并发请求共享同一 SQLiteStorageProvider 导致 "database is already open"
            "exclusive": True,
            "storage": {
                "type": "sqlite",
                "baseDir": storage_dir,
                "dbName": "agent-tars.db",
            }
        }
    }
    instance_config_path.write_text(json.dumps(instance_config, indent=2), encoding="utf-8")

    # ---- 启动前确保端口空闲（防止残留进程占用端口导致 EADDRINUSE 启动失败）----
    # 先用进程名模糊匹配，再用 fuser 直接针对端口（两者互补）
    subprocess.run(["pkill", "-f", f"agent-tars.*--port.*{port}"], capture_output=True)
    subprocess.run(f"fuser -k {port}/tcp 2>/dev/null; true", shell=True, capture_output=True)
    time.sleep(1)  # 等待端口释放

    # IMPORTANT: use local workspace CLI, NOT npx @latest
    cmd = [
        "pnpm",
        "--filter", "@agent-tars/cli",
        "exec", "--",
        "agent-tars", "serve",
        "--port", str(port),

        # per-instance config (storage path, etc.)
        "--config", str(instance_config_path),

        # browser
        "--browser.cdpEndpoint", cdp_endpoint,
        "--browser.control", browser_control,

        # model
        "--model.provider", provider,
        "--model.baseURL", base_url,
        "--model.apiKey", api_key,
        "--model.id", model_id,

        # logs
        "--logLevel", "debug",
        "--debug",
    ]
    
    # 添加工具过滤配置（include 优先于 exclude，二选一）
    if tool_include:
        cmd.extend(["--tool.include", tool_include])
    elif tool_exclude:
        cmd.extend(["--tool.exclude", tool_exclude])

    print("🚀 Launching Agent-TARS (LOCAL SOURCE) server:")
    print("  config:", str(cfg_path))
    print("  workspace(cwd):", str(multimodal_dir))
    print("  server_url:", server_url)
    print("  health_url:", _health_url(server_url))
    print("  cdp_endpoint:", cdp_endpoint)
    print("  browser.control:", browser_control)
    print("  page_pause_enabled:", page_pause_enabled)
    print("  page_pause_wait_ms:", page_pause_wait_ms)
    print("  press_duration_ms:", press_duration_ms)
    print("  click_wait_ms:", click_wait_ms)
    print("  navigate_wait_ms:", navigate_wait_ms)
    print("  llm_log_enabled:", llm_log_enabled)
    print("  llm_log_dir:", llm_log_dir)
    print("  screencast_enabled:", screencast_enabled)
    if screencast_enabled in ("true", "1", "yes"):
        print("  screencast_fps:", screencast_fps)
        print("  screencast_quality:", screencast_quality)
        print("  screencast_strategy:", screencast_strategy)
        print("  screencast_output:", screencast_output)
        print("  screencast_module_path:", screencast_module_path)
    print("  model.provider:", provider)
    print("  model.base_url:", base_url)
    print("  model.id:", model_id)
    print("  cmd:", " ".join(cmd))

    # 构建子进程环境变量，传递 pause/resume 配置
    child_env = os.environ.copy()
    child_env["AGENT_PAUSE_ENABLED"] = page_pause_enabled
    child_env["AGENT_PAUSE_WAIT_MS"] = page_pause_wait_ms
    child_env["AGENT_PRESS_DURATION_MS"] = press_duration_ms
    child_env["AGENT_CLICK_WAIT_MS"] = click_wait_ms
    child_env["AGENT_NAVIGATE_WAIT_MS"] = navigate_wait_ms
    
    # 传递 LLM 日志配置
    child_env["AGENT_LLM_SAVE_RAW"] = llm_log_enabled
    child_env["AGENT_LLM_LOG_DIR"] = llm_log_dir
    
    # 传递录屏配置
    child_env["AGENT_RECORD_ENABLED"] = screencast_enabled
    child_env["AGENT_RECORD_FPS"] = screencast_fps
    child_env["AGENT_RECORD_QUALITY"] = screencast_quality
    child_env["AGENT_RECORD_STRATEGY"] = screencast_strategy
    child_env["AGENT_RECORD_OUTPUT"] = screencast_output
    child_env["AGENT_RECORD_MODULE_PATH"] = screencast_module_path
    child_env["AGENT_RECORD_KEEP_FRAMES"] = screencast_keep_frames
    child_env["AGENT_RECORD_FROM_INIT"] = screencast_record_from_init

    # 日志过滤模式
    # verbose_log=true: 显示所有日志
    # verbose_log=false: 只显示关键日志（emoji开头、[CUSTOM-PATCH]、Duration等）
    
    if verbose_log:
        # 详细模式：日志直接输出到终端
        proc = subprocess.Popen(
            cmd,
            cwd=str(multimodal_dir),  # MUST be multimodal workspace
            env=child_env,
        )
    else:
        # 精简模式：通过管道捕获日志并过滤
        proc = subprocess.Popen(
            cmd,
            cwd=str(multimodal_dir),  # MUST be multimodal workspace
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        
        # 启动日志过滤线程
        import threading
        import re

        # 关键日志模式（只输出这些）
        KEY_LOG_PATTERNS = [
            r'^[🖼📣✅🤖🧩🔧💭🧠🧾────]',  # emoji 开头
            r'\[CUSTOM-PATCH\]',
            r'\[SCREENCAST',
            r'\[SESSION-CREATE\]',
            r'\[AGENT-SERVER',
            r'\[CUSTOM-GUI-WAIT\]',
            r'\[CUSTOM-VISION-WAIT\]',
            r'\[PAGE-PATCH\]',
            r'Duration:',
            r'Screenshot info:',
            r'^\s*(width|height|size|time|url|compression):',  # Screenshot info 内容
            r'Agent Loop Start',
            r'TOOL_RESULT|TOOL_CALL|PLAN',
            r'Iteration.*completed',
            r'isComplete',
            r'Execution completed',
            r'tool=browser_vision_control',
            r'\[Tool\] Result:',
            r'LLM.*stream start',
            r'Finalized Response',
            r'thought.*step.*action',  # LLM的响应内容
            r'"action":|"normalizedAction":',
        ]
        KEY_LOG_RE = re.compile('|'.join(KEY_LOG_PATTERNS))

        # 全量缓冲区：无论是否匹配过滤条件都保留，供进程意外退出时诊断
        captured_lines: list = []

        def filter_log_output():
            """读取子进程输出，关键日志打印到终端，全部日志写入缓冲区"""
            try:
                for line in iter(proc.stdout.readline, ''):
                    if not line:
                        break
                    line = line.rstrip('\n')
                    captured_lines.append(line)   # 始终缓冲
                    if KEY_LOG_RE.search(line):
                        print(line)
            except Exception:
                pass

        log_thread = threading.Thread(target=filter_log_output, daemon=True)
        log_thread.start()

    if wait_ready:
        t0 = time.time()
        last_lines = []
        while time.time() - t0 < ready_timeout_s:
            if proc.poll() is not None:
                if verbose_log:
                    out = ""
                    try:
                        if proc.stdout:
                            out = proc.stdout.read()
                    except Exception:
                        pass
                else:
                    # 等过滤线程把剩余输出读完，然后从缓冲区取诊断信息
                    log_thread.join(timeout=3)
                    out = "\n".join(captured_lines[-60:]) if captured_lines else "(no output captured)"
                raise RuntimeError(
                    f"Agent-TARS exited early (rc={proc.returncode}).\nOutput:\n{out}"
                )

            # 只在 verbose_log=true 模式下读取 stdout（精简模式下由过滤线程处理）
            if verbose_log:
                try:
                    if proc.stdout:
                        for _ in range(5):
                            line = proc.stdout.readline()
                            if not line:
                                break
                            last_lines.append(line.rstrip("\n"))
                            if len(last_lines) > 80:
                                last_lines = last_lines[-80:]
                except Exception:
                    pass

            if is_tars_alive(server_url, timeout_s=0.8):
                print(f"✅ Agent-TARS is ready: {server_url}")
                return proc

            time.sleep(0.5)

        # Timeout: include recent logs
        tail = "\n".join(last_lines[-40:]) if last_lines else "(logs captured by filter thread)"
        raise TimeoutError(
            f"Agent-TARS not ready after {ready_timeout_s}s. "
            f"health={_health_url(server_url)}\nRecent logs:\n{tail}"
        )

    return proc


if __name__ == "__main__":
    start_agent_tars("conf/config.yaml", wait_ready=True)
