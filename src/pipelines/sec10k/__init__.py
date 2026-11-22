


import yaml

from src import BASE_DATA_DIR, CONFIGS_DIR


def load_config(config_key: str = None):
    config_path = CONFIGS_DIR / 'pipelines' / 'sec10k_data_to_process.yaml'
    with open(config_path) as f:
        config = yaml.safe_load(f)
    if config_key is None:
        return config
    current = config
    for key in str(config_key).split('.'):
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current
