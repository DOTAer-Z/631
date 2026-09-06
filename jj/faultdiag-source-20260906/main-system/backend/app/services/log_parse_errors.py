class LogParseCancelled(RuntimeError):
    """Raised when a user cancellation must stop the active parse."""


class LogParseTimedOut(RuntimeError):
    """Raised when a parse exceeds its wall-clock deadline."""
