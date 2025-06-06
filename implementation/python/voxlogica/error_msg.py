"""
VoxLogicA Error Message module - Python implementation
"""

import logging
from typing import List, Tuple, Optional, Any, TypeVar
from dataclasses import dataclass, field

# Type variable for generic types
T = TypeVar('T')


# Exception class for VoxLogicA
class VLException(Exception):
    """VoxLogicA specific exception with stack trace support"""

    def __init__(self, msg: str, stack_trace: Optional[List[Tuple[str, str]]] = None):
        self.msg = msg
        self.stack_trace = stack_trace or []
        super().__init__(self.format_message())

    def format_message(self) -> str:
        if not self.stack_trace:
            return self.msg

        trace_str = ""
        for identifier, position in self.stack_trace:
            trace_str += f"\n{identifier} at {position}"

        return f"{self.msg}{trace_str}"


# Type alias for stack trace
Stack = List[Tuple[str, str]]


def fail(msg: str) -> None:
    """Raise a VoxLogicA exception with a message"""
    raise VLException(msg, [])


def fail_with_stacktrace(msg: str, stack_trace: Stack) -> None:
    """Raise a VoxLogicA exception with a message and stack trace"""
    raise VLException(msg, stack_trace)


@dataclass
class Report:
    """Report class for tracking print and save operations"""

    _print_items: List[Tuple[str, str, str]] = field(default_factory=list)
    _save_items: List[Tuple[str, str, float, float, str]] = field(default_factory=list)

    def print(self, name: str, type_: str, result: str) -> None:
        """Record a print operation"""
        self._print_items.append((name, type_, result))

    def save(
        self, name: str, type_: str, min_val: float, max_val: float, path: str
    ) -> None:
        """Record a save operation"""
        self._save_items.append((name, type_, min_val, max_val, path))

    def get(
        self,
    ) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, float, float, str]]]:
        """Get all recorded operations"""
        return (self._print_items.copy(), self._save_items.copy())


# Singleton instance of Report
report = Report()


class Logger:
    """Logger class for VoxLogicA with support for multiple log levels and destinations"""
    _instance: Optional['Logger'] = None
    _logger: logging.Logger
    _initialized: bool = False

    def __new__(cls) -> 'Logger':
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not self._initialized:
            self._logger = logging.getLogger('voxlogica')
            self._logger.setLevel(logging.INFO)
            
            # Create console handler with a higher log level
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            
            # Create formatter and add it to the handlers
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            ch.setFormatter(formatter)
            
            # Add the handler to the logger
            self._logger.addHandler(ch)
            self._initialized = True
    
    @classmethod
    def get_logger(cls, name: Optional[str] = None) -> logging.Logger:
        """Get a logger instance with the given name"""
        if not hasattr(cls, '_instance') or cls._instance is None:
            cls._instance = cls()
        return logging.getLogger(name) if name else cls._instance._logger
    
    @classmethod
    def set_level(cls, level: int) -> None:
        """Set the logging level"""
        logger = cls.get_logger()
        logger.setLevel(level)
        for handler in logger.handlers:
            handler.setLevel(level)
    
    @classmethod
    def debug(cls, message: str, **kwargs: Any) -> None:
        """Log a debug message"""
        logger = cls.get_logger()
        if kwargs:
            logger.debug(f"{message} - {kwargs}")
        else:
            logger.debug(message)
    
    @classmethod
    def info(cls, message: str, **kwargs: Any) -> None:
        """Log an info message"""
        logger = cls.get_logger()
        if kwargs:
            logger.info(f"{message} - {kwargs}")
        else:
            logger.info(message)
    
    @classmethod
    def warning(cls, message: str, **kwargs: Any) -> None:
        """Log a warning message"""
        logger = cls.get_logger()
        if kwargs:
            logger.warning(f"{message} - {kwargs}")
        else:
            logger.warning(message)
    
    @classmethod
    def error(cls, message: str, **kwargs: Any) -> None:
        """Log an error message"""
        logger = cls.get_logger()
        if kwargs:
            logger.error(f"{message} - {kwargs}")
        else:
            logger.error(message)
    
    @classmethod
    def critical(cls, message: str, **kwargs: Any) -> None:
        """Log a critical message"""
        logger = cls.get_logger()
        if kwargs:
            logger.critical(f"{message} - {kwargs}")
        else:
            logger.critical(message)
    
    @classmethod
    def exception(cls, message: str, exc_info: bool = True, **kwargs: Any) -> None:
        """Log an exception with stack trace"""
        logger = cls.get_logger()
        if kwargs:
            logger.exception(f"{message} - {kwargs}", exc_info=exc_info)
        else:
            logger.exception(message, exc_info=exc_info)
    
    @classmethod
    def debug_exception(cls, exception: Exception) -> None:
        """Log a debug exception with stack trace"""
        logger = cls.get_logger()
        if __debug__:
            logger.exception("Debug exception", exc_info=exception)
        else:
            logger.error(str(exception))
