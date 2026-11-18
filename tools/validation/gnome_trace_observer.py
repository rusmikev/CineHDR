#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Child entry point: close only this native test application's windows normally."""
import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

import logging
import os
from pathlib import Path
import runpy
import sys
import time

logger = logging.getLogger(__name__)


def run_native_observation(video_path, duration):
    started = None
    close_requested = False

    def poll():
        nonlocal started, close_requested
        app = Gio.Application.get_default()
        windows = app.get_windows() if app is not None else []
        if not windows:
            return GLib.SOURCE_CONTINUE
        if started is None:
            started = time.monotonic()
        if time.monotonic() - started < duration:
            return GLib.SOURCE_CONTINUE
        # Gtk.Window.close() follows the normal close-request teardown, unlike SIGINT.
        close_requested = True
        for window in windows:
            window.close()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(100, poll)
    launcher = Path(__file__).resolve().parents[2] / "run_dev.py"
    sys.argv = [str(launcher), str(video_path)]
    try:
        runpy.run_path(str(launcher), run_name="__main__")
    except SystemExit as error:
        if error.code not in (None, 0):
            raise
    if not close_requested:
        logger.error("Application exited before the bounded observation completed")
        return 1
    return 0


if __name__ == "__main__":
    if os.environ.get("GSETTINGS_BACKEND") != "memory":
        raise RuntimeError("Use the isolated GNOME trace runner")
    sys.exit(run_native_observation(Path(sys.argv[1]), float(sys.argv[2])))
