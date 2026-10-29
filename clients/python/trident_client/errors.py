"""Errors raised by the TRIDENT client."""
from __future__ import annotations

from typing import Any, Optional


class TridentError(Exception):
    """Base class for all client errors; carries the HTTP status and server payload when available."""

    def __init__(self, message: str, status: Optional[int] = None, payload: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class AuthenticationError(TridentError):
    """The token is missing, invalid, expired or revoked (HTTP 401)."""


class PermissionDenied(TridentError):
    """The token's account lacks the permission required for this call (HTTP 403)."""


class VehicleNotFound(TridentError):
    """The server does not know this vehicle (HTTP 404)."""


class VehicleNotConnected(TridentError):
    """The vehicle is known but its bridge is not connected, so the command was not delivered (HTTP 409)."""


class InvalidCommand(TridentError):
    """The server rejected the command as malformed (HTTP 400)."""
