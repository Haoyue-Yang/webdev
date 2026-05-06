from playwright.sync_api import sync_playwright, Error, TimeoutError
from utils.config import config
import urllib.parse
from typing import Tuple, Optional, List, Dict
from pathlib import Path
import json
import os
import mimetypes
import datetime

MIRROR_MAP = {
        "cdnjs.cloudflare.com": "cdnjs.loli.net",
        "fonts.googleapis.com": "fonts.loli.net",
        "fonts.gstatic.com": "gstatic.loli.net",
    }

# Extended set of CDN domains to cache (beyond MIRROR_MAP)
CACHE_DOMAINS = list(MIRROR_MAP.keys()) + [
    "cdn.tailwindcss.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
]

def is_screenshot_blank_or_minimal(screenshot_path: Path) -> bool:
    """
    Analyze if a screenshot appears to be blank or has minimal content.
    Returns True if the screenshot is likely a blank/error page.
    """
    try:
        from PIL import Image
        import numpy as np
        
        # Open and analyze the image
        with Image.open(screenshot_path) as img:
            # Convert to RGB if needed
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Convert to numpy array for analysis
            img_array = np.array(img)
            
            # Calculate color variance (low variance = mostly single color)
            variance = np.var(img_array)
            
            # Calculate unique colors (very few = likely blank)
            unique_colors = len(np.unique(img_array.reshape(-1, img_array.shape[-1]), axis=0))
            
            # Get image dimensions
            width, height = img.size
            total_pixels = width * height
            
            # Criteria for blank/minimal content:
            # 1. Very low color variance (< 100)
            # 2. Very few unique colors (< 10)
            # 3. OR predominantly white/light colored
            
            is_low_variance = variance < 100
            is_few_colors = unique_colors < 10
            
            # Check if predominantly white/light (average color > 240 on 0-255 scale)
            avg_color = np.mean(img_array)
            is_mostly_white = avg_color > 240
            
            # Consider blank if any of these conditions are met
            is_blank = is_low_variance or (is_few_colors and is_mostly_white)
            
            return bool(is_blank)
            
    except Exception as e:
        print(f"Warning: Could not analyze screenshot content: {e}")
        return False

def get_fallback_mime_type(url: str) -> str:
    """
    Infer the correct MIME type from a URL path (query parameters stripped).

    Fixes:
    - Some Windows registry anomalies misidentify .js as text/plain
    - mimetypes.guess_type fails on URLs with query strings
    - Missing entries for web fonts
    - Extension-less CDN URLs (e.g. cdn.tailwindcss.com) that serve JavaScript
    """
    clean_path = urllib.parse.urlparse(url).path
    mime_type, _ = mimetypes.guess_type(clean_path)

    # Fix: some Windows registry entries map .js -> text/plain
    if mime_type == 'text/plain' and clean_path.endswith('.js'):
        mime_type = 'application/javascript'

    # Enhanced fallback inference
    if not mime_type:
        if 'fonts.googleapis.com/css' in url or clean_path.endswith('.css'):
            return "text/css"
        elif clean_path.endswith('.js'):
            return "application/javascript"
        elif clean_path.endswith('.woff2'):
            return "font/woff2"
        elif clean_path.endswith('.woff'):
            return "font/woff"
        elif clean_path.endswith('.ttf'):
            return "font/ttf"
        # Extension-less CDN URLs: infer by known domain
        # cdn.tailwindcss.com serves the Tailwind CSS browser runtime (JavaScript)
        elif 'cdn.tailwindcss.com' in url:
            return "application/javascript"
        # jsdelivr and unpkg typically serve JS/CSS; default to JS for path-less URLs
        elif 'cdn.jsdelivr.net' in url or 'unpkg.com' in url:
            return "application/javascript"
        return "application/octet-stream"

    return mime_type


def take_screenshot_with_logs(url: str, output_path: str, timeout: int = 15000, save_logs: bool = True) -> Tuple[bool, Optional[str], List[Dict]]:
    """
    Takes a screenshot of a given URL using Playwright and collects console logs.

    Args:
        url: The URL to screenshot.
        output_path: The file path to save the screenshot to.
        timeout: The timeout in milliseconds (default: 30 seconds).

    Returns:
        A tuple (success, error_message, console_logs).
    """
    ASSETS_DIR = Path(os.environ["BROWSER_CACHE_PATH"])
    # ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Taking screenshot of {url} and collecting console logs...")
    console_logs = []
    
    try:
        with sync_playwright() as p:
            proxy_config = config.get('playwright', {}).get('proxy')
            proxy_url = proxy_config.get('server', '') if proxy_config else None
            proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
            
            use_full_chromium = config.get("evaluators", {}).get("aesthetics_functional", {}).get("use_full_chromium", False)

            base_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            if use_full_chromium:
                # Full Chromium: has SwiftShader built-in, supports WebGL natively
                launch_config = {
                    "headless": True,
                    "channel": "chromium",
                    "args": base_args,
                }
                print("Using full Chromium (WebGL supported via SwiftShader)")
            else:
                # Headless shell: lighter but no GPU, add software WebGL fallback
                launch_config = {
                    "headless": True,
                    "args": base_args + ["--use-gl=angle", "--use-angle=swiftshader-webgl"],
                }
                print("Using headless shell with SwiftShader WebGL fallback")

            browser = p.chromium.launch(**launch_config)
            page = browser.new_page()
            
            import requests as req_lib

            use_intercept_browser = config["evaluators"]["aesthetics_functional"].get("use_intercept_browser", False)

            def handle_request_with_cache(route):
                request_url = route.request.url
                # 1. Skip local/internal resources (configured via LOCAL_DOMAINS env var)
                local_domains = os.environ.get("LOCAL_DOMAINS", "localhost,127.0.0.1").split(",")
                if any(local in request_url for local in local_domains):
                    return route.continue_()

                # 2. Check if the URL matches any CDN we want to cache
                is_cache_target = any(domain in request_url for domain in CACHE_DOMAINS)

                if is_cache_target:
                    # Normalize URL: strip trailing slash so that
                    # "https://cdn.tailwindcss.com" and "https://cdn.tailwindcss.com/"
                    # map to the same cache filename.
                    normalized_url = request_url.rstrip('/')
                    # Sanitize filename: replace /, ?, =, & and enforce 250-char limit
                    filename = normalized_url \
                        .replace("https://", "").replace("http://", "") \
                        .replace("/", "_").replace("?", "_") \
                        .replace("=", "_").replace("&", "_")
                    if len(filename) > 250:
                        filename = filename[:250]

                    local_file = ASSETS_DIR / filename

                    # Backward-compat: if the normalized filename doesn't exist, also
                    # check the old name that had a trailing underscore from a trailing
                    # slash (e.g. "cdn.tailwindcss.com_" cached before this fix).
                    if not local_file.exists():
                        old_file = ASSETS_DIR / (filename + '_')
                        if old_file.exists():
                            local_file = old_file

                    # Determine MIME type once for both cache-hit and fresh-download paths
                    mime_type = get_fallback_mime_type(request_url)

                    # Layer 1: Return from local cache if exists
                    if local_file.exists():
                        print(f"Local cache hit: {filename}...")
                        return route.fulfill(
                            status=200,
                            body=local_file.read_bytes(),
                            content_type=mime_type
                        )

                    # Layer 2: Try fetching from a faster/stable mirror or origin
                    target_url = request_url
                    for origin, mirror in MIRROR_MAP.items():
                        if origin in request_url:
                            target_url = request_url.replace(origin, mirror)
                            break

                    try:
                        print(f"Downloading to cache: {target_url}")
                        resp = req_lib.get(target_url, timeout=15)
                        if resp.status_code == 200:
                            local_file.write_bytes(resp.content)
                            return route.fulfill(
                                status=200,
                                body=resp.content,
                                content_type=mime_type
                            )
                    except Exception as e:
                        print(f"Cache download failed for {target_url}: {type(e).__name__}. Falling back to proxy/direct.")

                # Layer 3: Original proxy logic or direct connection
                if proxy_config:
                    try:
                        response = req_lib.get(request_url, proxies=proxies, timeout=10)
                        route.fulfill(
                            status=response.status_code,
                            headers=dict(response.headers),
                            body=response.content
                        )
                    except Exception as e:
                        print(f"Proxy failed for {request_url[:50]}...: {type(e).__name__}")
                        route.abort()
                else:
                    route.continue_()
            
            def handle_request(route):
                request_url = route.request.url
                # 1. Skip local/internal resources (configured via LOCAL_DOMAINS env var)
                local_domains = os.environ.get("LOCAL_DOMAINS", "localhost,127.0.0.1").split(",")
                if any(local in request_url for local in local_domains):
                    return route.continue_()

                if proxy_config:
                    try:
                        response = req_lib.get(request_url, proxies=proxies, timeout=10)
                        route.fulfill(
                            status=response.status_code,
                            headers=dict(response.headers),
                            body=response.content
                        )
                    except Exception as e:
                        print(f"Proxy failed for {request_url[:50]}...: {type(e).__name__}")
                        route.abort()
                else:
                    route.continue_()

            # Enable request routing
            if use_intercept_browser:
                page.route("**/*", handle_request_with_cache)
            else:
                page.route("**/*", handle_request)

            if proxy_config:
                print(f"Proxying ALL external requests through: {proxy_config.get('server', 'N/A')}")

            # Set page to continue even if some resources fail to load
            page.set_default_timeout(timeout)
            page.set_default_navigation_timeout(timeout)
            
            # Set up console log listener
            def handle_console_message(msg):
                console_logs.append({
                    "type": msg.type,
                    "text": msg.text,
                    "location": f"{msg.location.get('url', 'unknown')}:{msg.location.get('lineNumber', 0)}" if msg.location else "unknown"
                })
            
            page.on("console", handle_console_message)
            
            # Navigate and wait for page to be fully ready
            try:
                print(f"Navigating to {url}...")
                page.goto(url, timeout=timeout, wait_until='domcontentloaded')
                print(f"DOM content loaded, waiting for React render...")

                # Wait for page to be fully ready:
                # 1. document.readyState == "complete" (all subresources loaded)
                # 2. #root has children (React has mounted and rendered)
                # Poll every 1s, hard timeout at 30s
                try:
                    page.wait_for_function(
                        """() => {
                            if (document.readyState !== 'complete') return false;
                            const root = document.getElementById('root');
                            return root && root.children.length > 0;
                        }""",
                        timeout=30000,
                        polling=1000,
                    )
                    print(f"Page ready: readyState=complete, React rendered")
                except TimeoutError:
                    ready_state = page.evaluate('document.readyState')
                    root_children = page.evaluate(
                        'document.getElementById("root")?.children.length || 0'
                    )
                    print(f"Page wait timeout: readyState={ready_state}, "
                          f"root children={root_children}")

            except TimeoutError:
                print(f"Navigation timeout, using fallback strategy...")
                try:
                    page.wait_for_load_state('domcontentloaded', timeout=5000)
                    page.wait_for_timeout(3000)
                    print(f"Fallback rendering complete")
                except TimeoutError:
                    print(f"Final fallback - minimal wait...")
                    page.wait_for_timeout(2000)
            
            # Wait for animations to initialize before taking screenshot.
            # Some pages (Three.js, p5.js) start blank and need a few seconds
            # before the first rendered frame appears.
            animation_wait_ms = config.get("evaluators", {}).get(
                "aesthetics_functional", {}
            ).get("screenshot_animation_wait_ms", 0)
            if animation_wait_ms > 0:
                print(f"Waiting {animation_wait_ms}ms for animations to initialize...")
                page.wait_for_timeout(animation_wait_ms)

            # Take screenshot with animations allowed so the current rendered
            # frame (after the wait above) is captured rather than frame 0.
            try:
                page.screenshot(
                    path=output_path,
                    full_page=True,
                    timeout=timeout,
                    animations='allow'
                )
            except TimeoutError:
                # If screenshot times out (often due to font loading), try with a simpler approach
                print(f"Screenshot timeout, trying without full_page...")
                page.screenshot(
                    path=output_path,
                    full_page=False,  # Just capture viewport
                    timeout=30000,  # Increased from 10s to 30s for slow networks
                    animations='allow'
                )
            browser.close()
            print(f"Screenshot saved to {output_path}")
            print(f"Collected {len(console_logs)} console messages")

            # Clamp screenshot dimensions: if either side exceeds max_dimension,
            # crop the image (discard the overflow) and overwrite the file.
            max_dimension = config.get("evaluators", {}).get(
                "aesthetics_functional", {}
            ).get("screenshot_max_dimension", 8000)
            try:
                from PIL import Image
                with Image.open(output_path) as _img:
                    orig_w, orig_h = _img.size
                    crop_w = min(orig_w, max_dimension)
                    crop_h = min(orig_h, max_dimension)
                    if crop_w < orig_w or crop_h < orig_h:
                        cropped = _img.crop((0, 0, crop_w, crop_h))
                        cropped.save(output_path)
                        msg = (f"Screenshot cropped from {orig_w}x{orig_h} to "
                               f"{crop_w}x{crop_h} (max_dimension={max_dimension})")
                        print(f"WARNING: {msg}")
                        console_logs.append({
                            "type": "warning",
                            "text": f"Screenshot size clamped: {msg}",
                            "location": "screenshot_clamp"
                        })
                    else:
                        print(f"Screenshot size OK: {orig_w}x{orig_h}")
            except Exception as _clamp_err:
                print(f"Warning: Could not clamp screenshot size: {_clamp_err}")

            # Analyze screenshot content to detect blank pages
            screenshot_path_obj = Path(output_path)
            is_blank = is_screenshot_blank_or_minimal(screenshot_path_obj)
            
            if is_blank:
                print(f"WARNING: Screenshot appears to be blank or minimal content")
                # Add this information to console logs for context
                console_logs.append({
                    "type": "warning",
                    "text": "Screenshot analysis: Page appears to render blank or with minimal content",
                    "location": "screenshot_analysis"
                })
            
            # Save console logs to file if requested
            if save_logs:
                logs_file = Path(output_path).parent / "console_logs.json"
                log_data = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "url": url,
                    "total_messages": len(console_logs),
                    "screenshot_blank_detected": is_blank,
                    "logs": console_logs
                }
                with open(logs_file, 'w', encoding='utf-8') as f:
                    json.dump(log_data, f, indent=2, ensure_ascii=False)
                print(f"Console logs saved to {logs_file} ({len(console_logs)} messages)")
            
            return True, None, console_logs
    except (TimeoutError, Error) as e:
        error_msg = f"Error taking screenshot: {e}"
        print(error_msg)
        return False, error_msg, console_logs

def take_screenshot(url: str, output_path: str, timeout: int = 15000) -> Tuple[bool, Optional[str]]:
    """
    Takes a screenshot of a given URL using Playwright.
    Legacy function for backward compatibility.

    Args:
        url: The URL to screenshot.
        output_path: The file path to save the screenshot to.
        timeout: The timeout in milliseconds.

    Returns:
        A tuple (success, error_message).
    """
    success, error_msg, _ = take_screenshot_with_logs(url, output_path, timeout)
    return success, error_msg
