"""Nightly SQLite online backup using the backup API (never a file copy).

Keeps the 14 newest backups in data/backups/, and optionally rsyncs each
backup off the server when BACKUP_RSYNC_TARGET is set. Media and NiceGUI
storage are deliberately excluded: the import CLI re-downloads media, and lost
storage only logs people out.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from config import settings

log = logging.getLogger("gym_tracker.backup")

KEEP_BACKUPS = 14


def run_backup() -> Path:
    db_path = Path(settings.database_path).resolve()
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"gym-{timestamp}.db"

    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)  # online backup API
        finally:
            destination.close()
    finally:
        source.close()
    log.info("Backup written: %s (%d bytes)", backup_path, backup_path.stat().st_size)

    _prune(backup_dir)
    _rsync_offsite(backup_path)
    return backup_path


def _prune(backup_dir: Path) -> None:
    backups = sorted(backup_dir.glob("gym-*.db"))
    for old in backups[:-KEEP_BACKUPS]:
        old.unlink()
        log.info("Pruned old backup: %s", old.name)


def _rsync_offsite(backup_path: Path) -> None:
    target = getattr(settings, "backup_rsync_target", None)
    if not target:
        return
    result = subprocess.run(
        ["rsync", "-a", str(backup_path), target],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        log.info("Backup copied off-server to %s", target)
    else:
        log.error("Off-server rsync failed: %s", result.stderr.strip())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    run_backup()
