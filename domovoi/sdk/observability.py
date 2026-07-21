"""Per-plugin loggers (design §4.14).

``get_logger(slug)`` returns the ``"plugin.<slug>"`` child logger. It
propagates into the core log AND tees into a per-plugin rotating file
``~/.domovoi/logs/plugin_<slug>.log`` (5 MB × 3) which the plugin
detail page tails. Per-plugin level override: the core setting/env var
``LOG_LEVEL_PLUGIN_<SLUG>`` (hot tier — re-read on each get_logger
call), so one chatty plugin can go DEBUG without drowning the core log.

Idempotent: repeated calls never stack handlers.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path.home() / ".domovoi" / "logs"
_FILE_MARKER = "_domovoi_plugin_file_handler"


def get_logger(slug: str) -> logging.Logger:
    logger = logging.getLogger(f"plugin.{slug}")

    level_env = os.environ.get(f"LOG_LEVEL_PLUGIN_{slug.upper()}")
    if level_env:
        logger.setLevel(level_env.upper())

    if not any(getattr(h, _FILE_MARKER, False) for h in logger.handlers):
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                _LOG_DIR / f"plugin_{slug}.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            setattr(handler, _FILE_MARKER, True)
            logger.addHandler(handler)
        except OSError:
            # A read-only home dir must never block a plugin from logging
            # to the core log (propagation stays on).
            pass
    return logger
