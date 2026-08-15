from __future__ import annotations


class GrapeError(Exception):
    """Base error for grape."""


class GraderDefinitionError(GrapeError):
    """Raised when the grader definition cannot compile."""


class CapabilityNotAvailableError(GraderDefinitionError):
    """Raised when a requested capability is not implemented."""
