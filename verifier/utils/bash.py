"""
Interactive Bash module
"""
import subprocess
from typing import List, Union, Dict, Any
import os
import signal
from .log_utils import log_wrapper

def run_command_in_bash(command_path, 
                        command: Union[List[str], str],
                        output_log_path: str, 
                        err_log_path: str,
                        timeout: float = 180.0) -> Dict[str, Any]:
    """
    Execute command using subprocess
    
    Args:
        command_path: Working directory to execute the command in
        command: The command to execute (string or list)
        output_log_path: Path to write stdout to
        err_log_path: Path to write stderr to
        timeout: Maximum execution time in seconds
        
    Returns:
        Dictionary containing:
        - output_log: Contents of stdout
        - err_log: Contents of stderr
        - return_code: Exit code of the process
        - timed_out: Boolean indicating if the process timed out
    """
    # create parent directory if not exists
    dirname = os.path.dirname(output_log_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
        
    output_content = ""
    error_content = ""
    timed_out = False
    
    with open(output_log_path, "w") as log_fp, open(err_log_path, "w") as err_fp:
        # Handle both string and list commands
        cmd = command if isinstance(command, str) else ' '.join(command)
        
        process = subprocess.Popen(
                cmd,
                stdout=log_fp,
                stderr=err_fp,
                cwd=command_path,
                shell=True,
                preexec_fn=os.setsid  # Create new process group
            )
        try:
            # Wait for the process with timeout
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # If timeout occurs, kill the entire process group
            log_wrapper.info("Timeout occurred, terminating process group")
            os.killpg(process.pid, signal.SIGKILL)  # Kill process group
            timed_out = True
            
    # Read the contents after process completion
    with open(output_log_path, "r") as log_fp:
        output_content = log_fp.read()
    with open(err_log_path, "r") as err_fp:
        error_content = err_fp.read()
            
    return {
        "output_log": output_content,
        "err_log": error_content,
        "return_code": process.returncode,
        "timed_out": timed_out
    }


if __name__ == "__main__":
    print(run_command_in_bash(".", "sleep 2 ; echo 'hello'; sleep 2 ; echo 'world'", "output.log", "error.log", timeout=60))