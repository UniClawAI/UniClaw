class IlinkBotError(Exception):
    """Base error for this SDK."""


class ApiError(IlinkBotError):
    def __init__(
        self, message: str, *, code: int | None = None, payload: dict | None = None
    ):
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


class AuthError(ApiError):
    """Raised when login credentials are missing or expired."""


class NoContextError(IlinkBotError):
    """Raised when sending without a cached context_token."""


class MediaError(IlinkBotError):
    """Raised for media encryption or upload failures."""
