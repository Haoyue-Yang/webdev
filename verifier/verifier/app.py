#!/usr/bin/env python3
"""
Flask server for the verifier service.

This server provides a REST API for running project verification.
It accepts code as a dictionary format: {"path": "code content"}
"""

import os
import tempfile
import shutil
import json
import uuid
import tarfile
import io
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from flask import Flask, request, jsonify
import traceback
import re
import requests as req_lib

from .verifier import Verifier, VALID_AGENT_TYPES

app = Flask(__name__)

def infer_language(code_dict: Dict[str, str]) -> Optional[str]:
    """
    Infer the programming language from the code dictionary.

    Args:
        code_dict: Dictionary with file paths as keys and content as values

    Returns:
        Inferred programming language ("react", "html", or "webdev_scaffold_2") or None if unknown
    """
    extension_set = set([filepath.split('.')[-1] for filepath in code_dict.keys() if "." in filepath])

    if "jsx" in extension_set or "tsx" in extension_set or "ts" in extension_set:
        return "react"
    elif "html" in extension_set and extension_set.issubset({'html', 'js', 'css'}):
        return "html"
    elif "vue" in extension_set:
        return "react"  # TODO: Add vue support

    # Fallback: if there's a package.json with react dependency, treat as react
    for filepath, content in code_dict.items():
        if filepath.endswith("package.json"):
            try:
                pkg = json.loads(content)
                deps = pkg.get("dependencies", {})
                if any("react" in d for d in deps):
                    return "react"
            except (json.JSONDecodeError, TypeError):
                pass

    return None

def parse_adaartifact_files(response: str) -> Dict[str, str]:
    """
    Parse AdaArtifact XML response and extract file contents.
    
    Args:
        response (str): The model's response containing AdaArtifact XML
        
    Returns:
        Dict[str, str]: Dictionary mapping file paths to their contents
    """
    files = {}
    
    # Parse adaAction elements with file type
    file_pattern = r'<adaAction\s+type="file"\s+filePath="([^"]+)"[^>]*>(.*?)</adaAction>'
    matches = re.finditer(file_pattern, response, re.DOTALL)
    
    for match in matches:
        file_path = match.group(1)
        content = match.group(2).strip()
        files[file_path] = content
    
    return files

class VerifierService:
    """Service class to handle verifier operations"""

    def __init__(self):
        # Don't create a reusable verifier instance - create fresh ones per verification
        # Use WORKSPACE_DIR env var or default to /app/verifier/workspace (writable in container)
        # workspace_path = os.environ.get('WORKSPACE_DIR', '/app/verifier/workspace')
        workspace_path = os.environ.get('WORKSPACE_DIR', './verifier/workspace')
        self.workspace_dir = Path(workspace_path)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
    
    def create_project_from_dict(self, code_dict: Dict[str, str], project_id: str) -> Path:
        """
        Create a project directory structure from code dictionary.
        
        Args:
            code_dict: Dictionary with file paths as keys and content as values
            project_id: Unique project identifier
            
        Returns:
            Path to the created project directory
        """
        project_dir = self.workspace_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        
        for file_path, content in code_dict.items():
            # Normalize the file path
            file_path = file_path.lstrip('/')  # Remove leading slash if present
            full_path = project_dir / file_path
            
            # Create parent directories if they don't exist
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write the file content
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
                
        return project_dir
    
    def verify_project(self, code_dict: Dict[str, str], query: str, language: str = None, project_id: Optional[str] = None, agent_type: str = "static", keep_existing_project: bool = False, evaluator_config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Run verification on a project created from code dictionary.

        Args:
            code_dict: Dictionary with file paths as keys and content as values
            query: User query describing the project requirements
            language: Programming language/framework (default: "react")
            project_id: Optional project identifier. If not provided, generates a new UUID
            agent_type: Type of agent evaluation ("static", "agent_tars", or "interactive_video")
            keep_existing_project: If False (default), remove existing project directory to ensure clean state.
                                 If True, preserve existing project files and only clear .verifier directory.
            evaluator_config: Optional dict to override evaluator config for this request (deep-merged into loaded config).

        Returns:
            Verification results dictionary including project_id
        """
        try:
            # Generate project ID if not provided
            if project_id is None:
                project_id = str(uuid.uuid4())

            # Build timestamped folder name to avoid conflicts across concurrent/repeated runs
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            folder_id = f"{project_id}_{timestamp}"

            # Handle existing project directory at service level
            project_dir = self.workspace_dir / folder_id
            if not keep_existing_project and project_dir.exists():
                print(f"Removing existing project directory: {project_dir}")
                try:
                    shutil.rmtree(project_dir)
                    print(f"Successfully removed existing project directory")
                except Exception as e:
                    print(f"Warning: Could not remove existing project directory: {e}")

            # Create project from code dictionary
            project_dir = self.create_project_from_dict(code_dict, folder_id)

            # Create fresh verifier instance for each verification to prevent state reuse
            verifier = Verifier(evaluator_overrides=evaluator_config)

            # Run verification (always keep existing since we handled removal above)
            results = verifier.test_project(
                project_dir=str(project_dir),
                language=language,
                query=query,
                agent_type=agent_type,
                keep_existing_project=True  # Service layer handles directory removal
            )

            # Add project information to results
            results["project_id"] = project_id
            results["project_path"] = str(project_dir)

            # Write response to .verifier/verifier_response.json for async polling
            verifier_dir = project_dir / '.verifier'
            verifier_dir.mkdir(parents=True, exist_ok=True)
            response_file = verifier_dir / 'verifier_response.json'
            with open(response_file, 'w') as f:
                json.dump(results, f)

            return results
            
        except Exception as e:
            return {
                "error": f"Verification failed: {str(e)}",
                "traceback": traceback.format_exc(),
                "project_id": project_id if 'project_id' in locals() else None
            }
    
    def get_project_info(self, project_id: str) -> Dict[str, Any]:
        """
        Get information about a project in the workspace.
        
        Args:
            project_id: Project identifier
            
        Returns:
            Dictionary with project information
        """
        project_dir = self.workspace_dir / project_id
        
        if not project_dir.exists():
            return {"error": f"Project {project_id} not found"}
        
        # Get file listing
        files = []
        for file_path in project_dir.rglob('*'):
            if file_path.is_file():
                rel_path = file_path.relative_to(project_dir)
                files.append({
                    "path": str(rel_path),
                    "size": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime
                })
        
        return {
            "project_id": project_id,
            "project_path": str(project_dir),
            "exists": True,
            "files": files,
            "file_count": len(files)
        }
    
    def delete_project(self, project_id: str) -> Dict[str, Any]:
        """
        Delete a project from the workspace.
        
        Args:
            project_id: Project identifier
            
        Returns:
            Dictionary with deletion result
        """
        project_dir = self.workspace_dir / project_id
        
        if not project_dir.exists():
            return {"error": f"Project {project_id} not found"}
        
        try:
            shutil.rmtree(project_dir)
            return {
                "project_id": project_id,
                "deleted": True,
                "message": f"Project {project_id} deleted successfully"
            }
        except Exception as e:
            return {
                "error": f"Failed to delete project {project_id}: {str(e)}",
                "project_id": project_id,
                "deleted": False
            }
    
    def list_projects(self) -> Dict[str, Any]:
        """
        List all projects in the workspace.
        
        Returns:
            Dictionary with list of projects
        """
        projects = []
        for project_dir in self.workspace_dir.iterdir():
            if project_dir.is_dir():
                file_count = len(list(project_dir.rglob('*')))
                projects.append({
                    "project_id": project_dir.name,
                    "project_path": str(project_dir),
                    "file_count": file_count,
                    "modified": project_dir.stat().st_mtime
                })
        
        return {
            "workspace_path": str(self.workspace_dir),
            "project_count": len(projects),
            "projects": projects
        }

    def verify_scaffold_project(self, response: str, query: str, project_id: Optional[str] = None, agent_type: str = "static", evaluator_config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Run verification on a scaffold project created from tool_call response.

        If response starts with [tar_url]:, downloads and extracts the tar
        instead of replaying tool_calls.

        Args:
            response: LLM response containing <tool_call>...</tool_call> blocks,
                      or "[tar_url]:<url>" for pre-built tar projects
            query: User query describing the project requirements
            project_id: Optional project identifier. If not provided, generates a new UUID
            agent_type: Type of agent evaluation ("static", "agent_tars", or "interactive_video")
            evaluator_config: Optional dict to override evaluator config for this request.

        Returns:
            Verification results dictionary including project_id
        """
        try:
            # Generate project ID if not provided
            if project_id is None:
                project_id = str(uuid.uuid4())

            # Handle existing project directory at service level
            project_dir = self.workspace_dir / project_id
            if project_dir.exists():
                print(f"Removing existing project directory: {project_dir}")
                try:
                    shutil.rmtree(project_dir)
                    print(f"Successfully removed existing project directory")
                except Exception as e:
                    print(f"Warning: Could not remove existing project directory: {e}")

            # Create project directory
            project_dir.mkdir(parents=True, exist_ok=True)

            # Check if response is a tar URL
            if response.startswith("[tar_url]:"):
                results = self._verify_tar_project(
                    tar_url=response[len("[tar_url]:"):],
                    project_dir=project_dir,
                    query=query,
                    agent_type=agent_type,
                    evaluator_config=evaluator_config,
                )
            else:
                # Create fresh verifier instance for each verification
                verifier = Verifier(evaluator_overrides=evaluator_config)

                # Run scaffold verification
                results = verifier.test_scaffold_project(
                    project_dir=str(project_dir),
                    response=response,
                    query=query,
                    agent_type=agent_type
                )

            # Add project information to results
            results["project_id"] = project_id
            results["project_path"] = str(project_dir)

            # Write response to .verifier/verifier_response.json for async polling
            verifier_dir = project_dir / '.verifier'
            verifier_dir.mkdir(parents=True, exist_ok=True)
            response_file = verifier_dir / 'verifier_response.json'
            with open(response_file, 'w') as f:
                json.dump(results, f)

            return results

        except Exception as e:
            return {
                "error": f"Scaffold verification failed: {str(e)}",
                "traceback": traceback.format_exc(),
                "project_id": project_id if 'project_id' in locals() else None
            }

    def _verify_tar_project(self, tar_url: str, project_dir: Path, query: str, agent_type: str = "static", evaluator_config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Verify a pre-built project from a BOS tar URL.

        Downloads the tar, extracts it to project_dir, then starts HTTP server
        on dist/ and runs evaluations (skipping install + build).

        Args:
            tar_url: HTTP URL to the .tar file on BOS
            project_dir: Path to extract the project into
            query: User query describing the project requirements
            agent_type: Type of agent evaluation

        Returns:
            Verification results dictionary
        """
        print(f"[tar] Downloading tar from: {tar_url}")

        # Download tar
        resp = req_lib.get(tar_url, timeout=120)
        resp.raise_for_status()

        # Extract tar to project_dir
        tar_bytes = io.BytesIO(resp.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:*") as tar:
            # Security: prevent path traversal
            for member in tar.getmembers():
                member_path = Path(project_dir / member.name).resolve()
                if not str(member_path).startswith(str(project_dir.resolve())):
                    raise ValueError(f"Tar member {member.name} escapes project directory")
            tar.extractall(path=str(project_dir))

        print(f"[tar] Extracted tar to: {project_dir}")

        # Use verifier with keep_existing_project=True since files are already extracted
        verifier = Verifier(evaluator_overrides=evaluator_config)

        language = "webdev_scaffold_2"
        lang_config = verifier.config['languages'].get(language, {}).copy()
        # Set empty response since we don't need tool_call replay
        lang_config['response'] = ''
        lang_config['language_name'] = language
        lang_config['skip_install'] = True  # Signal handler to skip install
        verifier.config['languages'][language] = lang_config

        return verifier.test_project(
            project_dir=str(project_dir),
            language=language,
            query=query,
            agent_type=agent_type,
            keep_existing_project=True,
        )

# Create global verifier service instance
verifier_service = VerifierService()

def parse_llm_response_to_code_dict(response_text: str) -> Dict[str, str]:
    """
    Parses a formatted string from an LLM response into a code dictionary.

    The expected format for each file is:
    ---
    ### [File Path]
    ```[language]
    [File Content]
    ```

    Args:
        response_text: The raw string response from the LLM.

    Returns:
        A dictionary where keys are file paths and values are file contents.
    """
    code_dict = {}
    # Regex to find file blocks: matches file path and code content
    # It looks for '### filepath' followed by a fenced code block.
    # re.DOTALL allows '.' to match newlines, capturing multi-line code.
    pattern = re.compile(r"###\s*(?P<path>.*?)\s*\n```[^\n]*\n(?P<content>.*?)\n```", re.DOTALL)
    
    # Split the text by the '---' separator to handle individual file blocks
    blocks = response_text.split("\n---\n")

    for block in blocks:
        match = pattern.search(block)
        if match:
            file_path = match.group('path').strip()
            # The file path might have special characters like '【' and '】'
            file_path = file_path.replace('【', '').replace('】', '')
            content = match.group('content')
            if file_path:
                code_dict[file_path] = content
                
    return code_dict

@app.route('/verify-response', methods=['POST'])
def verify_response():
    """
    Parses an LLM response string, converts it to a project, and verifies it.
    
    Expected JSON payload:
    {
        "response_text": "LLM response string with file blocks...",
        "query": "Create a simple React app with counter",
        "language": "react",  // optional, will use automatic type inference if not provided
        "project_id": "my-project-123",  // optional, generates UUID if not provided
        "response_type": "default" | "ada"  // optional, defaults to "default"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        response_text = data.get('response_text')
        query = data.get('query')
        project_id = data.get('project_id')
        agent_type = data.get('agent_type', 'static')
        response_type = data.get('response_type', 'default')  # New parameter
        keep_existing_project = data.get('keep_existing_project', False)
        evaluator_config = data.get('evaluator_config', None)

        if agent_type not in VALID_AGENT_TYPES:
            return jsonify({"error": f"agent_type must be one of: {', '.join(VALID_AGENT_TYPES)}"}), 400

        if not response_text or not isinstance(response_text, str):
            return jsonify({"error": "Missing or invalid 'response_text' field"}), 400
        
        if not query or not isinstance(query, str):
            return jsonify({"error": "Missing or invalid 'query' field"}), 400

        # Parse the LLM response based on response_type
        if response_type == 'ada':
            # Use the ada artifact parser
            code_dict = parse_adaartifact_files(response_text)
        else:
            # Use the default parser
            code_dict = parse_llm_response_to_code_dict(response_text)

        if not code_dict:
            return jsonify({"error": "Failed to parse code from 'response_text'"}), 400

        # Get language from data or infer automatically
        language = data.get('language', None)
        if language is None:
            language = infer_language(code_dict)
            if language is None:
                return jsonify({"error": "Failed to infer language from code. Response: " + response_text}), 400

        # Run verification using the parsed code
        results = verifier_service.verify_project(code_dict, query, language, project_id, agent_type=agent_type, keep_existing_project=keep_existing_project, evaluator_config=evaluator_config)
        
        return jsonify(results)

    except Exception as e:
        app.logger.error(f"Error in verify-response endpoint: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "traceback": traceback.format_exc()
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "verifier"})

@app.route('/verify', methods=['POST'])
def verify():
    """
    Main verification endpoint.
    
    Expected JSON payload:
    {
        "code": {"path/to/file.js": "content", "package.json": "{...}"},
        "query": "Create a simple React app with counter",
        "language": "react",  // optional, defaults to "react"
        "project_id": "my-project-123"  // optional, generates UUID if not provided
    }
    
    Returns:
    {
        "project_id": "my-project-123",
        "project_path": "/path/to/verifier/workspace/my-project-123",
        "evaluations": {
            "running": {...},
            "aesthetics": {...},
            "functional": {...}
        }
    }
    """
    try:
        # Parse request JSON
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Validate required fields
        if 'code' not in data:
            return jsonify({"error": "Missing 'code' field"}), 400
        
        if 'query' not in data:
            return jsonify({"error": "Missing 'query' field"}), 400
        
        code_dict = data['code']
        query = data['query']
        language = data.get('language', 'react')
        project_id = data.get('project_id')
        agent_type = data.get('agent_type', 'static')
        keep_existing_project = data.get('keep_existing_project', False)
        evaluator_config = data.get('evaluator_config', None)

        if agent_type not in VALID_AGENT_TYPES:
            return jsonify({"error": f"agent_type must be one of: {', '.join(VALID_AGENT_TYPES)}"}), 400

        # Validate code dictionary
        if not isinstance(code_dict, dict):
            return jsonify({"error": "'code' must be a dictionary"}), 400
        
        if not code_dict:
            return jsonify({"error": "'code' dictionary cannot be empty"}), 400
        
        # Validate query
        if not isinstance(query, str) or not query.strip():
            return jsonify({"error": "'query' must be a non-empty string"}), 400
        
        # Validate project_id if provided
        if project_id is not None and (not isinstance(project_id, str) or not project_id.strip()):
            return jsonify({"error": "'project_id' must be a non-empty string if provided"}), 400
        
        # Run verification
        results = verifier_service.verify_project(code_dict, query, language, project_id, agent_type=agent_type, keep_existing_project=keep_existing_project, evaluator_config=evaluator_config)
        
        return jsonify(results)
        
    except Exception as e:
        app.logger.error(f"Error in verify endpoint: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "traceback": traceback.format_exc()
        }), 500

@app.route('/verify-project', methods=['POST'])
def verify_project():
    """
    Alternative endpoint for project verification with more detailed options.
    
    Expected JSON payload:
    {
        "files": {"path/to/file.js": "content", "package.json": "{...}"},
        "query": "Create a simple React app with counter",
        "language": "react",  // optional
        "project_id": "my-project-123",  // optional
        "options": {  // optional
            "skip_running": false,
            "skip_aesthetics": false,
            "skip_functional": false
        }
    }
    """
    try:
        # Parse request JSON
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Validate required fields
        if 'files' not in data:
            return jsonify({"error": "Missing 'files' field"}), 400
        
        if 'query' not in data:
            return jsonify({"error": "Missing 'query' field"}), 400
        
        code_dict = data['files']
        query = data['query']
        language = data.get('language')
        project_id = data.get('project_id')
        agent_type = data.get('agent_type', 'static')
        keep_existing_project = data.get('keep_existing_project', False)
        evaluator_config = data.get('evaluator_config', None)
        options = data.get('options', {})

        if agent_type not in VALID_AGENT_TYPES:
            return jsonify({"error": f"agent_type must be one of: {', '.join(VALID_AGENT_TYPES)}"}), 400
        
        # Validate code dictionary
        if not isinstance(code_dict, dict):
            return jsonify({"error": "'files' must be a dictionary"}), 400
        
        if not code_dict:
            return jsonify({"error": "'files' dictionary cannot be empty"}), 400
        
        # Run verification
        results = verifier_service.verify_project(
            code_dict=code_dict,
            query=query,
            language=language,
            project_id=project_id,
            agent_type=agent_type,
            keep_existing_project=keep_existing_project,
            evaluator_config=evaluator_config
        )
        
        return jsonify(results)

    except Exception as e:
        app.logger.error(f"Error in verify-project endpoint: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "traceback": traceback.format_exc()
        }), 500

@app.route('/verify-scaffold', methods=['POST'])
def verify_scaffold():
    """
    Verify a scaffold project by parsing tool_calls from LLM response.

    This endpoint accepts a response containing <tool_call>...</tool_call> blocks,
    parses them, executes the tools to build a React/Vite/Tailwind project,
    and runs evaluation on the built project.

    Expected JSON payload:
    {
        "response": "<tool_call>{\"name\":\"create_file\",...}</tool_call>...",
        "query": "Create a counter app",
        "project_id": "my-project-123",  // optional, generates UUID if not provided
        "agent_type": "static"  // optional, defaults to "static"
    }

    Returns:
    {
        "project_id": "my-project-123",
        "project_path": "/path/to/verifier/workspace/my-project-123",
        "evaluations": {
            "installation": {...},
            "running": {...},
            "aesthetics": {...},
            "functional": {...}
        }
    }
    """
    try:
        # Parse request JSON
        data = request.get_json()

        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        # Validate required fields
        if 'response' not in data:
            return jsonify({"error": "Missing 'response' field"}), 400

        if 'query' not in data:
            return jsonify({"error": "Missing 'query' field"}), 400

        response = data['response']
        query = data['query']
        project_id = data.get('project_id')
        agent_type = data.get('agent_type', 'static')
        evaluator_config = data.get('evaluator_config', None)

        if agent_type not in VALID_AGENT_TYPES:
            return jsonify({"error": f"agent_type must be one of: {', '.join(VALID_AGENT_TYPES)}"}), 400
        if not isinstance(response, str) or not response.strip():
            return jsonify({"error": "'response' must be a non-empty string"}), 400

        # Validate query
        if not isinstance(query, str) or not query.strip():
            return jsonify({"error": "'query' must be a non-empty string"}), 400

        # Run scaffold verification
        results = verifier_service.verify_scaffold_project(
            response=response,
            query=query,
            project_id=project_id,
            agent_type=agent_type,
            evaluator_config=evaluator_config
        )

        return jsonify(results)

    except Exception as e:
        app.logger.error(f"Error in verify-scaffold endpoint: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "traceback": traceback.format_exc()
        }), 500

@app.route('/projects', methods=['GET'])
def list_projects():
    """
    List all projects in the workspace.
    
    Returns:
    {
        "workspace_path": "/path/to/verifier/workspace",
        "project_count": 2,
        "projects": [
            {
                "project_id": "abc-123",
                "project_path": "/path/to/verifier/workspace/abc-123",
                "file_count": 5,
                "modified": 1234567890
            }
        ]
    }
    """
    return jsonify(verifier_service.list_projects())

@app.route('/projects/<project_id>', methods=['GET'])
def get_project_info(project_id):
    """
    Get information about a specific project.
    
    Returns:
    {
        "project_id": "abc-123",
        "project_path": "/path/to/verifier/workspace/abc-123",
        "exists": true,
        "files": [
            {
                "path": "package.json",
                "size": 1024,
                "modified": 1234567890
            }
        ],
        "file_count": 1
    }
    """
    return jsonify(verifier_service.get_project_info(project_id))

@app.route('/projects/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    """
    Delete a specific project from the workspace.
    
    Returns:
    {
        "project_id": "abc-123",
        "deleted": true,
        "message": "Project abc-123 deleted successfully"
    }
    """
    return jsonify(verifier_service.delete_project(project_id))

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # Development server
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    # Setup cleanup on exit
    import atexit
    import signal
    
    def signal_handler(signum, frame):
        print(f"\nReceived signal {signum}. Shutting down...")
        # Don't do global playwright cleanup here - it would break other running verifiers
        # Each verifier instance cleans up its own processes automatically
        exit(1)
    
    # No cleanup functions needed - each verifier handles its own cleanup
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print(f"Starting Verifier Flask server on port {port}")
    print(f"Debug mode: {debug}")
    print("\nAvailable endpoints:")
    print("  GET  /health - Health check")
    print("  POST /verify - Main verification endpoint")
    print("  POST /verify-project - Alternative verification endpoint")
    print("  GET  /projects - List all projects in workspace")
    print("  GET  /projects/<id> - Get project information")
    print("  DELETE /projects/<id> - Delete a project")
    
    app.run(host='0.0.0.0', port=port, debug=debug) 