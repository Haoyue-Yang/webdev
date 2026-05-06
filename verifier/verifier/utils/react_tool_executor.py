# verifier/utils/react_tool_executor.py
"""
Tool definitions and execution for React Agent
Copied from temp/react_clean/tools/tools.py for Docker container compatibility
"""
import json
import os
import subprocess
from typing import Dict, Any, List, Optional


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for fuzzy matching (strip each line)"""
    return '\n'.join(line.strip() for line in text.split('\n'))


def _fuzzy_find(content: str, pattern: str):
    """
    Find pattern in content, with fallback to whitespace-normalized matching.
    Matches rollout's lmarena_server.py behavior.
    Returns (start_index, end_index, matched_text) or None.
    """
    # 1. Exact match
    idx = content.find(pattern)
    if idx != -1:
        return (idx, idx + len(pattern), pattern)

    # 2. Whitespace-normalized match (strip each line, sliding window)
    norm_pattern = _normalize_whitespace(pattern)
    pattern_lines = norm_pattern.split('\n')
    content_lines = content.split('\n')
    norm_content_lines = [line.strip() for line in content_lines]

    for i in range(len(content_lines) - len(pattern_lines) + 1):
        window = '\n'.join(norm_content_lines[i:i + len(pattern_lines)])
        if window == norm_pattern:
            start_idx = sum(len(line) + 1 for line in content_lines[:i])
            end_idx = start_idx + sum(len(line) + 1 for line in content_lines[i:i + len(pattern_lines)]) - 1
            matched_text = '\n'.join(content_lines[i:i + len(pattern_lines)])
            return (start_idx, end_idx, matched_text)

    return None


class ReactToolExecutor:
    """Execute React Agent tools with virtual file system"""

    def __init__(self, project_dir: str, template_path: Optional[str] = None, use_virtual_fs: bool = True):
        """
        Initialize tool executor

        Args:
            project_dir: Project directory path
            template_path: Path to project template JSON file
            use_virtual_fs: Use virtual file system (delay disk writes) - DEPRECATED, always False now
        """
        self.project_dir = project_dir
        self.virtual_fs = {}  # Virtual file system for tracking files only
        self.use_virtual_fs = use_virtual_fs
        self.disk_synced = False  # Track if virtual FS has been written to disk
        self.deleted_files = set()  # Track deleted files for proper sync

        # Always create project directory first
        os.makedirs(project_dir, exist_ok=True)

        # Load project template if provided and write to disk immediately
        if template_path and os.path.exists(template_path):
            self._load_template(template_path)

    def _load_template(self, template_path: str):
        """
        Load project template, write to disk immediately, and track in virtual FS

        Args:
            template_path: Path to template JSON file
        """
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                template_data = json.load(f)

            if template_data.get("status") == "success":
                files = template_data.get("files", [])
                for file_info in files:
                    path = file_info.get("path")
                    content = file_info.get("content", "")
                    if path:
                        # Track in virtual FS
                        self.virtual_fs[path] = content

                        # Write to disk immediately
                        full_path = os.path.join(self.project_dir, path)
                        os.makedirs(os.path.dirname(full_path), exist_ok=True)
                        with open(full_path, 'w', encoding='utf-8') as f:
                            f.write(content)
        except Exception as e:
            # If template loading fails, continue without it
            pass

    def _copy_node_modules_cache(self):
        """
        Copy pre-installed node_modules from cache directory to project
        This avoids running npm install for every project
        """
        import shutil

        # Path to cached node_modules (relative to this file's location)
        cache_dir = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "temp",
            "react_clean",
            "templates",
            "node_modules_cache"
        )
        cache_node_modules = os.path.join(cache_dir, "node_modules")

        if not os.path.exists(cache_node_modules):
            return  # Cache not available, will need npm install later

        target_node_modules = os.path.join(self.project_dir, "node_modules")

        if os.path.exists(target_node_modules):
            return  # Already exists

        try:
            # Copy node_modules from cache (use copy to avoid symlink issues)
            shutil.copytree(cache_node_modules, target_node_modules, symlinks=True)
            print(f"✓ Copied node_modules from cache (no npm install needed!)")
        except Exception as e:
            # If copy fails, continue without it (will npm install later if needed)
            print(f"⚠️  Failed to copy node_modules cache: {e}")
            pass

    def sync_single_file_to_disk(self, path: str):
        """
        Sync a single file from virtual FS to disk (for lint checks)

        Args:
            path: Relative file path
        """
        if not self.use_virtual_fs:
            return  # Already on disk

        if path not in self.virtual_fs:
            return  # File doesn't exist in virtual FS

        # Create project directory if needed
        os.makedirs(self.project_dir, exist_ok=True)

        # Write single file to disk
        full_path = os.path.join(self.project_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(self.virtual_fs[path])

    def sync_to_disk(self):
        """
        Sync virtual file system to disk
        Called before operations that need actual files (build, npm install)
        """
        if self.disk_synced:
            return  # Already synced

        # Create project directory
        os.makedirs(self.project_dir, exist_ok=True)

        # Delete files that were marked for deletion
        for path in self.deleted_files:
            full_path = os.path.join(self.project_dir, path)
            try:
                if os.path.exists(full_path):
                    os.remove(full_path)
            except Exception:
                pass  # Ignore deletion errors

        # Write all files from virtual FS to disk
        for path, content in self.virtual_fs.items():
            full_path = os.path.join(self.project_dir, path)

            # Create parent directories
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # Write file
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)

        self.disk_synced = True

    def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool

        Args:
            tool_name: Tool name
            tool_input: Tool input parameters

        Returns:
            Tool execution result
        """
        if tool_name == "create_file":
            return self._create_file(tool_input)
        elif tool_name == "edit_file":
            return self._edit_file(tool_input)
        elif tool_name == "read_file":
            return self._read_file(tool_input)
        elif tool_name == "list_files":
            return self._list_files()
        elif tool_name == "delete_file":
            return self._delete_file(tool_input)
        elif tool_name == "install_npm_packages":
            return self._install_npm_packages(tool_input)
        elif tool_name == "build_project":
            return self._build_project()
        else:
            return {
                "status": "error",
                "message": f"Unknown tool: {tool_name}"
            }

    def _create_file(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create or overwrite a file (always write to disk, virtual FS is for tracking only)"""
        import mimetypes

        try:
            path = params["path"]
            content = params["content"]

            # Validate path
            if path.startswith('/') or '..' in path:
                return {
                    "status": "error",
                    "message": "Invalid path: must not start with '/' and must not contain '..'"
                }

            # Update virtual fs for tracking
            self.virtual_fs[path] = content

            # Always write to disk
            full_path = os.path.join(self.project_dir, path)

            # Create parent directories
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # Write file
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)

            return {
                "status": "success",
                "message": f"Successfully created file {path}",
                "lint_error": None
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error creating file: {str(e)}"
            }

    def _edit_file(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Edit a file by replacing context with replacement (always read/write to disk, virtual FS is for tracking only)"""
        try:
            path = params["path"]
            context = params.get("context") or params.get("context_block")
            replacement = params.get("replacement") or params.get("replacement_block")

            # Validate path
            if path.startswith('/') or '..' in path:
                return {
                    "status": "error",
                    "message": "Invalid path: must not start with '/' and must not contain '..'"
                }

            # Always read from disk
            full_path = os.path.join(self.project_dir, path)
            if not os.path.exists(full_path):
                return {
                    "status": "error",
                    "message": f"File {path} does not exist"
                }

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Find context using fuzzy matching (matches rollout behavior)
            match_result = _fuzzy_find(content, context)
            if match_result is None:
                return {
                    "status": "error",
                    "message": "Context not found in file"
                }

            start_idx, end_idx, matched_text = match_result
            match_type = "exact" if matched_text == context else "fuzzy"

            # Replace using index-based substitution (matches rollout behavior)
            new_content = content[:start_idx] + replacement + content[end_idx:]

            # Update virtual fs for tracking
            self.virtual_fs[path] = new_content

            # Always write to disk
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            return {
                "status": "success",
                "message": "Edit applied.",
                "match_type": match_type,
                "matched_text": matched_text,
                "lint_error": None
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error editing file: {str(e)}"
            }

    def _read_file(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Read a file (from virtual FS if available)"""
        import mimetypes

        try:
            path = params["path"]

            # Validate path
            if path.startswith('/') or '..' in path:
                return {
                    "status": "error",
                    "message": "Invalid path: must not start with '/' and must not contain '..'"
                }

            # Try to read from virtual FS first
            if path in self.virtual_fs:
                content = self.virtual_fs[path]
            else:
                # Read from disk
                full_path = os.path.join(self.project_dir, path)

                # Check if file exists
                if not os.path.exists(full_path):
                    return {
                        "status": "error",
                        "message": f"File {path} does not exist"
                    }

                # Read file
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()

            # Determine content type
            content_type = mimetypes.guess_type(path)[0] or "text/plain"

            return {
                "status": "success",
                "file": {
                    "path": path,
                    "content": content,
                    "contentType": content_type
                }
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error reading file: {str(e)}"
            }

    def _list_files(self) -> Dict[str, Any]:
        """List all files in the project (from virtual FS if enabled)"""
        import mimetypes

        try:
            files = []

            # If using virtual FS, return virtual files
            if self.use_virtual_fs and self.virtual_fs:
                for path, content in self.virtual_fs.items():
                    content_type = mimetypes.guess_type(path)[0] or "text/plain"
                    files.append({
                        "path": path,
                        "content": content,
                        "contentType": content_type
                    })
            else:
                # Walk through project directory on disk
                if not os.path.exists(self.project_dir):
                    return {
                        "status": "success",
                        "files": []
                    }

                for root, dirs, filenames in os.walk(self.project_dir):
                    # Skip node_modules, .git, etc.
                    dirs[:] = [d for d in dirs if d not in ['node_modules', '.git', 'dist', '.next']]

                    for filename in filenames:
                        full_path = os.path.join(root, filename)
                        rel_path = os.path.relpath(full_path, self.project_dir)

                        # Read content
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                content = f.read()

                            content_type = mimetypes.guess_type(rel_path)[0] or "text/plain"

                            files.append({
                                "path": rel_path,
                                "content": content,
                                "contentType": content_type
                            })
                        except Exception:
                            # Skip binary files or unreadable files
                            continue

            return {
                "status": "success",
                "files": files
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error listing files: {str(e)}"
            }

    def _delete_file(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a file (always delete from disk, update virtual FS for tracking)"""
        try:
            path = params["path"]

            # Validate path
            if path.startswith('/') or '..' in path:
                return {
                    "status": "error",
                    "message": "Invalid path: must not start with '/' and must not contain '..'"
                }

            # Always delete from disk
            full_path = os.path.join(self.project_dir, path)

            # Check if file exists on disk
            if not os.path.exists(full_path):
                return {
                    "status": "error",
                    "message": f"File {path} does not exist"
                }

            # Delete file from disk
            os.remove(full_path)

            # Remove from virtual fs for tracking
            if path in self.virtual_fs:
                del self.virtual_fs[path]

            return {
                "status": "success"
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error deleting file: {str(e)}"
            }

    def _install_npm_packages(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Install npm packages (files already on disk)"""
        try:
            packages = params["packages"]

            # Check if package.json exists
            package_json_path = os.path.join(self.project_dir, "package.json")
            if not os.path.exists(package_json_path):
                return {
                    "status": "error",
                    "message": "package.json not found. Please create it first.",
                    "stdout": "",
                    "stderr": ""
                }

            # First run npm install to ensure base dependencies are installed
            print(f"[install_npm_packages] Running npm install first...")
            base_install_result = subprocess.run(
                ["npm", "install"],
                cwd=self.project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=180  # 3 minutes timeout
            )

            if base_install_result.returncode != 0:
                return {
                    "status": "error",
                    "message": f"npm install failed: {base_install_result.stderr}",
                    "stdout": base_install_result.stdout,
                    "stderr": base_install_result.stderr
                }

            # Then run npm install -S with the specified packages
            cmd = ["npm", "install", "-S"] + packages

            result = subprocess.run(
                cmd,
                cwd=self.project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=120  # Reduced timeout to 120s (2 minutes)
            )

            if result.returncode == 0:
                return {
                    "status": "success",
                    "stdout": result.stdout,
                    "stderr": result.stderr
                }
            else:
                return {
                    "status": "error",
                    "message": f"npm install failed with code {result.returncode}",
                    "stdout": result.stdout,
                    "stderr": result.stderr
                }

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": "npm install timed out (120s)",
                "stdout": "",
                "stderr": ""
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Error installing packages: {str(e)}",
                "stdout": "",
                "stderr": ""
            }

    def _build_project(self) -> Dict[str, Any]:
        """Build the project using npm run build (files already on disk)"""
        try:
            # Check if package.json exists
            package_json_path = os.path.join(self.project_dir, "package.json")
            if not os.path.exists(package_json_path):
                return {
                    "status": "error",
                    "message": "package.json not found.",
                    "stdout": "",
                    "stderr": ""
                }

            # Check if node_modules exists, try cache if not
            node_modules_path = os.path.join(self.project_dir, "node_modules")
            if not os.path.exists(node_modules_path):
                # Try to copy from cache first (FAST!)
                print(f"[build_project] node_modules not found, trying cache...")
                self._copy_node_modules_cache()

            # Always run npm install to ensure dependencies are up-to-date
            print(f"[build_project] Running npm install...")
            install_result = subprocess.run(
                ["npm", "install"],
                cwd=self.project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=180  # 3 minutes timeout
            )

            if install_result.returncode != 0:
                return {
                    "status": "error",
                    "message": f"npm install failed before build: {install_result.stderr}",
                    "stdout": install_result.stdout,
                    "stderr": install_result.stderr
                }

            # Run npm run build
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=self.project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=180  # Reduced timeout to 180s (3 minutes)
            )

            if result.returncode == 0:
                return {
                    "status": "success",
                    "stdout": result.stdout,
                    "stderr": result.stderr
                }
            else:
                return {
                    "status": "error",
                    "message": f"Build failed with code {result.returncode}",
                    "stdout": result.stdout,
                    "stderr": result.stderr
                }

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": "Build timed out (180s)",
                "stdout": "",
                "stderr": ""
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Error building project: {str(e)}",
                "stdout": "",
                "stderr": ""
            }

    def get_files(self) -> Dict[str, str]:
        """Get all files from virtual file system"""
        return self.virtual_fs.copy()
