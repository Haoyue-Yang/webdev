#!/usr/bin/env python3
import os
import sys
import time
import signal
import argparse
import subprocess
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("cleanup_server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("cleanup_server")

def kill_server_process(project_dir, force=False):
    """
    Kill the server process running for the given project.
    
    Args:
        project_dir (Path): Path to the project directory
        force (bool): Whether to use SIGKILL instead of SIGTERM
        
    Returns:
        bool: True if process was killed successfully, False otherwise
    """
    pid_file = project_dir / "server.pid"
    
    if not pid_file.exists():
        logger.error(f"No server.pid file found in {project_dir}")
        return False
    
    try:
        with open(pid_file, 'r') as f:
            lines = f.readlines()
            if not lines:
                logger.error(f"Empty server.pid file in {project_dir}")
                return False
                
            pid = int(lines[0].strip())
            port = int(lines[1].strip()) if len(lines) > 1 else None
            url = lines[2].strip() if len(lines) > 2 else "unknown"
            
        logger.info(f"Killing server process (PID: {pid}, Port: {port}, URL: {url})")
        
        # Check if process is still running
        try:
            os.kill(pid, 0)  # This just checks if the process exists
            process_exists = True
        except OSError:
            process_exists = False
            
        if not process_exists:
            logger.info(f"Process with PID {pid} is not running anymore")
            pid_file.unlink(missing_ok=True)
            return True
            
        # Kill the process and its children
        try:
            sig = signal.SIGKILL if force else signal.SIGTERM
            pgid = os.getpgid(pid)
            os.killpg(pgid, sig)
            
            # Wait for the process to terminate
            max_wait = 5  # seconds
            for _ in range(max_wait * 2):
                try:
                    os.kill(pid, 0)
                    # If we get here, process still exists
                    time.sleep(0.5)
                except OSError:
                    # Process is gone
                    break
            
            # Check if the process is still running
            try:
                os.kill(pid, 0)
                if force:
                    logger.error(f"Failed to kill process {pid} even with SIGKILL")
                    return False
                else:
                    # Try with SIGKILL
                    logger.info(f"Process {pid} did not terminate with SIGTERM, trying SIGKILL")
                    return kill_server_process(project_dir, force=True)
            except OSError:
                # Process is gone
                logger.info(f"Successfully killed process {pid}")
                
                # Also clean up the port
                if port:
                    try:
                        # Check if anything is still using the port
                        subprocess.run(
                            f"lsof -i:{port} | grep {port} | awk '{{print $2}}' | xargs kill -9",
                            shell=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        logger.info(f"Cleaned up port {port}")
                    except Exception as e:
                        logger.warning(f"Error cleaning up port {port}: {e}")
                
                # Remove the PID file
                pid_file.unlink(missing_ok=True)
                return True
                
        except Exception as e:
            logger.error(f"Error killing process {pid}: {e}")
            return False
            
    except Exception as e:
        logger.error(f"Error reading PID file: {e}")
        return False

def cleanup_after_timeout(project_dir, timeout_seconds=-1):
    """
    Wait for the specified timeout and then kill the server process.
    
    Args:
        project_dir (str or Path): Project directory
        timeout_seconds (int): Time to wait in seconds before killing the process.
                              -1 means never kill (just verify the process exists).
    """
    project_dir = Path(project_dir)
    logger.info(f"Starting cleanup monitor for {project_dir}")
    
    if timeout_seconds == -1:
        logger.info(f"Timeout set to -1, server in {project_dir} will not be automatically killed")
        # Just check if process exists
        pid_file = project_dir / "server.pid"
        if pid_file.exists():
            with open(pid_file, 'r') as f:
                lines = f.readlines()
                if lines:
                    pid = int(lines[0].strip())
                    port = int(lines[1].strip()) if len(lines) > 1 else "unknown"
                    url = lines[2].strip() if len(lines) > 2 else "unknown"
                    logger.info(f"Server is running: PID={pid}, Port={port}, URL={url}")
                    # Check if process is actually running
                    try:
                        os.kill(pid, 0)
                        logger.info(f"Process with PID {pid} is running")
                    except OSError:
                        logger.warning(f"Process with PID {pid} is not running, but PID file exists")
                else:
                    logger.warning(f"PID file exists but is empty in {project_dir}")
        else:
            logger.warning(f"No server.pid file found in {project_dir}")
        return
    
    logger.info(f"Server in {project_dir} will be killed after {timeout_seconds} seconds")
    
    # Wait for the specified timeout
    time.sleep(timeout_seconds)
    
    # Kill the server process
    result = kill_server_process(project_dir)
    if result:
        logger.info(f"Successfully cleaned up server in {project_dir} after {timeout_seconds} seconds")
    else:
        logger.error(f"Failed to clean up server in {project_dir}")

def run_as_daemon(project_dir, timeout_seconds=-1):
    """
    Run the cleanup process as a daemon in the background.
    
    Args:
        project_dir (str or Path): Project directory
        timeout_seconds (int): Time to wait in seconds before killing the process
    """
    try:
        # Fork a child process
        pid = os.fork()
        
        if pid > 0:
            # Parent process, return to caller
            logger.info(f"Started cleanup daemon with PID {pid} for {project_dir}")
            return True
            
        # Child process, continue
        # Detach from parent environment
        os.setsid()
        
        # Fork a second child process and exit the first to prevent zombie processes
        pid = os.fork()
        if pid > 0:
            # First child exits
            sys.exit(0)
            
        # Second child (daemon) continues
        # Change working directory to a safe location
        os.chdir("/")
        
        # Close all open file descriptors
        for fd in range(0, 1024):
            try:
                os.close(fd)
            except OSError:
                pass
                
        # Redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        
        with open(os.devnull, 'r') as stdin, \
             open(os.devnull, 'w') as stdout, \
             open(os.devnull, 'w') as stderr:
            os.dup2(stdin.fileno(), sys.stdin.fileno())
            os.dup2(stdout.fileno(), sys.stdout.fileno())
            os.dup2(stderr.fileno(), sys.stderr.fileno())
        
        # Run the cleanup function
        cleanup_after_timeout(project_dir, timeout_seconds)
        
        # Exit the daemon
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"Error starting daemon: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Cleanup server processes after a specified timeout")
    parser.add_argument("project_dir", help="Directory containing the project")
    parser.add_argument("-t", "--timeout", type=int, default=-1, 
                       help="Timeout in seconds after which to kill the server. Default: -1 (never kill)")
    parser.add_argument("-n", "--no-daemon", action="store_true", 
                       help="Run in foreground (not as a daemon)")
    parser.add_argument("-k", "--kill-now", action="store_true",
                       help="Kill the server immediately without waiting")
    args = parser.parse_args()
    
    project_dir = Path(args.project_dir)
    
    if args.kill_now:
        # Kill the server immediately
        logger.info(f"Killing server in {project_dir} immediately")
        result = kill_server_process(project_dir)
        print(f"{'Successfully' if result else 'Failed to'} kill server in {project_dir}")
        return
        
    if args.no_daemon:
        # Run in foreground
        logger.info(f"Running cleanup in foreground with timeout {args.timeout} seconds")
        cleanup_after_timeout(project_dir, args.timeout)
    else:
        # Run as daemon
        logger.info(f"Starting cleanup daemon with timeout {args.timeout} seconds")
        success = run_as_daemon(project_dir, args.timeout)
        if success:
            print(f"Started cleanup daemon for {project_dir} with timeout {args.timeout} seconds")
            print(f"Check cleanup_server.log for details")
        else:
            print(f"Failed to start cleanup daemon")

if __name__ == "__main__":
    main() 