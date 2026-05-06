#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Logging utilities for the model iteration project.
Provides a consistent logging interface using loguru.
"""

import os
import json
import sys
import time
from typing import Dict, Any, Optional

from loguru import logger
from flask import g, has_request_context

# Constants for metric fields
METRIC_KEY = ["ltype", "cost_s", "code", "answer_flag", "answer_type"]


def setup_logging(config: Dict[str, Any]) -> None:
    """
    Set up logging based on configuration.
    
    Args:
        config: Configuration dictionary with logging settings
    """
    log_config = config.get('log', {})
    log_path = log_config.get('dir', '/tmp')
    
    if not os.path.isabs(log_path):
        # If path is relative, make it absolute from project root
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        log_path = os.path.join(project_root, log_path)
    
    # Ensure log directory exists
    os.makedirs(log_path, exist_ok=True)
    
    # Get module name for log files
    module = log_config.get('name', "model_iteration")
    
    # Configure loguru with sensible defaults
    log_format = log_config.get('format', 
                              "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}")
    
    # Remove default logger and set up our own handlers
    logger.remove()
    
    # Console handler
    logger.add(
        sys.stderr,
        level=log_config.get('console.level', "INFO"),
        format=log_format
    )
    
    # Standard log file
    logger.add(
        os.path.join(log_path, f"{module}.log"),
        rotation=log_config.get('file.when', "midnight"),
        retention=int(log_config.get('file.backupCount', 7)),
        level=log_config.get('file.level', "INFO"),
        format=log_format,
        filter=lambda record: record["level"].name == "INFO"  # Only INFO level messages
    )
    
    # Warning/error log file
    logger.add(
        os.path.join(log_path, f"{module}.log.wf"),
        rotation=log_config.get('wf.when', "midnight"),
        retention=int(log_config.get('wf.backupCount', 7)),
        level=log_config.get('wf.level', "WARNING"),
        format=log_format,
        filter=lambda record: record["level"].name in ["WARNING", "ERROR", "CRITICAL"]  # Only WARNING and above
    )


def get_log_filepath(root_path: Optional[str] = None) -> str:
    """
    Get a path for saving log files.
    
    Args:
        root_path: Base directory for logs
        
    Returns:
        Path where logs should be saved
    """
    if root_path is None:
        root_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    
    # Create logs directory if it doesn't exist
    os.makedirs(root_path, exist_ok=True)
    
    # Create a filename with timestamp
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(root_path, f"api_call_{timestamp}.json")


def kvformat(kwargs: Dict[str, Any]) -> str:
    """
    Format key-value pairs for logging, with METRIC_KEY fields prioritized.
    
    Args:
        kwargs: Dictionary of key-value pairs to format
        
    Returns:
        Formatted string
    """
    s = ""

    # Metric fields first
    for key in METRIC_KEY:
        if key in kwargs:
            s = f"{s}{key}={kwargs[key]} "

    # Other fields
    for key in kwargs:
        if key not in METRIC_KEY:
            if isinstance(kwargs[key], (list, dict)):
                s = f"{s}{key}={json.dumps(kwargs[key], ensure_ascii=False)} "
            else:
                s = f"{s}{key}={kwargs[key]} "

    return s


class LogWrapper:
    """
    Logging wrapper that adds conversation_id to all logs when available in Flask context.
    """
    
    def __init__(self, logger_instance=logger):
        """Initialize with a logger instance (defaults to root loguru logger)"""
        self.logger = logger_instance
    
    def _get_conversation_id(self) -> str:
        """Get conversation_id from Flask request context if available"""
        try:
            if has_request_context():
                return getattr(g, 'conversation_id', '')
        except Exception:
            pass
        return ''
    
    def _format_message(self, msg: str) -> str:
        """Format message with conversation_id"""
        conversation_id = self._get_conversation_id()
        if conversation_id:
            return f"ConversationId:{conversation_id},{msg}"
        return msg
    
    def info(self, msg: str, *args, **kwargs):
        """Log at INFO level with conversation_id"""
        self.logger.info(self._format_message(msg),  *args, **kwargs)
    
    def warning(self, msg: str, *args, **kwargs):
        """Log at WARNING level with conversation_id"""
        self.logger.warning(self._format_message(msg), *args, **kwargs)
    
    def error(self, msg: str, *args, **kwargs):
        """Log at ERROR level with conversation_id"""
        self.logger.error(self._format_message(msg), *args, **kwargs)
    
    def debug(self, msg: str, *args, **kwargs):
        """Log at DEBUG level with conversation_id"""
        self.logger.debug(self._format_message(msg), *args, **kwargs)
    
    def critical(self, msg: str, *args, **kwargs):
        """Log at CRITICAL level with conversation_id"""
        self.logger.critical(self._format_message(msg), *args, **kwargs)


# Create default logger instance
log_wrapper = LogWrapper() 