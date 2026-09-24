"""Storage adapters.

The raw layer keeps a clean boundary so it can sit behind its port without
rewriting callers; the dataset adapter (#39) is the ``StoragePort`` driver.
"""

from .datasets import MongoStorageAdapter
from .raw import (
    PROVIDER_GITHUB,
    MongoRawStore,
    RawDocument,
    RawStore,
    is_fresh,
    raw_params_hash,
)

__all__ = [
    "PROVIDER_GITHUB",
    "MongoRawStore",
    "MongoStorageAdapter",
    "RawDocument",
    "RawStore",
    "is_fresh",
    "raw_params_hash",
]
