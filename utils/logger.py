"""
utils/logger.py
=================
Sets up structured logging with:
  - Console handler (INFO level, coloured output)
  - File handler (DEBUG level, JSON lines for audit trail)
  - Separate audit_log() function for key events

Audit log entries look like:
  {"ts": "2025-...", "event": "tool_call", "data": {"tool": "run_terraform", ...}}
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON for machine-readable audit files."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


class ColorConsoleFormatter(logging.Formatter):
    """Adds ANSI colour codes to console output by log level."""

    LEVEL_COLORS = {
        "DEBUG":    "\033[36m",    # cyan
        "INFO":     "\033[32m",    # green
        "WARNING":  "\033[33m",    # yellow
        "ERROR":    "\033[31m",    # red
        "CRITICAL": "\033[35m",    # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logger(name: str, log_file: str = "logs/chatbot.log") -> logging.Logger:
    """
    Create and configure a logger with console + file handlers.

    Args:
        name:     Logger name (usually the application name).
        log_file: Path to the JSON-lines audit log file.

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # ── Console handler ─────────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        ColorConsoleFormatter(
            fmt="%(asctime)s  %(levelname)s  %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    # ── File handler ─────────────────────────────────────────────────────────────
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


def audit_log(logger: logging.Logger, event: str, data: dict):
    """
    Write a structured audit trail entry at DEBUG level.

    These entries capture key system events (tool calls, terraform runs,
    user inputs) for compliance and debugging purposes.

    Args:
        logger: The application logger.
        event:  Short event identifier (e.g., "tool_call", "terraform_apply").
        data:   Arbitrary structured data associated with the event.
    """
    payload = {"AUDIT": True, "event": event, "data": data}
    logger.debug(json.dumps(payload))
