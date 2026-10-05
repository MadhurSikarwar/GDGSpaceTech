"""
OrbitalGuard Tool Layer Exceptions.
Standardized domain exceptions for tool invocations.
"""


class ToolError(Exception):
    """Base exception for all OrbitalGuard tool layer errors."""
    pass


class ObjectNotFoundError(ToolError):
    """Raised when a requested space object is not found in the catalog or database."""
    pass


class ConjunctionNotFoundError(ToolError):
    """Raised when a specified conjunction candidate cannot be found."""
    pass


class ComputationError(ToolError):
    """Raised when a deterministic astrodynamic or physical calculation cannot be completed."""
    pass


class ConstraintViolationError(ToolError):
    """Raised when a safety or operational constraint is strictly violated."""
    pass


class DataSourceUnavailableError(ToolError):
    """Raised when an external data source or service is completely unreachable and no fallback exists."""
    pass
