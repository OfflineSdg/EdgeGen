from typing import Dict, Any


def get_config_or_default(config: Dict[str, Any], config_key: str, default: Any):
    if config and config_key in config:
        return config[config_key]
    return default