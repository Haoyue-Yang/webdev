#!/usr/bin/env python3
"""
run_tars_daemon.py — Agent-TARS 生命周期守护进程

职责：
  - 启动 Chrome CDP 进程
  - 启动 Agent-TARS Node.js 服务
  - watchdog 定期检查两者健康状态，自动重启

与 run_verifier_server.py 完全解耦：
  - run_verifier_server.py 以 gunicorn 多 worker 启动，不感知 TARS 进程
  - 本脚本独立管理 Chrome + TARS，verifier worker 通过 TARSAgentClient HTTP 调用 TARS

多实例批量评估时，为每个实例分配不同端口：
  python run_tars_daemon.py --tars-port 8891 --cdp-port 9226
  python run_tars_daemon.py --tars-port 8892 --cdp-port 9227
  ...

用法：
  # 启动 TARS daemon（端口从 config.yaml 读取）
  python run_tars_daemon.py

  # 覆盖端口（多实例）
  python run_tars_daemon.py --tars-port 8891 --cdp-port 9226

  # 调整 watchdog 检查间隔（秒，默认 30）
  python run_tars_daemon.py --watchdog-interval 20
"""

import argparse
import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import yaml

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))


from start_chrome_cdp import is_cdp_alive, start_chrome, resolve_env_vars
from start_agent_tars import is_tars_alive, start_agent_tars


# ──────────────────────────────────────────────────────────────────────────────
# Global state
# ──────────────────────────────────────────────────────────────────────────────
chrome_proc: Optional[object] = None
tars_proc:   Optional[object] = None

_config_path: str = "conf/config.yaml"
_cdp_port:    int = 9225
_serve_port:  int = 8890


# ──────────────────────────────────────────────────────────────────────────────
# Process helpers
# ──────────────────────────────────────────────────────────────────────────────

def _terminate(proc, name: str):
    if not proc:
        return
    try:
        if getattr(proc, "poll", lambda: None)() is None:
            print(f"Terminating {name} (pid={proc.pid}) ...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    except Exception as e:
        print(f"Warning: failed to terminate {name}: {e}")


def _cleanup():
    global chrome_proc, tars_proc
    print("Shutting down Chrome + Agent-TARS ...")
    _terminate(tars_proc, "Agent-TARS")
    _terminate(chrome_proc, "Chrome")
    tars_proc = None
    chrome_proc = None
    print("Done.")


# ──────────────────────────────────────────────────────────────────────────────
# Start / restart logic
# ──────────────────────────────────────────────────────────────────────────────

def _start_runtime(force: bool = False) -> dict:
    """
    Start or restart Chrome + Agent-TARS.

    force=True: terminate existing processes before restarting.
    force=False: skip if both are already alive.

    Returns a status dict.
    """
    global chrome_proc, tars_proc

    server_url = f"http://127.0.0.1:{_serve_port}"
    status = {}

    chrome_alive = is_cdp_alive(_cdp_port, check_pages=True)
    tars_alive   = is_tars_alive(server_url)

    if not force and chrome_alive and tars_alive:
        print(f"Chrome (port {_cdp_port}) and Agent-TARS ({server_url}) are already running.")
        return {"chrome": "already_running", "tars": "already_running"}

    # ── Stop existing processes ─────────────────────────────────────────────
    if force or not chrome_alive:
        if tars_alive:
            # TARS is alive but Chrome is not: TARS's CDP connection is broken.
            # Kill the stale TARS process by port pattern.
            print(f"Chrome (port {_cdp_port}) is down; force-killing stale Agent-TARS (port {_serve_port})...")
            subprocess.run(
                ["pkill", "-f", f"agent-tars.*--port.*{_serve_port}"],
                capture_output=True,
            )
            time.sleep(1)
        print("Stopping Chrome ..." if not force else "Force-stopping Chrome + Agent-TARS ...")
        _terminate(tars_proc, "Agent-TARS"); tars_proc = None
        _terminate(chrome_proc, "Chrome");  chrome_proc = None
        time.sleep(2)
    elif force:
        print("Force-stopping Chrome + Agent-TARS ...")
        _terminate(tars_proc, "Agent-TARS"); tars_proc = None
        _terminate(chrome_proc, "Chrome");  chrome_proc = None
        time.sleep(2)

    # ── Start Chrome ────────────────────────────────────────────────────────
    print(f"Starting Chrome CDP (port {_cdp_port}) ...")
    try:
        chrome_proc = start_chrome(_config_path, wait_ready=True)
        status["chrome"] = "started"
        print(f"Chrome CDP ready (port {_cdp_port})")
    except RuntimeError as e:
        print(f"Chrome: {e}")
        status["chrome"] = "already_running"
    except Exception as e:
        print(f"Chrome start failed: {e}")
        status["chrome"] = f"failed: {e}"
        return status   # cannot start TARS without Chrome

    # ── Start Agent-TARS ────────────────────────────────────────────────────
    print(f"Starting Agent-TARS ({server_url}) ...")
    try:
        tars_proc = start_agent_tars(_config_path, wait_ready=True)
        status["tars"] = "started"
        print(f"Agent-TARS ready at {server_url}")
    except RuntimeError as e:
        print(f"Agent-TARS: {e}")
        status["tars"] = "already_running"
    except TimeoutError as e:
        print(f"Agent-TARS start timed out: {e}")
        status["tars"] = f"timeout: {e}"
    except Exception as e:
        print(f"Agent-TARS start failed: {e}")
        status["tars"] = f"failed: {e}"

    return status


def _check_and_restart() -> dict:
    """
    Health check: if Chrome or TARS is down, restart both.
    Called periodically by the watchdog loop.
    """
    global chrome_proc, tars_proc

    server_url    = f"http://127.0.0.1:{_serve_port}"
    chrome_alive  = is_cdp_alive(_cdp_port, check_pages=True)
    tars_alive    = is_tars_alive(server_url)

    if chrome_alive and tars_alive:
        return {"chrome": "alive", "tars": "alive"}

    reason = []
    if not chrome_alive: reason.append(f"Chrome CDP (port {_cdp_port})")
    if not tars_alive:   reason.append(f"Agent-TARS ({server_url})")
    print(f"[watchdog] Service down: {', '.join(reason)} — restarting ...")

    return _start_runtime(force=True)


# ──────────────────────────────────────────────────────────────────────────────
# Watchdog loop
# ──────────────────────────────────────────────────────────────────────────────

def _run_watchdog(interval_s: int):
    """
    Blocking watchdog loop. Checks service health every `interval_s` seconds.
    Exits on SIGINT / SIGTERM (set by signal handlers before calling this).
    """
    server_url = f"http://127.0.0.1:{_serve_port}"
    print(f"[watchdog] Started (interval={interval_s}s)  Chrome port={_cdp_port}  TARS={server_url}")
    while True:
        time.sleep(interval_s)
        try:
            status = _check_and_restart()
            if status not in ({"chrome": "alive", "tars": "alive"},):
                print(f"[watchdog] Restart result: {status}")
        except Exception as e:
            print(f"[watchdog] Error during health check: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Signal handling
# ──────────────────────────────────────────────────────────────────────────────

def _sig_handler(sig, frame):
    print(f"\nCaught signal {sig}, shutting down daemon ...")
    _cleanup()
    sys.exit(0)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Agent-TARS Lifecycle Daemon (Chrome CDP + Agent-TARS watchdog)"
    )
    parser.add_argument(
        "--tars-port", type=int, default=None, dest="tars_port",
        help="Agent-TARS serve port, overrides config.yaml serve_port",
    )
    parser.add_argument(
        "--cdp-port", type=int, default=None, dest="cdp_port",
        help="Chrome CDP port, overrides config.yaml cdp_port",
    )
    parser.add_argument(
        "--watchdog-interval", type=int, default=30, dest="watchdog_interval",
        help="Health check interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--config", type=str, default="conf/config.yaml",
        help="Path to config.yaml (default: conf/config.yaml)",
    )
    args = parser.parse_args()

    _config_path = args.config

    # Propagate CLI port overrides into env so ${VAR||default} picks them up
    if args.tars_port is not None:
        os.environ["AGENT_TARS_SERVE_PORT"]  = str(args.tars_port)
        os.environ["AGENT_TARS_SERVER_URL"]  = f"http://localhost:{args.tars_port}"
    if args.cdp_port is not None:
        os.environ["AGENT_CDP_PORT"]           = str(args.cdp_port)
        os.environ["AGENT_TARS_CDP_ENDPOINT"]  = f"http://127.0.0.1:{args.cdp_port}/json/version"

    # Resolve ports from config (after env overrides)
    try:
        cfg_path = Path(_config_path)
        if not cfg_path.is_absolute():
            cfg_path = project_root / _config_path
        with open(cfg_path, "r") as f:
            cfg = yaml.safe_load(f)
        cfg = resolve_env_vars(cfg)
        agent_cfg   = (cfg.get("evaluators") or {}).get("agent_tars") or {}
        _cdp_port   = int(agent_cfg.get("cdp_port",   9225))
        _serve_port = int(agent_cfg.get("serve_port", 8890))
    except Exception as e:
        print(f"Warning: could not read ports from config ({e}), using defaults cdp={_cdp_port} tars={_serve_port}")

    server_url = f"http://127.0.0.1:{_serve_port}"

    print("=" * 56)
    print("Agent-TARS Daemon")
    print(f"  config              : {_config_path}")
    print(f"  Chrome CDP port     : {_cdp_port}")
    print(f"  Agent-TARS port     : {_serve_port}  ({server_url})")
    print(f"  watchdog interval   : {args.watchdog_interval}s")
    print("=" * 56)

    # Register cleanup
    atexit.register(_cleanup)
    signal.signal(signal.SIGINT,  _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    # Initial startup
    _start_runtime(force=False)

    # Watchdog loop (blocks forever)
    _run_watchdog(args.watchdog_interval)
