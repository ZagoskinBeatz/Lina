"""
Lina — Desktop Integration Installer.

Installs/uninstalls:
  - .desktop file → app menu (KDE/GNOME)
  - autostart entry → start on login
  - global hotkeys → Meta+L to open Lina
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_DESKTOP_FILE = _DATA_DIR / "lina.desktop"
_AUTOSTART_FILE = _DATA_DIR / "lina-autostart.desktop"


def install_desktop_entry() -> bool:
    """Install .desktop file to user applications directory."""
    dest_dir = Path.home() / ".local" / "share" / "applications"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "lina.desktop"
    try:
        shutil.copy2(_DESKTOP_FILE, dest)
        # Update desktop database
        subprocess.run(
            ["update-desktop-database", str(dest_dir)],
            capture_output=True, timeout=10,
        )
        logger.info("Desktop entry installed: %s", dest)
        return True
    except Exception as e:
        logger.error("Failed to install desktop entry: %s", e)
        return False


def uninstall_desktop_entry() -> bool:
    """Remove .desktop file from user applications directory."""
    dest = Path.home() / ".local" / "share" / "applications" / "lina.desktop"
    try:
        dest.unlink(missing_ok=True)
        logger.info("Desktop entry removed")
        return True
    except Exception as e:
        logger.error("Failed to remove desktop entry: %s", e)
        return False


def install_autostart(enabled: bool = True) -> bool:
    """Install/update autostart entry."""
    dest_dir = Path.home() / ".config" / "autostart"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "lina-autostart.desktop"
    try:
        shutil.copy2(_AUTOSTART_FILE, dest)
        if not enabled:
            # Write Hidden=true to disable
            text = dest.read_text()
            text = text.replace("Hidden=false", "Hidden=true")
            dest.write_text(text)
        logger.info("Autostart installed (enabled=%s): %s", enabled, dest)
        return True
    except Exception as e:
        logger.error("Failed to install autostart: %s", e)
        return False


def uninstall_autostart() -> bool:
    """Remove autostart entry."""
    dest = Path.home() / ".config" / "autostart" / "lina-autostart.desktop"
    try:
        dest.unlink(missing_ok=True)
        logger.info("Autostart removed")
        return True
    except Exception as e:
        logger.error("Failed to remove autostart: %s", e)
        return False


def install_all(autostart: bool = False) -> dict:
    """Install everything: desktop entry + optional autostart.

    Returns:
        {"desktop": bool, "autostart": bool}
    """
    return {
        "desktop": install_desktop_entry(),
        "autostart": install_autostart(enabled=autostart) if autostart else True,
    }


def uninstall_all() -> dict:
    """Remove all desktop integration files."""
    return {
        "desktop": uninstall_desktop_entry(),
        "autostart": uninstall_autostart(),
    }


def is_installed() -> dict:
    """Check what's currently installed."""
    desktop = (Path.home() / ".local" / "share" / "applications" / "lina.desktop").exists()
    autostart = (Path.home() / ".config" / "autostart" / "lina-autostart.desktop").exists()
    return {"desktop": desktop, "autostart": autostart}
