import yaml
from pathlib import Path

def load_config(config_path: Path = Path('conf/config.yaml')) -> dict:
    """
    Loads the YAML configuration file.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found at: {config_path.resolve()}")
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f) 