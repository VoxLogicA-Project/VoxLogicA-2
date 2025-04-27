"""
VoxLogicA Error Message module - Python implementation
"""

import sys
import time
import logging
from typing import List, Tuple, Optional, Callable
from io import StringIO
from dataclasses import dataclass, field


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
    """Logger class for VoxLogicA"""

    _log_levels: set = set()
    _start_time: float = time.time()
    _destinations: List[Callable[[str], None]] = []

    @classmethod
    def set_log_level(cls, levels: List[str]) -> None:
        """Set the log levels that should be displayed"""
        cls._log_levels.clear()
        for level in levels:
            cls._log_levels.add(level)

    @classmethod
    def log_to_stdout(cls) -> None:
        """Add stdout as a log destination"""
        cls._destinations.append(lambda s: print(s, file=sys.stdout, flush=True))

    @classmethod
    def log_to_memory(cls) -> Callable[[], str]:
        """
        Add an in-memory string buffer as a log destination

        Returns:
            A function that, when called, returns all log content
        """
        string_io = StringIO()

        def write_to_memory(s: str) -> None:
            string_io.write(s + "\n")

        def read_from_memory() -> str:
            string_io.seek(0)
            return string_io.read()

        cls._destinations.append(write_to_memory)
        return read_from_memory

    @classmethod
    def _print(cls, prefix: str, message: str) -> None:
        """Print a log message with the given prefix"""
        elapsed_ms = int((time.time() - cls._start_time) * 1000)
        formatted_msg = message.replace("\n", "\n                      ")
        log_line = f"[{elapsed_ms:10d}ms] [{prefix}] {formatted_msg}"

        if not cls._log_levels or prefix in cls._log_levels:
            for dest in cls._destinations:
                dest(log_line)

    @classmethod
    def debug(cls, message: str) -> None:
        """Log a debug message"""
        if __debug__:
            cls._print("dbug", message)

    @classmethod
    def test(cls, level: str, message: str) -> None:
        """Log a test message with a specific level"""
        if __debug__:
            cls._print(level, message)

    @classmethod
    def warning(cls, message: str) -> None:
        """Log a warning message"""
        cls._print("warn", message)

    @classmethod
    def failure(cls, message: str) -> None:
        """Log a failure message"""
        cls._print("fail", message)

    @classmethod
    def info(cls, message: str) -> None:
        """Log an info message"""
        cls._print("info", message)

    @classmethod
    def result(cls, name: str, value) -> None:
        """Log a result"""
        cls._print("user", f"{name}={value}")

    @classmethod
    def debug_exception(cls, exception: Exception) -> None:
        """Log an exception"""
        if __debug__:
            cls.failure(str(exception))
        else:
            cls.failure(exception.args[0] if exception.args else str(exception))
