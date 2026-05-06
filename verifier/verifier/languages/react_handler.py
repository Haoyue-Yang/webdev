# verifier/languages/react_handler.py

import subprocess
import time
import threading
import queue
import datetime
import json
import re
from pathlib import Path
from typing import Dict, Any, List

from .base_handler import BaseLanguageHandler
from ..utils.port_utils import find_available_port
from ..utils.process_utils import (
    run_command, 
    kill_process_group, 
    save_project_process, 
    cleanup_project_processes,
    clear_node_cache,
    monitor_resource_usage,
    sanitize_vite_error_output
)

class ReactHandler(BaseLanguageHandler):
    """
    Language handler for React projects.
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

    def _wait_for_server_ready(self, timeout: int) -> Dict[str, Any]:
        """
        Wait for Vite server to be ready by monitoring output for ready pattern.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            Dict with success status and details
        """
        print(f"Waiting for Vite server to be ready (timeout: {timeout}s)...")
        
        # Pattern to match Vite ready message: "VITE v4.5.14  ready in 4333 ms"
        vite_ready_pattern = re.compile(r'VITE.*ready in \d+\s*ms', re.IGNORECASE)
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Check if process terminated
            if self.dev_server_process.poll() is not None:
                # Process terminated immediately, something went wrong
                # Collect any output that was captured
                time.sleep(1)  # Give threads a moment to finish reading
                sanitized_output = sanitize_vite_error_output(self.dev_server_output, self.project_dir)
                error_details = {
                    "success": False,
                    "reason": "Server process terminated unexpectedly after start.",
                    "captured_output": sanitized_output
                }
                return error_details
            
            # Check for ready message in output queue
            try:
                line = self.output_queue.get(timeout=0.5)
                print(f"Dev server: {line}")
                
                # Check if this line indicates server is ready
                if vite_ready_pattern.search(line):
                    elapsed = time.time() - start_time
                    print(f"Vite server ready after {elapsed:.2f}s: {line}")
                    return {"success": True, "ready_time": elapsed, "ready_message": line}
                    
            except queue.Empty:
                # No new output, continue waiting
                continue
        
        # Timeout reached
        elapsed = time.time() - start_time
        print(f"Timeout waiting for Vite server to be ready after {elapsed:.2f}s")
        sanitized_output = sanitize_vite_error_output(self.dev_server_output, self.project_dir)
        return {
            "success": False,
            "reason": f"Server startup timeout after {elapsed:.2f}s. Server may still be starting.",
            "captured_output": sanitized_output,
            "timeout": timeout
        }

    def _filter_dev_server_output(self, output_lines: List[str]) -> List[str]:
        """
        Filter out repetitive and non-informative dev server output lines.
        
        Args:
            output_lines: Raw output lines from dev server
            
        Returns:
            Filtered output lines with noise removed, timestamps stripped, and duplicates removed while preserving order
        """
        processed_lines = []
        
        # Step 1: Remove timestamps (content before [vite]) and skip empty lines
        timestamp_pattern = re.compile(r"^\d{1,2}:\d{2}:\d{2}\s+[AP]M\s+")
        
        for line in output_lines:
            # Skip empty lines
            if not line.strip():
                continue
            
            # Remove timestamp prefix if present
            cleaned_line = timestamp_pattern.sub("", line).strip()
            
            # Only keep non-empty lines after timestamp removal
            if cleaned_line:
                processed_lines.append(cleaned_line)
        
        # Step 2: Remove duplicates while preserving order
        unique_lines = list(dict.fromkeys(processed_lines))
        
        # Step 3: Apply noise filtering patterns
        noise_patterns = [
            # Vite server announcements
            r"➜\s+Local:\s+http://localhost:\d+/",
            r"➜\s+Network:\s+http://[\d\.]+:\d+/", 
            r"➜\s+press h.*to show help",
            r"➜\s+press h.*enter.*help",
            # Note: removed "VITE.*ready in \d+\s*ms" pattern as it's now used as ready indicator
            r"ready in \d+\s*ms",
            r"Local:\s+http://localhost:\d+",
            r"Network:\s+http://[\d\.]+:\d+",
            
            # Command execution lines
            r">\s+vite\s+.*--host.*--port.*--no-open",
            r">\s+.*@\d+\.\d+\.\d+\s+dev\s+/.*",  # npm/pnpm script execution
            r">\s+[^@]+@[^@]+\s+dev\s+.*",         # alternative package@version dev format
            
            # Generic npm/pnpm command patterns
            r">\s+vite\s+\".*\"",
            r">\s+.*\s+dev\s+.*workspace.*",
        ]
        
        compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in noise_patterns]
        
        filtered_lines = []
        for line in unique_lines:
            # Check if line matches any noise pattern
            is_noise = any(pattern.search(line) for pattern in compiled_patterns)
            
            if not is_noise:
                filtered_lines.append(line)
        
        return filtered_lines

    def get_dev_server_output_after_ready(self, wait_seconds: int = 3, save_logs: bool = True, ready_indicators: List[str] = None) -> List[str]:
        """
        Collects dev server output after the server is ready, particularly looking for
        output that appears after "press h to show help" or similar ready messages.
        
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
            ready_indicators = ["➜  press h + enter to show help", "ready in", "Local:", "ready at"]
        
        print(f"Collecting dev server output for {wait_seconds} seconds...")
        
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
                print(f"Dev server: {line}")
            except queue.Empty:
                # No new output, continue waiting
                continue
        
        # Filter out noise from the collected output
        filtered_output_after_ready = self._filter_dev_server_output(output_after_ready)
        filtered_new_output = self._filter_dev_server_output(new_output_lines)
        
        # Sanitize output to prevent cross-project path contamination
        sanitized_output_after_ready = sanitize_vite_error_output(filtered_output_after_ready, self.project_dir)
        sanitized_new_output = sanitize_vite_error_output(filtered_new_output, self.project_dir)
        sanitized_all_output = sanitize_vite_error_output(self.dev_server_output, self.project_dir)
        
        # Save dev server output to file if requested
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
                "output_lines_after_ready": sanitized_output_after_ready,  # Sanitized filtered lines after ready marker
                "new_output_during_wait": sanitized_new_output,      # Sanitized filtered new lines during wait period
                "raw_output_lines_after_ready": output_after_ready,  # Original unfiltered for debugging (local paths only)
                "all_output_since_start": sanitized_all_output,  # All output since server start (sanitized)
                "sanitization_applied": True  # Flag to indicate sanitization was applied
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            
            # Update the print statement to show filtering and sanitization results
            raw_count = len(output_after_ready)
            filtered_count = len(filtered_output_after_ready)
            sanitized_count = len(sanitized_output_after_ready)
            print(f"Dev server output saved to {output_file} (ready_index: {ready_index}, raw: {raw_count}, filtered: {filtered_count}, sanitized: {sanitized_count}, new: {len(sanitized_new_output)}, total: {len(sanitized_all_output)})")
                
        return sanitized_output_after_ready  # Return sanitized filtered lines after ready marker

    def install(self) -> Dict[str, Any]:
        """
        Installs dependencies and returns a result dictionary.
        """
        print(f"Installing dependencies for {self.project_dir}...")
        install_command = self.config['install_script']
        return_code, stdout, stderr = run_command(install_command, cwd=str(self.project_dir))
        
        if return_code != 0:
            error_details = {
                "success": False,
                "reason": "Installation command failed.",
                "exit_code": return_code,
                "stdout": stdout,
                "stderr": stderr
            }
            return error_details
        
        print("Installation successful.")
        return {"success": True}

    def start(self) -> Dict[str, Any]:
        """
        Starts the dev server and returns a result dictionary.
        """
        try:
            min_port = self.global_config['ports']['min']
            max_port = self.global_config['ports']['max']
            self.port = find_available_port(min_port, max_port)
        except IOError as e:
            print(f"Error finding available port: {e}")
            return {"success": False, "reason": "Error finding available port"}

        start_command = self.config['start_script'].format(port=self.port)
        print(f"Starting dev server with command: '{start_command}'...")

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
            start_new_session=True, # Important for proper cleanup
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

        # Wait for Vite server to be ready by monitoring output
        startup_timeout = self.global_config.get('server', {}).get('startup_timeout', 30)
        ready_result = self._wait_for_server_ready(startup_timeout)
        
        if not ready_result['success']:
            # If server failed to start, clean up the process tracking
            print("Server failed to start, cleaning up process tracking...")
            cleanup_project_processes(self.project_dir)
            return ready_result
            
        self.server_url = f"http://localhost:{self.port}"
        print(f"Dev server started successfully at {self.server_url}")
        return {"success": True}

    def stop(self) -> bool:
        """
        Stops the dev server process group with comprehensive cleanup.
        """
        success = True
        
        # First try the basic process group kill if we have a process
        if self.dev_server_process:
            print(f"Stopping dev server (PID: {self.dev_server_process.pid})...")
            kill_process_group(self.dev_server_process.pid)
            
            # Ensure it's terminated
            try:
                self.dev_server_process.wait(timeout=5)
                print("Dev server process terminated gracefully.")
            except subprocess.TimeoutExpired:
                print(f"Warning: Server process {self.dev_server_process.pid} did not terminate gracefully.")
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

        # Clear Node.js/Vite cache to prevent contamination
        print("Clearing Node.js/Vite cache...")
        cache_result = clear_node_cache(self.project_dir)
        
        if not cache_result.get("success", False):
            print(f"Warning: Cache cleanup had issues: {cache_result}")
        else:
            cleared_count = len(cache_result.get("cleared_items", []))
            if cleared_count > 0:
                print(f"Cleared {cleared_count} cache items: {cache_result['cleared_items']}")

        self.dev_server_process = None
        print("Server stopped with comprehensive cleanup.")
        return success 