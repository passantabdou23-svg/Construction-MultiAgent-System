"""Compatibility import for earlier project versions.

The production implementation moved to :mod:`design_agent`. Automated tests
now live under ``tests/`` so unittest discovery is no longer confused by this
historical filename.
"""

from design_agent import DesignAgentError, LocalLLMDesignAgent

__all__ = ["DesignAgentError", "LocalLLMDesignAgent"]
