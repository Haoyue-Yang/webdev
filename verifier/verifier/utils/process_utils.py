# verifier/utils/process_utils.py

import subprocess
import os
import signal
import time
import json
import psutil
from typing import Tuple, List, Dict, Any, Set
from pathlib import Path
import uuid

def save_project_process(project_dir: Path, pid: int, command: str = None, port: int = None):
    """
    Save a process PID to the project's .verifier directory for safe tracking.
    
    Args:
        project_dir: Project directory path
        pid: Process ID to save
        command: Optional command that started the process
        port: Optional port number the process is using
    """
    try:
        verifier_dir = project_dir / ".verifier"
        verifier_dir.mkdir(exist_ok=True)
        
        processes_file = verifier_dir / "processes.json"
        
        # Load existing processes
        processes = []
        if processes_file.exists():
            try:
                with open(processes_file, 'r') as f:
                    processes = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load existing processes file: {e}")
                processes = []
        
        # Add new process
        process_info = {
            "pid": pid,
            "command": command,
            "port": port,
            "start_time": time.time(),
            "project_dir": str(project_dir.resolve())
        }
        processes.append(process_info)
        
        # Save updated processes
        with open(processes_file, 'w') as f:
            json.dump(processes, f, indent=2)
        
        print(f"Saved PID {pid} to {processes_file}")
        
    except Exception as e:
        print(f"Warning: Could not save process {pid}: {e}")

def load_project_processes(project_dir: Path) -> List[Dict[str, Any]]:
    """
    Load all tracked process PIDs for a project from .verifier directory.
    
    Args:
        project_dir: Project directory path
        
    Returns:
        List of process information dictionaries (only active processes)
    """
    try:
        verifier_dir = project_dir / ".verifier"
        processes_file = verifier_dir / "processes.json"
        
        if not processes_file.exists():
            return []
        
        with open(processes_file, 'r') as f:
            processes = json.load(f)
        
        # Filter out processes that are no longer running
        active_processes = []
        for proc_info in processes:
            try:
                pid = proc_info["pid"]
                psutil.Process(pid)  # This will raise NoSuchProcess if not running
                active_processes.append(proc_info)
            except psutil.NoSuchProcess:
                print(f"Process {proc_info['pid']} no longer exists, removing from tracking")
        
        # Save the filtered list back if it changed
        if len(active_processes) != len(processes):
            with open(processes_file, 'w') as f:
                json.dump(active_processes, f, indent=2)
        
        return active_processes
        
    except Exception as e:
        print(f"Warning: Could not load project processes: {e}")
        return []

def remove_project_process(project_dir: Path, pid: int):
    """
    Remove a process PID from the project's tracking file.
    
    Args:
        project_dir: Project directory path
        pid: Process ID to remove
    """
    try:
        verifier_dir = project_dir / ".verifier"
        processes_file = verifier_dir / "processes.json"
        
        if not processes_file.exists():
            return
        
        with open(processes_file, 'r') as f:
            processes = json.load(f)
        
        # Remove the process
        original_count = len(processes)
        processes = [p for p in processes if p["pid"] != pid]
        
        if len(processes) != original_count:
            # Save updated list
            with open(processes_file, 'w') as f:
                json.dump(processes, f, indent=2)
            print(f"Removed PID {pid} from tracking")
        
    except Exception as e:
        print(f"Warning: Could not remove process {pid}: {e}")

def clear_project_processes(project_dir: Path):
    """
    Clear all process tracking for a project.
    
    Args:
        project_dir: Project directory path
    """
    try:
        verifier_dir = project_dir / ".verifier"
        processes_file = verifier_dir / "processes.json"
        
        if processes_file.exists():
            processes_file.unlink()
            print(f"Cleared process tracking for {project_dir}")
            
    except Exception as e:
        print(f"Warning: Could not clear process tracking: {e}")

def run_command(
    command: str,
    cwd: str,
    timeout: int = 300
) -> Tuple[int, str, str]:
    """
    Runs a shell command in a specified directory and returns its output.

    Args:
        command: The command to execute.
        cwd: The working directory to run the command in.
        timeout: The timeout in seconds.

    Returns:
        A tuple containing (return_code, stdout, stderr).
    """
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True  # Creates a new process group
        )
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        # Kill the entire process group
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        return -1, "", "Command timed out."
    except Exception as e:
        return -1, "", str(e)

def kill_process_group(pid: int):
    """
    Kills an entire process group given the parent PID.
    """
    try:
        # The negative PID kills the entire process group
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        # Process already gone
        pass
    except Exception as e:
        # Log this error
        print(f"Error killing process group for PID {pid}: {e}")

def run_command_with_project_tracking(
    command: str,
    cwd: str,
    project_id: str,
    timeout: int = 300,
    additional_env: Dict[str, str] = None
) -> Tuple[int, str, str, int]:
    """
    Runs a shell command with project-specific process tracking.

    Args:
        command: The command to execute.
        cwd: The working directory to run the command in.
        project_id: Unique project identifier for process tracking
        timeout: The timeout in seconds.
        additional_env: Additional environment variables

    Returns:
        A tuple containing (return_code, stdout, stderr, pid).
    """
    # Create unique environment marker for this project
    env_marker = f"VERIFIER_PROJECT_{uuid.uuid4().hex[:8]}"
    
    # Prepare environment
    env = os.environ.copy()
    env["VERIFIER_PROJECT_ID"] = project_id
    env["VERIFIER_PROJECT_MARKER"] = env_marker
    env["VERIFIER_PROJECT_CWD"] = str(cwd)
    
    if additional_env:
        env.update(additional_env)
    
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # Creates a new process group
            env=env
        )
        
        stdout, stderr = process.communicate(timeout=timeout)
        
        return process.returncode, stdout, stderr, process.pid
        
    except subprocess.TimeoutExpired:
        # Kill the entire process group
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            # Wait a bit and then force kill if still alive
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        return -1, "", "Command timed out.", process.pid if 'process' in locals() else -1
        
    except Exception as e:
        return -1, "", str(e), -1

def force_kill_process_tree(pid: int, timeout: int = 5) -> Dict[str, Any]:
    """
    Forcefully kills a process and all its children with escalating signals.
    
    Args:
        pid: Parent process ID
        timeout: Maximum time to wait between signal attempts
        
    Returns:
        Dictionary with cleanup results
    """
    result = {
        "pid": pid,
        "killed_processes": [],
        "failed_processes": [],
        "method_used": None,
        "success": False
    }
    
    try:
        parent = psutil.Process(pid)
        # Get all children processes recursively
        children = parent.children(recursive=True)
        processes_to_kill = [parent] + children
        
        print(f"Found {len(processes_to_kill)} processes to terminate (parent PID: {pid})")
        
        # Step 1: Try graceful termination with SIGTERM
        for proc in processes_to_kill:
            try:
                proc.terminate()
                result["killed_processes"].append({"pid": proc.pid, "name": proc.name(), "signal": "SIGTERM"})
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                result["failed_processes"].append({"pid": proc.pid, "error": str(e)})
        
        # Wait for graceful termination
        gone, alive = psutil.wait_procs(processes_to_kill, timeout=timeout)
        
        if alive:
            print(f"{len(alive)} processes still alive after SIGTERM, escalating to SIGKILL")
            # Step 2: Force kill remaining processes with SIGKILL
            for proc in alive:
                try:
                    proc.kill()
                    result["killed_processes"].append({"pid": proc.pid, "name": proc.name(), "signal": "SIGKILL"})
                except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                    result["failed_processes"].append({"pid": proc.pid, "error": str(e)})
            
            # Final wait
            gone, alive = psutil.wait_procs(alive, timeout=timeout)
            result["method_used"] = "SIGKILL"
        else:
            result["method_used"] = "SIGTERM"
        
        result["success"] = len(alive) == 0
        if alive:
            print(f"Warning: {len(alive)} processes could not be killed")
            for proc in alive:
                result["failed_processes"].append({"pid": proc.pid, "name": proc.name(), "error": "Could not kill"})
        
    except psutil.NoSuchProcess:
        result["success"] = True  # Process already gone
        print(f"Process {pid} already terminated")
    except Exception as e:
        print(f"Error in force_kill_process_tree for PID {pid}: {e}")
        result["failed_processes"].append({"pid": pid, "error": str(e)})
    
    return result

def cleanup_project_processes(project_dir: Path) -> Dict[str, Any]:
    """
    Safely cleanup processes belonging to a specific project using file-based tracking.
    
    Args:
        project_dir: Project directory path (required for file-based tracking)
        
    Returns:
        Dictionary with cleanup results
    """
    result = {
        "project_dir": str(project_dir),
        "tracked_processes": [],
        "killed_processes": [],
        "failed_processes": [],
        "cleanup_methods": []
    }
    
    if not project_dir or not project_dir.exists():
        result["error"] = "Project directory does not exist"
        result["success"] = False
        return result
        
    try:
        # Method 1: Clean up tracked processes from .verifier/processes.json
        tracked_processes = load_project_processes(project_dir)
        result["tracked_processes"] = [p["pid"] for p in tracked_processes]
        
        if tracked_processes:
            result["cleanup_methods"].append("tracked_processes")
            for proc_info in tracked_processes:
                pid = proc_info["pid"]
                try:
                    # Verify the process is still running
                    psutil.Process(pid)
                    
                    # Kill this process tree
                    cleanup_result = force_kill_process_tree(pid)
                    result["killed_processes"].extend([p["pid"] for p in cleanup_result["killed_processes"]])
                    result["failed_processes"].extend(cleanup_result["failed_processes"])
                    
                    # Remove from tracking
                    remove_project_process(project_dir, pid)
                    
                except psutil.NoSuchProcess:
                    # Process already gone, just remove from tracking
                    remove_project_process(project_dir, pid)
                    print(f"Process {pid} already terminated")
                except Exception as e:
                    result["failed_processes"].append({"pid": pid, "error": str(e)})
        
        # Method 2: Find processes by working directory (very strict matching)
        result["cleanup_methods"].append("directory_scan")
        project_dir_resolved = project_dir.resolve()
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cwd']):
            try:
                # Skip if already killed
                if proc.info['pid'] in result["killed_processes"]:
                    continue
                
                # Only look for Node.js/Vite processes
                name = proc.info.get('name', '').lower()
                if name not in ['node', 'npm', 'pnpm']:
                    continue
                
                # Verify it's our project using strict directory matching
                is_our_process = False
                verification_methods = []
                
                # Check current working directory (exact match only)
                try:
                    proc_cwd = Path(proc.info.get('cwd', ''))
                    if proc_cwd.exists():
                        proc_cwd_resolved = proc_cwd.resolve()
                        
                        # Exact match
                        if proc_cwd_resolved == project_dir_resolved:
                            is_our_process = True
                            verification_methods.append("exact_cwd")
                        
                        # Subdirectory match (process running in subdirectory of our project)
                        elif proc_cwd_resolved.is_relative_to(project_dir_resolved):
                            is_our_process = True
                            verification_methods.append("subdir_cwd")
                            
                except (OSError, psutil.AccessDenied, ValueError):
                    continue
                
                # Additional verification: Check command line for our project path
                cmdline = proc.info.get('cmdline', [])
                if cmdline:
                    cmdline_str = ' '.join(str(cmd) for cmd in cmdline)
                    if str(project_dir_resolved) in cmdline_str:
                        # Exact path match in command line
                        if not is_our_process:
                            is_our_process = True
                        verification_methods.append("cmdline_path")
                
                # Only kill if we have strong evidence it's our process
                # AND it's not in our tracked list (to avoid double-killing)
                if is_our_process and proc.info['pid'] not in result["tracked_processes"]:
                    print(f"Found untracked process {proc.info['pid']} ({proc.info['name']}) in project directory")
                    print(f"  Verification methods: {verification_methods}")
                    print(f"  CWD: {proc.info.get('cwd', 'N/A')}")
                    print(f"  Command: {' '.join(cmdline) if cmdline else 'N/A'}")
                    
                    # Kill this process tree
                    cleanup_result = force_kill_process_tree(proc.info['pid'])
                    result["killed_processes"].extend([p["pid"] for p in cleanup_result["killed_processes"]])
                    result["failed_processes"].extend(cleanup_result["failed_processes"])
                    
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Clean up the tracking file
        clear_project_processes(project_dir)
        
        result["success"] = len(result["failed_processes"]) == 0
        
    except Exception as e:
        result["error"] = str(e)
        result["success"] = False
    
    return result

def get_file_descriptor_usage() -> Dict[str, Any]:
    """
    Get current file descriptor usage statistics.
    
    Returns:
        Dictionary with file descriptor usage information
    """
    try:
        current_process = psutil.Process()
        num_fds = current_process.num_fds() if hasattr(current_process, 'num_fds') else 0
        
        # Try to get system limits
        try:
            import resource
            soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        except:
            soft_limit, hard_limit = None, None
        
        # Get open files info
        try:
            open_files = current_process.open_files()
            file_types = {}
            for f in open_files:
                ext = Path(f.path).suffix.lower() if f.path else 'unknown'
                file_types[ext] = file_types.get(ext, 0) + 1
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            open_files = []
            file_types = {}
        
        return {
            "current_fds": num_fds,
            "soft_limit": soft_limit,
            "hard_limit": hard_limit,
            "usage_percentage": (num_fds / soft_limit * 100) if soft_limit else None,
            "open_files_count": len(open_files),
            "file_types": file_types,
            "warning": num_fds > (soft_limit * 0.8) if soft_limit else False
        }
    except Exception as e:
        return {"error": str(e), "current_fds": 0}

def clear_node_cache(project_dir: Path) -> Dict[str, Any]:
    """
    Clear Node.js cache and temporary files for a project.
    
    Args:
        project_dir: Project directory path
        
    Returns:
        Dictionary with cleanup results
    """
    result = {
        "cleared_items": [],
        "failed_items": [],
        "success": True
    }
    
    try:
        # Clear node_modules/.cache if it exists
        cache_dirs = [
            project_dir / "node_modules" / ".cache",
            project_dir / "node_modules" / ".vite",
            project_dir / ".vite",
            project_dir / "dist",
        ]
        
        for cache_dir in cache_dirs:
            if cache_dir.exists():
                try:
                    import shutil
                    shutil.rmtree(cache_dir)
                    result["cleared_items"].append(str(cache_dir))
                except Exception as e:
                    result["failed_items"].append({"path": str(cache_dir), "error": str(e)})
                    result["success"] = False
        
        # Clear package-lock.json if it exists (will be regenerated)
        lock_files = [
            project_dir / "package-lock.json",
            project_dir / "pnpm-lock.yaml"
        ]
        
        for lock_file in lock_files:
            if lock_file.exists():
                try:
                    lock_file.unlink()
                    result["cleared_items"].append(str(lock_file))
                except Exception as e:
                    result["failed_items"].append({"path": str(lock_file), "error": str(e)})
        
    except Exception as e:
        result["error"] = str(e)
        result["success"] = False
    
    return result

def monitor_resource_usage(project_id: str, stage: str) -> Dict[str, Any]:
    """
    Monitor and log resource usage for debugging purposes.
    
    Args:
        project_id: Project identifier
        stage: Stage of verification (start, install, server_start, evaluation, cleanup)
        
    Returns:
        Dictionary with resource usage information
    """
    try:
        current_process = psutil.Process()
        
        # Get memory info
        memory_info = current_process.memory_info()
        
        # Get file descriptor info
        fd_info = get_file_descriptor_usage()
        
        # Get CPU usage
        cpu_percent = current_process.cpu_percent()
        
        resource_info = {
            "project_id": project_id,
            "stage": stage,
            "timestamp": time.time(),
            "memory": {
                "rss_mb": memory_info.rss / 1024 / 1024,
                "vms_mb": memory_info.vms / 1024 / 1024
            },
            "cpu_percent": cpu_percent,
            "file_descriptors": fd_info,
            "process_count": len(current_process.children(recursive=True))
        }
        
        # Log warning if resources are high
        if fd_info.get("warning", False):
            print(f"WARNING: High file descriptor usage for {project_id} at {stage}: {fd_info['current_fds']}/{fd_info['soft_limit']}")
        
        if memory_info.rss > 1024 * 1024 * 1024:  # > 1GB
            print(f"WARNING: High memory usage for {project_id} at {stage}: {memory_info.rss / 1024 / 1024:.1f} MB")
        
        return resource_info
        
    except Exception as e:
        return {"project_id": project_id, "stage": stage, "error": str(e)}

def sanitize_error_message(error_message: str, current_project_dir: Path) -> str:
    """
    Sanitize error messages to prevent cross-project path leakage.
    
    Replaces absolute paths from other projects with generic placeholders,
    while preserving paths from the current project for debugging.
    
    Args:
        error_message: Raw error message that may contain file paths
        current_project_dir: Current project directory to preserve in messages
        
    Returns:
        Sanitized error message with other project paths replaced
    """
    if not error_message:
        return error_message
    
    try:
        import re
        
        current_project_resolved = current_project_dir.resolve()
        current_project_str = str(current_project_resolved)
        
        # Pattern to match complete workspace project paths
        # This matches paths like /path/to/verifier/workspace/project-uuid/file.ext
        workspace_project_pattern = re.compile(
            r'(/[^/\s]*verifier[^/\s]*/workspace/[^/\s]+)(/[^\s]*)?', 
            re.IGNORECASE
        )
        
        sanitized_message = error_message
        
        # Find all workspace project paths and replace non-current ones
        workspace_matches = workspace_project_pattern.findall(sanitized_message)
        for match_tuple in workspace_matches:
            project_base_path = match_tuple[0]  # e.g., /path/to/verifier/workspace/project-uuid
            file_path = match_tuple[1]  # e.g., /vite.config.ts (or empty)
            full_match = project_base_path + file_path
            
            # Check if this is our current project by comparing the base path
            if current_project_str.endswith(project_base_path.split('/')[-1]):
                # This is our current project, don't sanitize
                continue
            
            # Extract just the project directory name for replacement
            project_name = project_base_path.split('/')[-1] if '/' in project_base_path else project_base_path
            
            if file_path:
                # Extract filename if present
                filename = file_path.split('/')[-1] if '/' in file_path else file_path
                replacement = f"<other-project:{project_name}/{filename}>"
            else:
                replacement = f"<other-project:{project_name}>"
            
            sanitized_message = sanitized_message.replace(full_match, replacement)
        
        # Additional cleanup: Replace any remaining absolute paths that contain workspace
        # but weren't caught by the previous patterns
        workspace_fragment_pattern = re.compile(r'/[^\s]*workspace[^\s]*', re.IGNORECASE)
        workspace_fragments = workspace_fragment_pattern.findall(sanitized_message)
        for fragment in workspace_fragments:
            if current_project_str not in fragment:
                # Replace with a generic placeholder
                sanitized_message = sanitized_message.replace(fragment, "<other-workspace-path>")
        
        return sanitized_message
        
    except Exception as e:
        # If sanitization fails, return original message with a warning
        print(f"Warning: Error message sanitization failed: {e}")
        return error_message

def sanitize_vite_error_output(output_lines: List[str], current_project_dir: Path) -> List[str]:
    """
    Sanitize Vite error output to prevent cross-project path contamination.
    
    Args:
        output_lines: List of output lines from Vite dev server
        current_project_dir: Current project directory 
        
    Returns:
        List of sanitized output lines
    """
    sanitized_lines = []
    
    for line in output_lines:
        sanitized_line = sanitize_error_message(line, current_project_dir)
        sanitized_lines.append(sanitized_line)
    
    return sanitized_lines 