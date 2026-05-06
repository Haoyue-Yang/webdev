"""
    AdaArtifactParser:
        - 解析AdaArtifact XML字符串
        - 提取文件修改、项目结构、动作等信息
        - 返回解析后的字典
    AdaArtifactExecutor:
        - 执行AdaArtifact中的动作
        - 处理文件创建、shell命令日志、服务器启动等
    AdaReverseParser:
        - 从目录生成AdaArtifact XML字符串
"""

import re
from typing import Dict, Any, List, Tuple
import os
import time
import yaml
import logging
import signal
import traceback

from check_project_structure import ProjectStructureChecker
from utils.image_tool import supply_project_images
from utils.log_utils import log_wrapper

class AdaArtifactParser:
    """
    Parser for extracting and interpreting Ada artifacts and actions from structured strings.

    This class handles parsing of structured content containing file modifications,
    project structure, and various actions to be performed during project generation.

    Attributes:
        content (str): The structured string containing Ada artifacts and actions

    Example:
        >>> parser = AdaArtifactParser("<adaArtifact>...</adaArtifact>")
        >>> artifact = parser.parse()
    """

    def __init__(self, content: str):
        """
        Initialize the parser with content to be parsed.

        Args:
            content (str): The structured string containing Ada artifacts and actions
        """
        self.content = content
        self.project_structure = ""

    def parse(self) -> Dict[str, Any]:
        """
        Parses the content and extracts artifact metadata, modifications, and actions.

        Returns:
            Dict[str, Any]: A dictionary containing:
                - actions: List of parsed actions
                - startIdx: Index of the 'start' action
                - modifications: List of file modifications
                - project_structure: Extracted project structure
                - id: Artifact ID
                - title: Artifact title
        """
        # First check for modifications
        modifications = self._parse_modifications()

        # Parse project structure if present
        self._parse_project_structure()

        # Then parse the regular artifact
        artifact = self._parse_artifact()
        artifact["actions"], artifact["startIdx"] = self._parse_actions()

        # Add modifications and project structure to the artifact
        artifact["modifications"] = modifications
        artifact["project_structure"] = self.project_structure
        return artifact

    def _parse_project_structure(self) -> str:
        """
        Extracts the project structure from adaProjectStructure tags.

        Returns:
            str: The project structure string or empty string if not found

        Note:
            Looks for content between <adaProjectStructure> tags
        """
        structure_pattern = r"<adaProjectStructure>(.*?)</adaProjectStructure>"
        match = re.search(structure_pattern, self.content, re.DOTALL)
        if match:
            self.project_structure = match.group(1).strip()
            return self.project_structure
        return ""

    def _parse_artifact(self) -> Dict[str, str]:
        """
        Extracts the main artifact metadata from the content.

        Returns:
            Dict[str, str]: Dictionary containing:
                - id: The artifact identifier
                - title: The artifact title

        Note:
            Parses <adaArtifact> tag attributes
        """
        artifact_pattern = (
            r"<adaArtifact id=\"(?P<id>[^\"]+)\" title=\"(?P<title>[^\"]+)\">"
        )
        match = re.search(artifact_pattern, self.content)
        if not match:
            log_wrapper.info("Artifact metadata not found in the content.")
            return {}
        return match.groupdict()

    def _parse_actions(self) -> Tuple[List[Dict[str, Any]], int]:
        """
        Extracts all actions defined within the artifact.

        Returns:
            Tuple containing:
                - List[Dict[str, Any]]: List of parsed actions with their attributes
                - int: Index of the 'start' action (-1 if not found)

        Note:
            Each action includes type, filePath (if applicable), and content
        """
        action_pattern = (
            r"<adaAction type=\"(?P<type>[^\"]+)\"(?: filePath=\"(?P<filePath>[^\"]+)\")?>"
            r"(?P<content>.*?)"
            r"</adaAction>"
        )
        matches = re.finditer(action_pattern, self.content, re.DOTALL)
        actions = []
        start_idx = -1  # Default to -1 if no start action is found
        for idx, match in enumerate(matches):
            action = match.groupdict()
            action["content"] = action["content"].strip()
            actions.append(action)
            if action["type"] == "start":
                start_idx = idx
        return actions, start_idx

    def _parse_modifications(self) -> List[Dict[str, Any]]:
        """
        Extracts file modifications from the content.

        Returns:
            List[Dict[str, Any]]: List of modifications, each containing:
                - type: 'diff' or 'file'
                - path: File path
                - content: Modification content

        Note:
            Handles both complete file replacements and diffs
        """
        modifications = []

        # Look for modifications section
        mods_pattern = r"<modifications>(.*?)</modifications>"
        mods_match = re.search(mods_pattern, self.content, re.DOTALL)
        if not mods_match:
            return modifications

        mods_content = mods_match.group(1)

        # Parse diff elements
        diff_pattern = r"<diff path=\"([^\"]+)\">(.*?)</diff>"
        for match in re.finditer(diff_pattern, mods_content, re.DOTALL):
            modifications.append(
                {
                    "type": "diff",
                    "path": match.group(1),
                    "content": match.group(2).strip(),
                }
            )

        # Parse file elements
        file_pattern = r"<file path=\"([^\"]+)\">(.*?)</file>"
        for match in re.finditer(file_pattern, mods_content, re.DOTALL):
            modifications.append(
                {
                    "type": "file",
                    "path": match.group(1),
                    "content": match.group(2).strip(),
                }
            )

        return modifications

    def parse_file_order(self) -> Dict[str, List[str]]:
        """
        Parse file order from Stage 1 output.

        Returns:
            Dict containing implementation order of files
        """
        file_order_pattern = r"<adaFileOrder>(.*?)</adaFileOrder>"
        file_order_match = re.search(file_order_pattern, self.content, re.DOTALL)

        if not file_order_match:
            raise ValueError("Missing file order specification")

        return yaml.safe_load(file_order_match.group(1).strip())

    def parse_imports(self) -> Dict[str, str]:
        """
        Parse imports from project structure comments.

        Returns:
            Dict mapping file paths to their import statements
        """
        imports = {}
        for line in self.project_structure.split("\n"):
            if "#" in line:
                file_path, imports_str = line.split("#", 1)
                file_path = file_path.strip().rstrip(" └├─│")
                imports[file_path] = imports_str.strip()
        return imports

    def parse_file_sketches(self) -> Dict[str, Any]:
        """
        Parse file sketches from Stage 2 output.

        Returns:
            Dict containing file sketches and any new files added
        """
        sketch_pattern = r"<adaArtifact.*?>(.*?)</adaArtifact>"
        sketches = re.finditer(sketch_pattern, self.content, re.DOTALL)

        parsed_sketches = {}

        for sketch in sketches:
            content = sketch.group(1)
            file_pattern = r'<adaAction.*?filePath="([^"]+)".*?>(.*?)</adaAction>'

            for file_match in re.finditer(file_pattern, content, re.DOTALL):
                file_path = file_match.group(1)
                file_content = file_match.group(2).strip()
                parsed_sketches[file_path] = file_content

        return parsed_sketches


class AdaArtifactExecutor:
    """
    Executes adaActions from a parsed artifact. Handles file creation and shell command logging.

    This class is responsible for implementing the actions defined in parsed artifacts,
    including file operations, shell commands, and server management.
    """

    def __init__(self, root_path: str = "./runtime3/"):
        """
        Initialize the executor with a root path.

        Args:
            root_path (str, optional): Base directory for file operations. Defaults to "./runtime3/"
        """
        self.root_path = root_path
        self.shell_commands = []
        self.process = None  # Add process tracking

        # Ensure the root path exists
        if not os.path.exists(self.root_path):
            os.makedirs(self.root_path)

        # Register cleanup handler
        import atexit

        atexit.register(self._cleanup)

    def _cleanup(self):
        """Clean up resources when the executor is destroyed."""
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                logging.info("Process terminated successfully")
            except Exception as e:
                logging.error(f"Error terminating process: {e}")

    def execute(
        self, actions: List[Dict[str, Any]], modifications: List[Dict[str, Any]] = None
    ):
        """
        Executes all actions and modifications in the provided lists.

        Args:
            actions (List[Dict[str, Any]]): List of actions to execute
            modifications (List[Dict[str, Any]], optional): List of file modifications to apply

        Note:
            Applies modifications before executing regular actions
        """
        # First apply any modifications
        if modifications:
            for mod in modifications:
                self._apply_modification(mod)

        # Then execute regular actions
        for action in actions:
            action_type = action["type"]
            if action_type == "file" or action_type == "image":
                self._create_file(action)
            elif action_type == "shell":
                self._log_shell_command(action)
            elif action_type == "start":
                self._start_server(action)
            else:
                log_wrapper.info(f"Unknown action type: {action_type}")

    def _check_missing_files(
        self, project_structure: str, print_results: bool = True
    ) -> List[str]:
        """
        Checks which files from the project structure are missing.

        Args:
            project_structure (str): Expected project structure
            print_results (bool, optional): Whether to print check results. Defaults to True

        Returns:
            List[str]: List of missing file paths
        """
        checker = ProjectStructureChecker(project_structure, self.root_path)
        existing_files, missing_files, image_files = checker.check_structure()

        if print_results:
            checker.print_results(existing_files, missing_files, image_files)

        return missing_files

    def _request_missing_files(self, missing_files: List[str]) -> str:
        """
        Generates a request for implementing missing files.

        Args:
            missing_files (List[str]): List of files to be implemented

        Returns:
            str: Formatted request string for file implementation
        """
        files_list = "\n".join([f"- {f}" for f in missing_files])
        request = f"""The following files are missing from the project structure and need to be implemented:

{files_list}

Please provide the complete implementation for these missing files,\
    maintaining consistency with the existing codebase. \
        If you think the file is not necessary, or nothing to implement, \
            please return 'None' without any other content."""

        return request

    def _create_file(self, action: Dict[str, Any]):
        """
        Creates a file with the given content at the specified path.

        Args:
            action (Dict[str, Any]): Action containing:
                - filePath: Target file path
                - content: File content to write

        Note:
            Creates parent directories if they don't exist
        """
        try:
            file_path = os.path.join(self.root_path, action.get("filePath", ""))
            content = action.get("content", "")

            # Ensure the directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            # Write the content to the file
            with open(file_path, "w", encoding="utf-8") as file:
                file.write(content)

            log_wrapper.info(f"File created: {file_path}")
        except Exception as e:
            log_wrapper.info(f"Error creating file: {e}")
            log_wrapper.info(f"action: {action}")
            log_wrapper.info(f"root_path: {self.root_path}")

    def _log_shell_command(self, action: Dict[str, Any]):
        """
        Logs shell commands into the shell_commands list.

        Args:
            action (Dict[str, Any]): Action containing the shell command content
        """
        command = action.get("content", "").strip()
        if command:
            self.shell_commands.append(command)
            log_wrapper.info(f"Shell command logged: {command}")

    def _start_server(self, action: Dict[str, Any]):
        """
        Starts a server using the provided action.

        Args:
            action (Dict[str, Any]): Action containing the server start command
        """
        command = action.get("content", "").strip()
        if command:
            self.shell_commands.append(command)
            log_wrapper.info(f"[Start] Shell command logged: {command}")

    def get_shell_commands(self) -> List[str]:
        """
        Returns the list of logged shell commands.

        Returns:
            List[str]: All shell commands that have been logged
        """
        return self.shell_commands

    def _apply_modification(self, modification: Dict[str, Any]):
        """
        Applies a file modification, either as a full file replacement or as a diff.

        Args:
            modification (Dict[str, Any]): Modification details containing:
                - type: 'file' or 'diff'
                - path: Target file path
                - content: Modification content

        Note:
            Creates parent directories if they don't exist
        """
        file_path = os.path.join(self.root_path, modification["path"].lstrip("/"))
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        if modification["type"] == "file":
            # Direct file replacement
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(modification["content"])
            log_wrapper.info(f"File replaced: {file_path}")

        elif modification["type"] == "diff":
            # Apply diff
            if not os.path.exists(file_path):
                log_wrapper.info(f"Warning: Cannot apply diff to non-existent file: {file_path}")
                return

            # Read original file
            with open(file_path, "r", encoding="utf-8") as f:
                original_lines = f.readlines()

            # Parse and apply the diff
            modified_lines = self._apply_diff(original_lines, modification["content"])

            # Write modified content
            with open(file_path, "w", encoding="utf-8") as f:
                f.writelines(modified_lines)
            log_wrapper.info(f"Diff applied to: {file_path}")

    def _apply_diff(self, original_lines: List[str], diff_content: str) -> List[str]:
        """
        Applies a unified diff to the original file content with enhanced error handling.

        Args:
            original_lines (List[str]): Original file content as lines
            diff_content (str): Unified diff to apply

        Returns:
            List[str]: Modified file content as lines
        """
        try:
            # Add normalization for line endings
            original_lines = [line.rstrip("\r\n") + "\n" for line in original_lines]
            result_lines = original_lines.copy()

            # Parse each diff chunk
            for chunk in re.finditer(
                r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@(.*?)(?=@@|$)",
                diff_content,
                re.DOTALL,
            ):
                try:
                    orig_start = int(chunk.group(1)) - 1
                    orig_count = int(chunk.group(2))
                    new_start = int(chunk.group(3)) - 1
                    new_count = int(chunk.group(4))
                    chunk_content = chunk.group(5).strip().split("\n")[1:]

                    # Validate chunk boundaries
                    if orig_start < 0 or orig_start + orig_count > len(result_lines):
                        logging.error(
                            f"Invalid chunk boundaries: {orig_start}, {orig_count}"
                        )
                        continue

                    # Process the chunk lines
                    new_lines = []
                    orig_lines_consumed = 0

                    for line in chunk_content:
                        if line.startswith("+"):
                            new_lines.append(line[1:])
                        elif line.startswith("-"):
                            orig_lines_consumed += 1
                        else:
                            new_lines.append(line)
                            orig_lines_consumed += 1

                    # Replace the chunk in the result
                    result_lines[orig_start : orig_start + orig_count] = new_lines

                except Exception as e:
                    logging.error(f"Error processing diff chunk: {e}")
                    continue

            return result_lines

        except Exception as e:
            logging.error(f"Failed to apply diff: {e}")
            return original_lines

    def execute_batch(self, file_order: List[str], batch_number: int) -> List[str]:
        """
        Execute a batch of files according to the implementation order.

        Args:
            file_order: List of files in implementation order
            batch_number: Current batch number

        Returns:
            List of files implemented in this batch
        """
        start_idx = batch_number * self.batch_size
        end_idx = start_idx + self.batch_size
        batch_files = file_order[start_idx:end_idx]

        self.current_batch = batch_files
        return batch_files

    def execute_file(self, file_path: str, content: str, imports: Dict[str, str]):
        """
        Execute file creation with proper imports.

        Args:
            file_path: Path to the file
            content: File content
            imports: Import statements for the file
        """
        full_path = os.path.join(self.root_path, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Add imports at the top of the file if they exist
        if file_path in imports:
            content = f"{imports[file_path]}\n\n{content}"

        with open(full_path, "w") as f:
            f.write(content)

        self.implemented_files.append(file_path)


class AdaReverseParser:
    """
    A parser that reads files from a directory and generates AdaArtifact XML format.
    """

    def __init__(self, root_path: str):
        """
        Initialize the parser with a root path.

        Args:
            root_path (str, optional): Base directory for file operations. Defaults to "./runtime3/"
        """
        self.root_path = root_path

    def generate_project_structure(self):
        """
        Generates a tree-like representation of the project structure.
        Skips hidden files/directories (starting with .) and node_modules.

        Returns:
            String containing the project structure in tree format.
        """
        lines = ["<adaProjectStructure>", "/"]
        indent = "    "

        def add_to_tree(path, prefix="├── ", level=0):
            """Helper function to recursively build the tree"""
            if os.path.isfile(path):
                # Skip certain files
                basename = os.path.basename(path)
                if (
                    basename != ".DS_Store"
                    and not basename.startswith("__MACOSX")
                    and basename != "package-lock.json"
                    and basename != "save_step_results.py"
                    and basename != "pnpm-lock.yaml"
                    and basename != "tsconfig.tsbuildinfo"
                ):
                    lines.append(indent * level + prefix + basename)
            elif os.path.isdir(path):
                # Skip hidden directories and node_modules
                dirname = os.path.basename(path)
                if not dirname.startswith(".") and dirname not in [
                    "node_modules",
                    "logs",
                    "responses",
                    "step_results",
                    "test_recovery",
                    "dist",
                    ".verifier",
                ]:
                    if level > 0:  # Don't add root directory again
                        lines.append(indent * level + prefix + dirname + "/")

                    # Get all items in directory
                    items = os.listdir(path)
                    items.sort()  # Sort items alphabetically

                    # Process directories first, then files
                    dirs = [
                        item
                        for item in items
                        if os.path.isdir(os.path.join(path, item))
                    ]
                    files = [
                        item
                        for item in items
                        if os.path.isfile(os.path.join(path, item))
                    ]

                    # Process all items except the last one with ├──
                    for item in dirs + files:
                        full_path = os.path.join(path, item)
                        if item == (dirs + files)[-1]:
                            add_to_tree(full_path, prefix="└── ", level=level + 1)
                        else:
                            add_to_tree(full_path, prefix="├── ", level=level + 1)

        # Start building the tree from root path
        add_to_tree(self.root_path)
        lines.append("</adaProjectStructure>")
        return "\n".join(lines)

    def generate_artifact(
        self, title: str = "Generated Artifact", artifact_id: str = None
    ) -> str:
        """
        Generates an AdaArtifact XML string from files in the root directory.
        Skips hidden files/directories (starting with .) and node_modules.

        Args:
            title: Title for the artifact
            artifact_id: Optional ID for the artifact (defaults to timestamp)

        Returns:
            String containing the AdaArtifact XML
        """
        if not artifact_id:
            artifact_id = f"gen_{int(time.time())}"

        lines = []
        lines.append(f'<adaArtifact id="{artifact_id}" title="{title}">')

        # Walk through directory and add files
        for root, dirs, files in os.walk(self.root_path):
            # Skip hidden directories and node_modules
            dirs[:] = [
                d
                for d in dirs
                if (
                    not d.startswith(".")
                    and d != "node_modules"
                    and not d.startswith("logs")
                    and not d.startswith("test_recovery")
                    and not d.startswith("responses")
                    and not d.startswith("step_results")
                    and not d.startswith("__")
                    and not d.startswith("dist")
                )
            ]

            for file in files:
                # Skip hidden files, package-lock.json, logs, and responses
                if (
                    file == ".DS_Store"
                    or file == "package-lock.json"
                    or root.endswith("logs")
                    or root.endswith("responses")
                    or root.endswith("test_recovery")
                    or root.endswith("step_results")
                    or root.startswith("__")
                    or file.startswith("save_step_results")
                    or file.endswith(".png")
                    or file.endswith(".jpg")
                    or file.endswith(".jpeg")
                    or file.endswith(".gif")
                    or file.endswith(".svg")
                    or file == "pnpm-lock.yaml"
                    or file == "tsconfig.tsbuildinfo"
                ):
                    continue

                full_path = os.path.join(root, file)
                relative_path = os.path.relpath(full_path, self.root_path)

                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if relative_path.endswith(".url"):
                        # 保持图片描述内容一致
                        relative_path = relative_path[:-4]
                        
                    lines.append(f'<adaAction type="file" filePath="{relative_path}">')
                    lines.append(content)
                    lines.append("</adaAction>")
                except Exception as e:
                    log_wrapper.info(f"Error reading file {full_path}: {e}")
                    continue

        lines.append("</adaArtifact>")
        return "\n".join(lines)


def generate_code_from_adaartifact(
    adaartifact: str, project_dir: str = None, executor: AdaArtifactExecutor = None
) -> bool:
    """
    Generate code from AdaArtifact XML string.
    """
    # 1. Parse the AdaArtifact XML string
    parser = AdaArtifactParser(adaartifact)
    parsed_artifact = parser.parse()
    if parsed_artifact is None:
        return False

    # 2. Execute the actions
    executor = executor or AdaArtifactExecutor(root_path=project_dir)
    executor.execute(parsed_artifact["actions"])

    # try:
    #     supply_project_images(project_dir or executor.root_path)
    # except Exception as e:
    #     log_wrapper.info(f"图片替换错误:{traceback.format_exc()}")
    return True
