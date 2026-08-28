"""Project logging: console + logs/automation.log."""
from __future__ import annotations

import logging
from pathlib import Path

_CONFIGURED = False


def get_logger(name: str, log_dir: Path, debug: bool = True) -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        log_dir.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
        root = logging.getLogger()
        root.setLevel(logging.DEBUG if debug else logging.INFO)
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        sh.setLevel(logging.INFO)
        fh = logging.FileHandler(log_dir / "automation.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(sh)
        root.addHandler(fh)
        # keep third-party noise down
        for noisy in ("selenium", "urllib3", "seleniumbase"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        _CONFIGURED = True
    return logging.getLogger(name)
