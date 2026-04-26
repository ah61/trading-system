"""
src/exceptions.py
Custom exception hierarchy for the trading system.
All exceptions inherit from TradingSystemError for easy catch-all handling.
"""


class TradingSystemError(Exception):
    """Base exception for all trading system errors."""
    pass


class DataFetchError(TradingSystemError):
    """Raised when a data source fails to return data."""
    pass


class DataGapError(TradingSystemError):
    """Raised when missing data exceeds the allowable fill threshold."""
    pass


class DataValidationError(TradingSystemError):
    """Raised when data fails schema or content validation."""
    pass


class LookaheadError(TradingSystemError):
    """Raised when a lookahead bias violation is detected."""
    pass


class SignalComputationError(TradingSystemError):
    """Raised when signal computation fails."""
    pass


class InsufficientDataError(TradingSystemError):
    """Raised when there is insufficient data for a computation."""
    pass


class ConfigError(TradingSystemError):
    """Raised when configuration is missing or invalid."""
    pass


class StorageError(TradingSystemError):
    """Raised when data storage operations fail."""
    pass
