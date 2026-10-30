"""BUMM Guard: static analysis for NEAR smart contracts."""

__version__ = "0.3.0"

from .model import Finding  # noqa: E402,F401

__all__ = ["Finding", "__version__"]
