#!/usr/bin/env python3
"""
run_verifier_server.py — Verifier Flask server (gunicorn multi-worker)

外层接口与之前完全一致：gunicorn N workers，REST API 不变。

Agent-TARS 自动扩容（设 AUTO_START_AGENT_TARS=true）：
  - 启动时按 worker 数量自动拉起 N 个 Chrome+TARS 实例
  - 通过 gunicorn post_fork hook 给每个 worker 分配独占的 TARS 实例
  - 主进程后台 watchdog 线程定期检查所有 TARS 实例健康状态并自动重启
  - 对 agent_type 非 agent_tars 的请求无任何影响

无 AUTO_START_AGENT_TARS 时行为与之前完全相同。

用法：
  # 与之前完全一样（只处理 browser-use / static 类型）
  python run_verifier_server.py --port 5001 --workers 4

  # 启用 TARS pool（自动拉起 N 个 TARS 实例）
  AUTO_START_AGENT_TARS=true python run_verifier_server.py --port 5001 --workers 4

  # 指定 TARS/CDP 起始端口（多台机器并发时避免冲突）
  AUTO_START_AGENT_TARS=true python run_verifier_server.py \\
      --port 5001 --workers 4 \\
      --tars-base-port 8890 --cdp-base-port 9225 \\
      --watchdog-interval 30
"""

import argparse
import atexit
import glob
import multiprocessing
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from verifier.app import app

# ──────────────────────────────────────────────────────────────────────────────
# TARS Pool global state (lives in master process)
# ──────────────────────────────────────────────────────────────────────────────

# Each entry: {"index": i, "cdp_port": int, "tars_port": int,
#              "chrome_proc": Popen|None, "tars_proc": Popen|None}
_tars_instances: List[dict] = []

# Shared atomic counter for post_fork slot assignment.
# Allocated before fork → visible in all worker processes.
_worker_slot_counter: Optional[multiprocessing.Value] = None

# Protects per-instance restart in watchdog (master only, no fork concern)
_restart_lock = threading.Lock()

_config_path: str = "conf/config.yaml"
_watchdog_stop = threading.Event()


# ──────────────────────────────────────────────────────────────────────────────
# Process helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cleanup_stale_tmp() -> None:
    # Chrome's SingletonLock is a symlink whose target encodes the hostname that
    # acquired it. If a lock was baked into the image, a fresh container whose
    # hostname differs triggers "profile in use by another Chromium process on
    # another computer" and Chrome exits before CDP comes up.
    patterns = [
        "/tmp/tars-chromium-*",
        "/tmp/agent-tars-storage-*",
        "/tmp/agent-tars-config-*.json",
    ]
    removed = 0
    for pat in patterns:
        for path in glob.glob(pat):
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"Cleaned {removed} stale /tmp entries (tars-chromium / agent-tars-*)")


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
        print(f"Warning: could not terminate {name}: {e}")


def _cleanup_tars_pool():
    """Stop all TARS instances (called at exit)."""
    _watchdog_stop.set()
    for inst in _tars_instances:
        i = inst["index"]
        _terminate(inst.get("tars_proc"), f"Agent-TARS[{i}]")
        _terminate(inst.get("chrome_proc"), f"Chrome[{i}]")
    print("TARS pool stopped.")


def _cleanup_browser_procs():
    """Kill stray dev server and playwright browser processes."""
    for pattern in ["node.*vite|http.server", "chromium|chrome.*headless"]:
        try:
            result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
            for pid in result.stdout.strip().split('\n'):
                if pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except (ProcessLookupError, ValueError):
                        pass
        except Exception:
            pass


_shutting_down = False

def signal_handler(signum, frame):
    global _shutting_down
    if _shutting_down:
        # Second signal → kill entire process group (master + workers)
        os.killpg(0, signal.SIGKILL)
    _shutting_down = True
    print(f"\nCaught signal {signum}, shutting down ...")
    _cleanup_tars_pool()
    _cleanup_browser_procs()
    # Kill entire process group so gunicorn workers don't survive as orphans
    os.killpg(0, signal.SIGTERM)
    time.sleep(1)
    os._exit(0)


signal.signal(signal.SIGINT,  signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ──────────────────────────────────────────────────────────────────────────────
# Start / restart individual TARS instance
# ──────────────────────────────────────────────────────────────────────────────

def _start_one_instance(inst: dict, wait_ready: bool = True) -> bool:
    """
    Start (or restart) Chrome + Agent-TARS for one instance.
    Sets env vars briefly under lock to launch subprocesses, then waits
    for readiness outside the lock so multiple instances can start in parallel.
    """
    from start_chrome_cdp import start_chrome, is_cdp_alive
    from start_agent_tars import start_agent_tars, is_tars_alive

    i          = inst["index"]
    cdp_port   = inst["cdp_port"]
    tars_port  = inst["tars_port"]
    server_url = f"http://127.0.0.1:{tars_port}"

    need_chrome = not is_cdp_alive(cdp_port)
    need_tars   = not is_tars_alive(server_url)

    if not need_chrome and not need_tars:
        return True

    # Hold lock only for env-var mutation + subprocess launch (fast).
    with _restart_lock:
        saved_env = {
            "AGENT_TARS_SERVE_PORT":  os.environ.get("AGENT_TARS_SERVE_PORT"),
            "AGENT_TARS_SERVER_URL":  os.environ.get("AGENT_TARS_SERVER_URL"),
            "AGENT_CDP_PORT":         os.environ.get("AGENT_CDP_PORT"),
            "AGENT_TARS_CDP_ENDPOINT":os.environ.get("AGENT_TARS_CDP_ENDPOINT"),
        }
        os.environ["AGENT_TARS_SERVE_PORT"]   = str(tars_port)
        os.environ["AGENT_TARS_SERVER_URL"]   = f"http://localhost:{tars_port}"
        os.environ["AGENT_CDP_PORT"]           = str(cdp_port)
        os.environ["AGENT_TARS_CDP_ENDPOINT"] = f"http://127.0.0.1:{cdp_port}/json/version"

        try:
            if need_chrome:
                print(f"  [instance {i}] Starting Chrome CDP (port {cdp_port}) ...")
                try:
                    inst["chrome_proc"] = start_chrome(_config_path, wait_ready=False)
                except RuntimeError as e:
                    print(f"  [instance {i}] Chrome: {e}")
                    need_chrome = False  # already running

            if need_tars:
                print(f"  [instance {i}] Starting Agent-TARS (port {tars_port}) ...")
                try:
                    inst["tars_proc"] = start_agent_tars(_config_path, wait_ready=False)
                except RuntimeError as e:
                    print(f"  [instance {i}] Agent-TARS: {e}")
                    need_tars = False  # already running
                except Exception as e:
                    print(f"  [instance {i}] Agent-TARS start failed: {e}")
                    return False
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # Wait for readiness outside the lock (the slow part).
    if wait_ready:
        if need_chrome:
            # Cold Chrome startup on the cluster (2 CPU, 8GB) can take 20-40s.
            # 15s was too tight; 60s gives margin without delaying healthy paths.
            t0 = time.time()
            chrome_wait_s = 60
            while time.time() - t0 < chrome_wait_s:
                if is_cdp_alive(cdp_port):
                    print(f"  [instance {i}] Chrome ready")
                    break
                time.sleep(0.3)
            else:
                print(f"  [instance {i}] Chrome failed to become ready in {chrome_wait_s}s")
                return False

        if need_tars:
            t0 = time.time()
            while time.time() - t0 < 300:
                if is_tars_alive(server_url):
                    print(f"  [instance {i}] Agent-TARS ready at {server_url}")
                    break
                time.sleep(0.5)
            else:
                print(f"  [instance {i}] Agent-TARS failed to become ready in 300s")
                return False

    return True


def _force_restart_instance(inst: dict) -> bool:
    """Terminate and restart one Chrome+TARS instance."""
    i = inst["index"]
    print(f"[watchdog] Force-restarting instance {i} ...")
    _terminate(inst.get("tars_proc"), f"Agent-TARS[{i}]"); inst["tars_proc"] = None
    _terminate(inst.get("chrome_proc"), f"Chrome[{i}]");   inst["chrome_proc"] = None
    time.sleep(2)
    return _start_one_instance(inst, wait_ready=True)


# ──────────────────────────────────────────────────────────────────────────────
# TARS pool startup
# ──────────────────────────────────────────────────────────────────────────────

def _is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_available_ports(base_port: int, count: int) -> List[int]:
    """
    Find `count` consecutive-ish available ports starting from base_port.
    Skips occupied ports and keeps searching upward.
    """
    ports = []
    candidate = base_port
    while len(ports) < count and candidate < 65535:
        if not _is_port_in_use(candidate):
            ports.append(candidate)
        candidate += 1
    return ports


def _start_tars_pool(num_workers: int, tars_base_port: int, cdp_base_port: int):
    """
    Start num_workers Chrome+TARS instances in parallel before gunicorn forks.
    Populates _tars_instances.
    """
    global _tars_instances

    # Find available ports (skip occupied ones instead of killing)
    cdp_ports = _find_available_ports(cdp_base_port, num_workers)
    tars_ports = _find_available_ports(tars_base_port, num_workers)

    if len(cdp_ports) < num_workers or len(tars_ports) < num_workers:
        print(f"❌ Cannot find enough free ports for {num_workers} instances — aborting TARS pool startup")
        return

    print(f"Starting TARS pool: {num_workers} instances "
          f"(TARS ports {tars_ports}, CDP ports {cdp_ports})")

    # Build instance list
    for i in range(num_workers):
        inst = {
            "index":      i,
            "cdp_port":   cdp_ports[i],
            "tars_port":  tars_ports[i],
            "chrome_proc": None,
            "tars_proc":   None,
        }
        _tars_instances.append(inst)

    # Start all instances in parallel
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        futures = {
            pool.submit(_start_one_instance, inst, True): inst
            for inst in _tars_instances
        }
        for future in as_completed(futures):
            inst = futures[future]
            try:
                ok = future.result()
                if not ok:
                    print(f"  [instance {inst['index']}] startup failed")
            except Exception as e:
                print(f"  [instance {inst['index']}] startup error: {e}")

    ready = sum(
        1 for inst in _tars_instances
        if _is_instance_healthy(inst)
    )
    elapsed = time.time() - t0
    print(f"TARS pool ready: {ready}/{num_workers} instances healthy ({elapsed:.1f}s)")


def _is_instance_healthy(inst: dict) -> bool:
    from start_chrome_cdp import is_cdp_alive
    from start_agent_tars import is_tars_alive
    server_url = f"http://127.0.0.1:{inst['tars_port']}"
    return is_cdp_alive(inst["cdp_port"], check_pages=False) and is_tars_alive(server_url)


# ──────────────────────────────────────────────────────────────────────────────
# Watchdog (runs as daemon thread in master process only)
# ──────────────────────────────────────────────────────────────────────────────

def _watchdog_loop(interval_s: int):
    """
    Periodically check all TARS instances and restart unhealthy ones.
    Runs in master process. Threads do NOT survive fork, so workers are unaffected.
    """
    try:
        print(f"[watchdog] Started (interval={interval_s}s, instances={len(_tars_instances)})")
    except Exception:
        pass
    while not _watchdog_stop.wait(timeout=interval_s):
        if _watchdog_stop.is_set():
            break
        for inst in list(_tars_instances):
            if _watchdog_stop.is_set():
                break
            try:
                if not _is_instance_healthy(inst):
                    print(f"[watchdog] Instance {inst['index']} unhealthy, restarting ...")
                    ok = _force_restart_instance(inst)
                    print(f"[watchdog] Instance {inst['index']} restart {'ok' if ok else 'failed'}")
            except Exception:
                pass  # suppress errors during shutdown


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Verifier Flask Server (gunicorn)")
    parser.add_argument("--port", "-p", type=int, default=None,
                        help="Listen port (default: PORT env or 8301)")
    parser.add_argument("--workers", "-w", type=int, default=None,
                        help="Number of gunicorn workers (default: WORKERS env or cpu_count)")
    parser.add_argument("--tars-base-port", type=int, default=None, dest="tars_base_port",
                        help="First TARS serve port (default: TARS_BASE_PORT env or 8890)")
    parser.add_argument("--cdp-base-port", type=int, default=None, dest="cdp_base_port",
                        help="First Chrome CDP port (default: CDP_BASE_PORT env or 9225)")
    parser.add_argument("--watchdog-interval", type=int, default=30, dest="watchdog_interval",
                        help="TARS health-check interval in seconds (default: 30)")
    args = parser.parse_args()

    port    = args.port    if args.port    is not None else int(os.environ.get('PORT',    5001))
    workers = args.workers if args.workers is not None else int(os.environ.get('WORKERS', multiprocessing.cpu_count()))
    debug   = os.environ.get('DEBUG', 'False').lower() == 'true'
    host    = os.environ.get('HOST', '0.0.0.0')
    auto_start_tars = os.environ.get("AUTO_START_AGENT_TARS", "false").lower() in ("1", "true", "yes")

    tars_base_port = (args.tars_base_port if args.tars_base_port is not None
                      else int(os.environ.get("TARS_BASE_PORT", 8890)))
    cdp_base_port  = (args.cdp_base_port  if args.cdp_base_port  is not None
                      else int(os.environ.get("CDP_BASE_PORT",  9225)))

    print(f"Verifier server: {host}:{port}  workers={workers}  debug={debug}")
    print(f"auto_start_agent_tars={auto_start_tars}" +
          (f"  tars_ports={tars_base_port}–{tars_base_port+workers-1}"
           f"  cdp_ports={cdp_base_port}–{cdp_base_port+workers-1}"
           if auto_start_tars else ""))

    # ── TARS pool startup (before gunicorn forks) ──────────────────────────
    if auto_start_tars and not debug:
        from start_chrome_cdp import is_cdp_alive, start_chrome, resolve_env_vars
        from start_agent_tars import is_tars_alive, start_agent_tars

        _cleanup_stale_tmp()

        atexit.register(_cleanup_tars_pool)
        _start_tars_pool(workers, tars_base_port, cdp_base_port)

        # Background watchdog thread (master process only; threads don't survive fork)
        wt = threading.Thread(
            target=_watchdog_loop, args=(args.watchdog_interval,), daemon=True
        )
        wt.start()

        # Shared atomic counter for post_fork slot assignment
        _worker_slot_counter = multiprocessing.Value('i', 0)
        _n_instances = len(_tars_instances)

    # ── Gunicorn ───────────────────────────────────────────────────────────
    if debug:
        print("Starting Flask dev server (single worker, debug mode) ...")
        app.run(host=host, port=port, debug=True)
    else:
        import gunicorn.app.base

        class StandaloneApplication(gunicorn.app.base.BaseApplication):
            def __init__(self, application, options=None):
                self.options = options or {}
                self.application = application
                super().__init__()

            def load_config(self):
                for key, value in self.options.items():
                    if key in self.cfg.settings and value is not None:
                        self.cfg.set(key.lower(), value)

            def load(self):
                return self.application

        def _post_fork(server, worker):
            """
            Called in each new worker process after fork.
            Assigns this worker a dedicated TARS instance by slot.
            """
            if not auto_start_tars or _worker_slot_counter is None or _n_instances == 0:
                return
            with _worker_slot_counter.get_lock():
                slot = _worker_slot_counter.value % _n_instances
                _worker_slot_counter.value = slot + 1
            inst = _tars_instances[slot]
            tars_url = f"http://127.0.0.1:{inst['tars_port']}"
            os.environ["AGENT_TARS_SERVER_URL"] = tars_url
            os.environ["AGENT_CDP_PORT"]        = str(inst['cdp_port'])
            print(f"[worker {worker.pid}] assigned TARS slot {slot} → {tars_url}  CDP:{inst['cdp_port']}")

        options = {
            'bind':                f'{host}:{port}',
            'workers':             workers,
            'worker_class':        'sync',
            'timeout':             3600,
            'keepalive':           5,
            'max_requests':        1000,
            'max_requests_jitter': 50,
            'preload_app':         True,   # load app before fork so post_fork env takes effect
            'post_fork':           _post_fork,
        }

        print(f"Starting gunicorn with {workers} workers ...")
        StandaloneApplication(app, options).run()
