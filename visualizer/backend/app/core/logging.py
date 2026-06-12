import logging
import sys
from logging.handlers import RotatingFileHandler
from app.core.config import settings


def setup_logging() -> None:
    """Configure structured logging for the application.

    Logs are written to both stdout and a rotating log file under LOG_DIR.
    """
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    formatter = logging.Formatter(log_format)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # File handler: rotating by size, kept for LOG_FILE_BACKUP_COUNT days/backups
    try:
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = settings.LOG_DIR / "app.log"
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=settings.LOG_FILE_MAX_BYTES,
            backupCount=settings.LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except Exception as exc:
        # If file logging cannot be set up, fall back to stdout only
        logging.warning("Failed to setup file logging: %s", exc)

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format=log_format,
        handlers=handlers,
        force=True,
    )
    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
