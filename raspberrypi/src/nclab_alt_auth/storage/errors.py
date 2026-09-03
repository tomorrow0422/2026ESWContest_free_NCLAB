"""Errors shared by persistent storage adapters."""


class StorageError(RuntimeError):
    """Raised when persistent data cannot be handled safely."""
