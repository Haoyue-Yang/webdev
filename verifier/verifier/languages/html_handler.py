# verifier/languages/html_handler.py

import subprocess
import time
import threading
import queue
import datetime
import json
import http.server
import socketserver
import os
from pathlib import Path
from typing import Dict, Any, List

from .base_handler import BaseLanguageHandler
from ..utils.port_utils import find_available_port, is_port_in_use, kill_process_on_port
from ..utils.process_utils import (
    kill_process_group,
    save_project_process,
    cleanup_project_processes,
    clear_project_processes,
)

class HTMLHandler(BaseLanguageHandler):
    """
    Language handler for HTML projects.
    """

    def __init__(self, project_dir: Path, config: Dict[str, Any], global_config: Dict[str, Any]):
        super().__init__(project_dir, config)
        self.global_config = global_config
        self.port = None
        self.server_url = None
        self.dev_server_output = []
        self.output_queue = queue.Queue()

    def _read_output(self, pipe, output_list):
        """
        Continuously read output from a pipe and store it.
        """
        try:
            for line in iter(pipe.readline, b''):
                line_str = line.decode('utf-8', errors='ignore').strip()
                if line_str:
                    output_list.append(line_str)
                    self.output_queue.put(line_str)
        except Exception as e:
            print(f"Error reading output: {e}")

    def _filter_dev_server_output(self, output_lines: List[str]) -> List[str]:
        """
        Filter out repetitive and non-informative HTTP server output lines.
        
        Args:
            output_lines: Raw output lines from HTTP server
            
        Returns:
            Filtered output lines with noise removed
        """
        filtered_lines = []
        
        # Patterns to filter out (case-insensitive)
        noise_patterns = [
            # HTTP server standard messages
            r"Serving HTTP on .* port \d+",
            r"HTTP server started at http://.*:\d+",
            r"Server started successfully",
            r".*::ffff:\d+\.\d+\.\d+\.\d+ - - \[.*\]",  # Access logs
            r"\d+\.\d+\.\d+\.\d+ - - \[.*\]",  # More access logs
        ]
        
        import re
        compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in noise_patterns]
        
        for line in output_lines:
            # Skip empty lines
            if not line.strip():
                continue
                
            # Check if line matches any noise pattern
            is_noise = any(pattern.search(line) for pattern in compiled_patterns)
            
            if not is_noise:
                filtered_lines.append(line)
        
        return filtered_lines

    def get_dev_server_output_after_ready(self, wait_seconds: int = 3, save_logs: bool = True, ready_indicators: List[str] = None) -> List[str]:
        """
        Collects HTTP server output after the server is ready.
        
        Args:
            wait_seconds: How long to wait and collect output after server is ready
            save_logs: Whether to save the collected output to a file
            ready_indicators: List of strings that indicate server is ready
            
        Returns:
            List of output lines collected after the server became ready
        """
        if not hasattr(self, 'dev_server_process') or not self.dev_server_process:
            return []
        
        # Default ready indicators if not provided
        if ready_indicators is None:
            ready_indicators = ["Serving HTTP", "HTTP server started", "Server started successfully"]
        
        print(f"Collecting HTTP server output for {wait_seconds} seconds...")
        
        # Find the ready marker in existing output
        ready_index = -1
        for i, line in enumerate(self.dev_server_output):
            if any(indicator.lower() in line.lower() for indicator in ready_indicators):
                ready_index = i
                break
        
        # Get output after ready marker (if found)
        output_after_ready = []
        if ready_index >= 0:
            output_after_ready = self.dev_server_output[ready_index + 1:]
        
        # Wait and collect new output that appears after initial startup
        new_output_lines = []
        start_time = time.time()
        
        while time.time() - start_time < wait_seconds:
            try:
                # Get output with a short timeout
                line = self.output_queue.get(timeout=0.1)
                new_output_lines.append(line)
                output_after_ready.append(line)  # Also add to the after-ready collection
                print(f"HTTP server: {line}")
            except queue.Empty:
                # No new output, continue waiting
                continue
        
        # Filter out noise from the collected output
        filtered_output_after_ready = self._filter_dev_server_output(output_after_ready)
        filtered_new_output = self._filter_dev_server_output(new_output_lines)
        
        # Save HTTP server output to file if requested
        if save_logs:
            verifier_dir = self.project_dir / ".verifier"
            verifier_dir.mkdir(exist_ok=True)
            output_file = verifier_dir / "dev_server_output.json"
            
            log_data = {
                "timestamp": datetime.datetime.now().isoformat(),
                "project_dir": str(self.project_dir),
                "server_url": self.server_url,
                "collection_duration_seconds": wait_seconds,
                "ready_indicators_used": ready_indicators,
                "ready_marker_found_at_line": ready_index if ready_index >= 0 else None,
                "output_lines_after_ready": filtered_output_after_ready,  # Filtered lines after ready marker
                "new_output_during_wait": filtered_new_output,      # Filtered new lines during wait period
                "raw_output_lines_after_ready": output_after_ready,  # Unfiltered for debugging
                "all_output_since_start": self.dev_server_output  # All output since server start
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            
            # Update the print statement to show filtering results
            raw_count = len(output_after_ready)
            filtered_count = len(filtered_output_after_ready)
            print(f"HTTP server output saved to {output_file} (ready_index: {ready_index}, raw: {raw_count}, filtered: {filtered_count}, new: {len(filtered_new_output)}, total: {len(self.dev_server_output)})")
                
        return filtered_output_after_ready  # Return filtered lines after ready marker

    def install(self) -> Dict[str, Any]:
        """
        For HTML projects, no installation is needed.
        """
        print(f"HTML project detected at {self.project_dir}. No installation required.")
        return {"success": True}

    def _try_start_server(self, port: int) -> Dict[str, Any]:
        """
        Try to start the HTTP server on a specific port.

        Args:
            port: Port number to start the server on

        Returns:
            Dict with success status and details
        """
        self.port = port

        # Use Python's built-in HTTP server to serve the HTML file
        start_command = f"python3 -m http.server {self.port}"
        print(f"Starting HTTP server with command: '{start_command}'...")

        # Reset output collection
        self.dev_server_output = []
        self.output_queue = queue.Queue()

        # Start the server as a background process
        self.dev_server_process = subprocess.Popen(
            start_command,
            shell=True,
            cwd=str(self.project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # Important for proper cleanup
            bufsize=1,
            universal_newlines=False
        )

        # Start threads to continuously read stdout and stderr
        stdout_thread = threading.Thread(
            target=self._read_output,
            args=(self.dev_server_process.stdout, self.dev_server_output),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=self._read_output,
            args=(self.dev_server_process.stderr, self.dev_server_output),
            daemon=True
        )

        stdout_thread.start()
        stderr_thread.start()

        # Save process information for tracking
        save_project_process(
            self.project_dir,
            self.dev_server_process.pid,
            command=start_command,
            port=self.port
        )

        # Wait a moment for the server to start and check if it's running
        time.sleep(2)

        if self.dev_server_process.poll() is not None:
            # Process terminated immediately, something went wrong
            # Collect any output that was captured
            time.sleep(1)  # Give threads a moment to finish reading

            # Check if it's an "Address already in use" error
            output_str = ' '.join(self.dev_server_output)
            if 'Address already in use' in output_str or 'OSError: [Errno 98]' in output_str:
                return {
                    "success": False,
                    "reason": "port_in_use",
                    "port": port,
                    "captured_output": self.dev_server_output
                }

            error_details = {
                "success": False,
                "reason": "HTTP server process terminated unexpectedly after start.",
                "captured_output": self.dev_server_output
            }
            return error_details

        self.server_url = f"http://localhost:{self.port}"
        print(f"HTTP server started successfully at {self.server_url}")
        return {"success": True}

    def start(self) -> Dict[str, Any]:
        """
        Starts an HTTP server to serve the HTML file and returns a result dictionary.
        Includes retry logic for port binding failures.
        """
        # Check if index.html exists
        index_file = self.project_dir / "index.html"
        if not index_file.exists():
            return {"success": False, "reason": "index.html not found in project directory"}

        min_port = self.global_config['ports']['min']
        max_port = self.global_config['ports']['max']
        max_port_retries = 5

        for attempt in range(max_port_retries):
            try:
                port = find_available_port(min_port, max_port)
            except IOError as e:
                print(f"Error finding available port: {e}")
                return {"success": False, "reason": "Error finding available port"}

            result = self._try_start_server(port)

            if result["success"]:
                return result

            # If port was in use, try another port
            if result.get("reason") == "port_in_use":
                print(f"Port {port} is in use (attempt {attempt + 1}/{max_port_retries}), trying another port...")
                # Clean up the failed process tracking
                clear_project_processes(self.project_dir)
                self.dev_server_process = None
                continue

            # For other errors, return immediately
            return result

        # All retries exhausted
        return {
            "success": False,
            "reason": f"Could not start HTTP server after {max_port_retries} port retries",
            "captured_output": self.dev_server_output
        }

    def stop(self) -> bool:
        """
        Stops the HTTP server process group with comprehensive cleanup.
        """
        success = True

        # First try the basic process group kill if we have a process
        if self.dev_server_process:
            print(f"Stopping HTTP server (PID: {self.dev_server_process.pid})...")
            kill_process_group(self.dev_server_process.pid)

            # Ensure it's terminated
            try:
                self.dev_server_process.wait(timeout=5)
                print("HTTP server process terminated gracefully.")
            except subprocess.TimeoutExpired:
                print(f"Warning: HTTP server process {self.dev_server_process.pid} did not terminate gracefully.")
                success = False

        # Use comprehensive project-wide cleanup for any remaining processes
        print("Running comprehensive process cleanup...")
        cleanup_result = cleanup_project_processes(self.project_dir)

        if not cleanup_result.get("success", False):
            print(f"Warning: Process cleanup had issues: {cleanup_result}")
            success = False
        else:
            killed_count = len(cleanup_result.get("killed_processes", []))
            if killed_count > 0:
                print(f"Cleaned up {killed_count} additional processes")

        self.dev_server_process = None
        print("HTTP server stopped with comprehensive cleanup.")
        return success 