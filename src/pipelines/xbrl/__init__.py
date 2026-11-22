import yaml

from src import CONFIGS_DIR

XBRL_PIPELINES_CONFIG_DIR = CONFIGS_DIR / "pipelines" / "xbrl"


def load_config(config_name: str = "load_to_vectordb", config_key: str = None):
    """Load a YAML config from ``configs/pipelines/xbrl/<config_name>.yaml``.

    Mirrors ``src.pipelines.sec10k.load_config``, generalised with a
    ``config_name`` so multiple XBRL pipeline configs can live side by side
    (e.g. ``load_to_vectordb.yaml``).

    ``config_key`` is a dot-path into the loaded YAML (e.g. ``"qdrant.host"``);
    omit to get the whole document.
    """
    config_path = XBRL_PIPELINES_CONFIG_DIR / f"{config_name}.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    if config_key is None:
        return config
    current = config
    for key in str(config_key).split("."):
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current
