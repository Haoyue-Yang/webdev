# verifier/__main__.py

import argparse
import json
from .verifier import Verifier

def main():
    """
    Main entry point for running the verifier from the command line.
    """
    parser = argparse.ArgumentParser(description="Test and evaluate a code project.")
    parser.add_argument(
        "project_dir",
        type=str,
        help="The path to the project directory to test."
    )
    parser.add_argument(
        "--language",
        type=str,
        default="react",
        help="The programming language of the project."
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="The user query or prompt that describes the project's goal."
    )
    parser.add_argument(
        "--config",
        type=str,
        default="conf/config.yaml",
        help="Path to the verifier configuration file."
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Keep existing project files. By default, existing project directory is removed to ensure clean state."
    )

    args = parser.parse_args()

    verifier = Verifier(config_path=args.config)
    results = verifier.test_project(
        project_dir=args.project_dir,
        language=args.language,
        query=args.query,
        keep_existing_project=getattr(args, 'keep_existing', False)
    )

    print("\n--- Final Results ---")
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main() 