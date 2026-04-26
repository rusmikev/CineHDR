# save-session.py
#
# Copyright 2026 Diego Povliuk
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import gi
from .utils import LAST_PLAYLIST_FILE

gi.require_version("GLib", "2.0")
from gi.repository import GLib


def save_last_playlist_file(win_mpv):
    """Saves the current playlist to a m3u8 file."""

    try:
        win_mpv["save-position-on-quit"] = True
        with open(LAST_PLAYLIST_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for item in win_mpv.playlist:
                filename = item.get("filename")
                f.write(f"{filename}\n")
    except Exception as e:
        print(f"Error saving last playlist file: {e}")


def restore_last_playlist(window, app, win_mpv):
    """Restore the last playlist if its the first window."""

    if len(app.get_windows()) > 1:
        return

    if os.path.exists(LAST_PLAYLIST_FILE):
        window.start_page.set_sensitive(False)
        GLib.idle_add(win_mpv.loadfile, LAST_PLAYLIST_FILE, "replace")
