#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

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


def get_playwright_chromium_path() -> str:
    """
    获取 playwright 管理的 Chromium 可执行文件路径。

    需要提前安装：pip install playwright && playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
            if not Path(path).exists():
                raise FileNotFoundError(
                    f"Playwright Chromium 未找到: {path}\n"
                    f"请运行: playwright install chromium"
                )
            return path
    except ImportError:
        raise RuntimeError(
            "playwright 未安装，请运行:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )


def is_cdp_alive(port: int = 9225, timeout_s: float = 0.8, check_pages: bool = False) -> bool:
    """
    检测 Chrome CDP 是否存活。
    
    Args:
        port: CDP 端口
        timeout_s: 请求超时时间
        check_pages: 是否检查有活跃页面（更严格的检测）
    
    Returns:
        True: Chrome CDP 存活
        False: Chrome CDP 不存活，或无活跃页面（当 check_pages=True）
    """
    try:
        # 基础检测：/json/version
        r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout_s)
        if r.status_code != 200:
            return False
        
        # 可选：检测是否有活跃页面
        if check_pages:
            r2 = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=timeout_s)
            if r2.status_code != 200:
                return False
            pages = r2.json()
            # 至少有一个非空页面（排除 about:blank 等）
            has_active_page = any(
                p.get("type") == "page" 
                for p in pages
            )
            return has_active_page
        
        return True
    except Exception:
        return False


def start_chrome(
    config_path: str = "conf/config.yaml",
    *,
    wait_ready: bool = True,
    ready_timeout_s: int = 15,
) -> subprocess.Popen:
    root = Path(__file__).resolve().parent
    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.yaml not found: {cfg_path}")

    cfg = load_cfg(cfg_path)
    agent_cfg: Dict[str, Any] = (cfg.get("evaluators") or {}).get("agent_tars") or {}

    chrome_path = get_playwright_chromium_path()
    cdp_port = int(agent_cfg.get("cdp_port", 9225))
    default_user_data_dir = f"/tmp/tars-chromium-{cdp_port}"
    user_data_dir = str(agent_cfg.get("chrome_user_data_dir", default_user_data_dir))
    extra_args = agent_cfg.get("chrome_extra_args", [])

    if is_cdp_alive(cdp_port):
        print(f"✅ Chrome CDP already alive at 127.0.0.1:{cdp_port}, skip start.")
        raise RuntimeError(f"Chrome already running on port {cdp_port}")

    cmd = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={user_data_dir}",

        # 无头模式与视口
        "--headless=new",
        "--window-size=1280,900",

        # 容器/root 沙箱（Linux 容器必需，macOS 无副作用）
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",      # 防容器 /dev/shm 过小导致崩溃

        # 反自动化检测（防部分网站检测 CDP 并拦截）
        "--disable-blink-features=AutomationControlled",

        # 证书 & 内容安全（测试环境放宽限制）
        "--ignore-certificate-errors",
        "--allow-running-insecure-content",

        # 启动清洁化
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",

        # 防无头模式下后台 throttling（影响截图时序与坐标准确性）
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",

        # 自动化高频操作时防 IPC 限频
        "--disable-ipc-flooding-protection",

        # 截图颜色一致性
        "--force-color-profile=srgb",
    ]
    if isinstance(extra_args, list):
        cmd += [str(x) for x in extra_args]

    print("🚀 Launching Playwright Chromium for CDP:")
    print("  chromium_path:", chrome_path)
    print("  cdp_port:", cdp_port)
    print("  user_data_dir:", user_data_dir)
    print("  cmd:", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        cwd=str(root),
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if wait_ready:
        t0 = time.time()
        while time.time() - t0 < ready_timeout_s:
            if proc.poll() is not None:
                raise RuntimeError("Chrome exited early.")
            if is_cdp_alive(cdp_port):
                print(f"✅ Chrome CDP is ready: http://127.0.0.1:{cdp_port}/json/version")
                break
            time.sleep(0.3)

    return proc


if __name__ == "__main__":
    start_chrome("conf/config.yaml", wait_ready=True)
