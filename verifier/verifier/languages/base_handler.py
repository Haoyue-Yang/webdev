import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any

class BaseLanguageHandler(ABC):
    """
    Abstract base class for language-specific handlers.
    
    Each language handler is responsible for installing dependencies,
    starting and stopping the development server for a project.
    """

    def __init__(self, project_dir: Path, config: Dict[str, Any]):
        """
        Initializes the language handler.

        Args:
            project_dir: The path to the project directory.
            config: The language-specific configuration from config.yaml.
        """
        self.project_dir = project_dir
        self.config = config
        self.dev_server_process: subprocess.Popen = None

    @abstractmethod
    def install(self) -> Dict[str, Any]:
        """
        Install project dependencies.

        Returns:
            A dictionary with a 'success' boolean and optional error details.
        """
        pass

    @abstractmethod
    def start(self) -> Dict[str, Any]:
        """
        Start the development server.

        This method should handle starting the server as a background process
        and storing its process handle in `self.dev_server_process`.

        Returns:
            A dictionary with a 'success' boolean and optional error details.
        """
        pass

    @abstractmethod
    def stop(self) -> bool:
        """
        Stop the development server.

        This should gracefully terminate the process stored in `self.dev_server_process`.

        Returns:
            True if the server was stopped successfully, False otherwise.
        """
        pass

    def cleanup(self):
        """
        Perform any cleanup operations. This is always called, even if errors occur.
        """
        if self.dev_server_process:
            self.stop() 