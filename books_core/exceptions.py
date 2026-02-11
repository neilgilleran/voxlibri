"""
Custom exceptions for AI Reading Guide feature.
"""

from decimal import Decimal


class LimitExceededException(Exception):
    """
    Raised when an operation would exceed configured spending or usage limits.
    """
    def __init__(self, limit_type: str, current: Decimal, limit: Decimal, message: str = None):
        self.limit_type = limit_type
        self.current = current
        self.limit = limit
        if message is None:
            message = (
                f"{limit_type.capitalize()} limit exceeded: "
                f"current {current} would exceed limit of {limit}. "
                f"Please visit Settings to increase your limits."
            )
        super().__init__(message)


class CostEstimationException(Exception):
    """
    Raised when cost estimation fails.
    """
    pass


class EmergencyStopException(Exception):
    """
    Raised when AI features are disabled via emergency stop setting.
    """
    def __init__(self, message: str = "AI features are currently disabled. Visit Settings to enable."):
        super().__init__(message)
