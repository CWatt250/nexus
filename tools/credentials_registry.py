"""
tools/credentials_registry.py — re-export from core.credentials_registry.

The canonical registry lives in core/ so other tools can import it without
depending on the tools/ package. This re-export satisfies the Phase 33 spec
requirement for tools/credentials_registry.py.
"""
from core.credentials_registry import registry, ServiceDef, ValidationMethod

__all__ = ["registry", "ServiceDef", "ValidationMethod"]
