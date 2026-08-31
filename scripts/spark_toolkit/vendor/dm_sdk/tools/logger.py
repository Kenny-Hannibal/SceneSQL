#!/usr/bin/env python3
"""
A wrapper of the logging.getLogger with some default setting:

Set logs to print both to a daily rolling file and console.

logs are formatted into
"[dm_sdk] LEVEL - filename:lineno - funcName() - message"
"""

import logging
import os
from logging import FileHandler, Logger
from typing import Dict

loggers: Dict[str, Logger] = {}
DEFAULT_LOG_FOLDER = "var/log"


def get_logger(name, level=logging.INFO, with_file_handler=False) -> Logger:
    """This method is not thread safe"""
    if name in loggers:
        return loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(level)

    loggers[name] = logger
    return logger


def add_handler(name: str, fh: FileHandler = None):
    if not loggers.get(name):
        return

    if fh:
        loggers[name].addHandler(fh)


def make_file_handler(custom_log_file: str) -> FileHandler:
    log_file_path = os.path.dirname(custom_log_file)
    if not os.path.exists(log_file_path):
        os.makedirs(log_file_path)
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )
    fh = logging.FileHandler(custom_log_file)
    fh.setFormatter(formatter)
    return fh


def remove_handler(name: str, fh: FileHandler):
    if not loggers.get(name):
        return
    loggers[name].removeHandler(fh)


def remove_logger(name: str):
    loggers.pop(name, None)


def enable_logging(level: int = logging.INFO):
    sdk_logger = logging.getLogger("dm_sdk")

    if sdk_logger.handlers:
        return  # 避免重复添加

    formatter = logging.Formatter(
        fmt="[dm_sdk] %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    sdk_logger.addHandler(handler)

    sdk_logger.setLevel(level)
