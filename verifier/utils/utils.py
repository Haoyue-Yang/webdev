"""
Utility functions for the model iteration project.
"""

import os
import time
import json
import threading
import functools
from typing import Dict, Any, List, Union, Callable

from .log_utils import log_wrapper

def retry(max_retries=3):
    """
    Retry decorator with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        
    Returns:
        Decorated function that will retry on exceptions
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        log_wrapper.error(f"Function {func.__name__} failed after {max_retries} attempts: {e}")
                        raise
                    wait_time = 4 ** (attempt + 1)
                    log_wrapper.info(f"Function {func.__name__} failed (attempt {attempt + 1}/{max_retries}). Retrying after {wait_time} seconds...")
                    time.sleep(wait_time)
            return None
        return wrapper
    return decorator

def timer(func):
    """
    Timer decorator to measure and log function execution time.
    
    Args:
        func: Function to decorate
        
    Returns:
        Decorated function that logs execution time
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        elapsed_time = time.time() - start_time
        log_wrapper.info(f"Function {func.__name__} completed in {elapsed_time:.2f} seconds")
        return result
    return wrapper

def display_timer(stop_event: threading.Event, interval: float = 5.0) -> None:
    """
    Display a timer in the console for long-running operations.
    
    Args:
        stop_event: Event to signal when to stop the timer
        interval: How often to update the timer (seconds)
    """
    start_time = time.time()
    while not stop_event.is_set():
        elapsed = time.time() - start_time
        if elapsed > interval:
            print(f"Operation running for {elapsed:.1f} seconds...", end="\r")
        time.sleep(0.5)


def save_json(data: Union[Dict[str, Any], List[Any]], filepath: str) -> None:
    """
    Save data as JSON to a file.
    
    Args:
        data: Data to save
        filepath: Path where the file should be saved
    """
    # Ensure directory exists
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    # Save the file
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2) 