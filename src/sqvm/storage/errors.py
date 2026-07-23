"""Typed failures for the storage foundation layer."""

from __future__ import annotations


class StorageError(ValueError):
    """Base class for a fail-closed storage operation error."""


class StorageValidationError(StorageError):
    """A policy payload or its canonical identity is invalid."""


class StoragePolicyError(StorageValidationError):
    """A policy source cannot be read as a strict storage policy."""


class StorageInventoryError(StorageError):
    """A filesystem tree cannot be safely inventoried without following links."""


class ArchiveFormatError(StorageError):
    """An .sqrun archive violates the deterministic v1 evidence format."""
