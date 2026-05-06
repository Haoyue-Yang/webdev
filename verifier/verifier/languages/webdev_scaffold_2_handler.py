# verifier/languages/webdev_scaffold_2_handler.py

import subprocess
import os
import time
from pathlib import Path
from typing import Dict, Any

from .base_handler import BaseLanguageHandler
from ..utils.port_utils import find_available_port
from ..utils.process_utils import (
    kill_process_group,
    save_project_process,
    cleanup_project_processes
)
from ..utils.tool_call_parser import parse_tool_calls
from ..utils.react_tool_executor import ReactToolExecutor


class WebdevScaffold2Handler(BaseLanguageHandler):
    """
    Language handler for webdev_scaffold_2 projects.

    This handler:
    1. Takes a 'response' field containing <tool_call>...</tool_call> blocks
    2. Parses and executes tool_calls to build a React/Vite/Tailwind project
    3. Serves the built project via HTTP server for evaluation
    """

    def __init__(self, project_dir: Path, config: Dict[str, Any], global_config: Dict[str, Any]):
        super().__init__(project_dir, config)
        self.global_config = global_config
        self.port = None
        self.server_url = None
        self.tool_executor = None

        # Get the response from config (passed from verifier)
        self.response = config.get('response', '')
        self.skip_install = config.get('skip_install', False)

        # Template path for React project (inside verifier package)
        self.template_path = str(Path(__file__).parent.parent / "templates" / "project_template.json")

    def install(self) -> Dict[str, Any]:
        """
        Install by parsing tool_calls from response and executing them.

        Returns:
            Dict with 'success' boolean and optional error details
        """
        print(f"[webdev_scaffold_2] Installing project at {self.project_dir}...")

        # For BOS tar projects: skip tool_call replay, but build if dist/ is missing
        if self.skip_install:
            dist_index = self.project_dir / "dist" / "index.html"
            if dist_index.exists():
                print(f"[webdev_scaffold_2] Skipping install (pre-built tar project, dist/index.html exists)")
                return {"success": True}

            # dist/ not found — run npm install + build on the extracted source files
            print(f"[webdev_scaffold_2] dist/index.html not found in tar, running npm install + build...")
            self.tool_executor = ReactToolExecutor(
                project_dir=str(self.project_dir),
                template_path=None,  # Don't load template — tar already has all source files
                use_virtual_fs=False,
            )
            build_result = self.tool_executor.execute('build_project', {})
            if build_result.get('status') == 'error':
                return {
                    "success": False,
                    "reason": f"Build failed: {build_result.get('message')}",
                    "stdout": build_result.get('stdout', ''),
                    "stderr": build_result.get('stderr', ''),
                }
            dist_index = self.project_dir / "dist" / "index.html"
            if not dist_index.exists():
                return {
                    "success": False,
                    "reason": "Build completed but dist/index.html not found",
                }
            print(f"[webdev_scaffold_2] Build successful, dist/index.html exists")
            return {"success": True}

        try:
            # Initialize ReactToolExecutor with template
            print(f"[webdev_scaffold_2] Initializing ReactToolExecutor with template: {self.template_path}")
            self.tool_executor = ReactToolExecutor(
                project_dir=str(self.project_dir),
                template_path=self.template_path,
                use_virtual_fs=False  # Write directly to disk
            )

            # Parse tool_calls from response
            tool_calls = parse_tool_calls(self.response)
            print(f"[webdev_scaffold_2] Parsed {len(tool_calls)} tool calls from response")

            if not tool_calls:
                return {
                    "success": False,
                    "reason": "No tool calls found in response"
                }

            # Separate build_project from other tool calls
            build_calls = [tc for tc in tool_calls if tc['name'] == 'build_project']
            other_calls = [tc for tc in tool_calls if tc['name'] != 'build_project']

            # Execute all non-build tool calls
            for i, tool_call in enumerate(other_calls):
                tool_name = tool_call['name']
                tool_args = tool_call['arguments']

                print(f"[webdev_scaffold_2] Executing tool {i+1}/{len(other_calls)}: {tool_name}")

                result = self.tool_executor.execute(tool_name, tool_args)

                if result.get('status') == 'error':
                    print(f"[webdev_scaffold_2] Tool {tool_name} failed: {result.get('message')}")
                    # Continue with other tools, don't fail immediately
                else:
                    print(f"[webdev_scaffold_2] Tool {tool_name} succeeded")

            # Execute build_project at the end
            print(f"[webdev_scaffold_2] Building project...")
            build_result = self.tool_executor.execute('build_project', {})

            if build_result.get('status') == 'error':
                return {
                    "success": False,
                    "reason": f"Build failed: {build_result.get('message')}",
                    "stdout": build_result.get('stdout', ''),
                    "stderr": build_result.get('stderr', '')
                }

            # Verify dist/index.html exists
            dist_index = self.project_dir / "dist" / "index.html"
            if not dist_index.exists():
                return {
                    "success": False,
                    "reason": "Build completed but dist/index.html not found"
                }

            print(f"[webdev_scaffold_2] Build successful, dist/index.html exists")
            return {"success": True}

        except Exception as e:
            import traceback
            return {
                "success": False,
                "reason": f"Installation error: {str(e)}",
                "traceback": traceback.format_exc()
            }

    def start(self) -> Dict[str, Any]:
        """
        Start HTTP server serving the dist/ directory.

        Returns:
            Dict with 'success' boolean and optional error details
        """
        try:
            # Find available port
            min_port = self.global_config['ports']['min']
            max_port = self.global_config['ports']['max']
            self.port = find_available_port(min_port, max_port)
        except IOError as e:
            print(f"[webdev_scaffold_2] Error finding available port: {e}")
            return {"success": False, "reason": "Error finding available port"}

        # Verify dist directory exists
        dist_dir = self.project_dir / "dist"
        if not dist_dir.exists():
            return {
                "success": False,
                "reason": "dist directory not found. Build may have failed."
            }

        # Start HTTP server serving dist/
        start_command = f"python3 -m http.server {self.port}"
        print(f"[webdev_scaffold_2] Starting HTTP server with command: '{start_command}'...")

        try:
            self.dev_server_process = subprocess.Popen(
                start_command,
                shell=True,
                cwd=str(dist_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )

            # Save process information for tracking
            save_project_process(
                self.project_dir,
                self.dev_server_process.pid,
                command=start_command,
                port=self.port
            )

            # Wait a moment for server to start
            time.sleep(1)

            # Check if process is still running
            if self.dev_server_process.poll() is not None:
                stdout, stderr = self.dev_server_process.communicate()
                return {
                    "success": False,
                    "reason": "HTTP server failed to start",
                    "stdout": stdout.decode('utf-8', errors='ignore'),
                    "stderr": stderr.decode('utf-8', errors='ignore')
                }

            self.server_url = f"http://localhost:{self.port}"
            print(f"[webdev_scaffold_2] HTTP server started at {self.server_url}")
            return {"success": True}

        except Exception as e:
            return {
                "success": False,
                "reason": f"Failed to start HTTP server: {str(e)}"
            }

    def stop(self) -> bool:
        """
        Stop the HTTP server process.

        Returns:
            True if stopped successfully, False otherwise
        """
        success = True

        if self.dev_server_process:
            print(f"[webdev_scaffold_2] Stopping HTTP server (PID: {self.dev_server_process.pid})...")
            kill_process_group(self.dev_server_process.pid)

            try:
                self.dev_server_process.wait(timeout=5)
                print("[webdev_scaffold_2] HTTP server process terminated gracefully.")
            except subprocess.TimeoutExpired:
                print(f"[webdev_scaffold_2] Warning: Server process {self.dev_server_process.pid} did not terminate gracefully.")
                success = False

        # Clean up any remaining processes
        print("[webdev_scaffold_2] Running comprehensive process cleanup...")
        cleanup_result = cleanup_project_processes(self.project_dir)

        if not cleanup_result.get("success", False):
            print(f"[webdev_scaffold_2] Warning: Process cleanup had issues: {cleanup_result}")
            success = False

        self.dev_server_process = None
        print("[webdev_scaffold_2] Server stopped.")
        return success
