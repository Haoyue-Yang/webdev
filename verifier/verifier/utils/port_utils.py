# verifier/utils/port_utils.py

import socket
import random
from typing import Optional


def is_port_in_use(port: int, host: str = 'localhost') -> bool:
    """
    Check if a given port is currently in use by attempting to bind to it.

    This method is more reliable than connect_ex because it detects:
    - Ports with active listeners
    - Ports in TIME_WAIT state
    - Ports bound but not listening

    Args:
        port: Port number to check
        host: Host address to bind to (default: localhost)

    Returns:
        True if port is in use, False if available
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return False  # Port is available
    except OSError:
        return True  # Port is in use
    finally:
        if sock:
            sock.close()


def find_available_port(min_port: int, max_port: int, max_retries: int = 50, host: str = 'localhost') -> int:
    """
    Find an available port within a given range [min_port, max_port].

    Args:
        min_port: The minimum port number in the range.
        max_port: The maximum port number in the range.
        max_retries: The maximum number of random ports to check.
        host: Host address to check availability on.

    Returns:
        An available port number.

    Raises:
        IOError: If no available port is found after max_retries.
    """
    for _ in range(max_retries):
        port = random.randint(min_port, max_port)
        if not is_port_in_use(port, host):
            return port

    raise IOError(f"Could not find an available port in the range [{min_port}-{max_port}] after {max_retries} retries.")


def kill_process_on_port(port: int) -> bool:
    """
    Attempt to kill any process using the specified port.

    Args:
        port: Port number to free up

    Returns:
        True if successful or no process found, False on error
    """
    try:
        import psutil

        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr.port == port and conn.status in ('LISTEN', 'ESTABLISHED'):
                try:
                    proc = psutil.Process(conn.pid)
                    proc.terminate()
                    proc.wait(timeout=3)
                    print(f"Terminated process {conn.pid} using port {port}")
                    return True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired) as e:
                    print(f"Failed to terminate process on port {port}: {e}")
                    return False
        return True  # No process found on port
    except Exception as e:
        print(f"Error checking port {port}: {e}")
        return False 