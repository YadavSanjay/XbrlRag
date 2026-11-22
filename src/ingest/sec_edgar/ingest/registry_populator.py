# Backward-compatibility shim.
# The class was renamed to Layer0Populator in layer0_populator.py.
from src.ingest.sec_edgar.ingest.layer0_populator import (
    Layer0Populator as RegistryPopulator,  # noqa: F401
)

__all__ = ["RegistryPopulator"]
