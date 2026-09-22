# main.py
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

import logging
import os
import subprocess
import sys
from gettext import gettext as _
from typing import cast

import gi

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk, Gdk

from .mpris import MPRIS
from .preferences import Preferences, settings
from .render_backend import select_process_backend
from .save_session import is_same_playlist
from .window import CineWindow

logger = logging.getLogger(__name__)

# Force the modern GL renderer if the user hasn't overridden it. GTK's Vulkan
# renderer has known issues with 16-bit float textures and PQ color states
# on some drivers (e.g., radv). 'ngl' is fully color-managed.
if "GSK_RENDERER" not in os.environ:
    os.environ["GSK_RENDERER"] = "ngl"

# Set the icon shown in gnome sound settings
os.environ["PIPEWIRE_PROPS"] = '{application.icon-name="io.github.rusmikev.CineHDR"}'


class CineApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self):
        self.gpu_validation_config = None
        application_flags = Gio.ApplicationFlags.HANDLES_OPEN
        if os.environ.get("CINEHDR_NON_UNIQUE", "").strip():
            application_flags |= Gio.ApplicationFlags.NON_UNIQUE
        if os.environ.get("CINEHDR_GPU_VALIDATION", "").strip():
            from .gpu_validation import validation_config_from_env

            self.gpu_validation_config = validation_config_from_env(os.environ)
            # A validation run must not be forwarded to an already-open normal
            # CineHDR process: its renderer is immutable for that process.
            application_flags |= Gio.ApplicationFlags.NON_UNIQUE

        super().__init__(
            application_id="io.github.rusmikev.CineHDR",
            flags=application_flags,
            resource_base_path="/io/github/rusmikev/CineHDR",
        )

        self.add_main_option(
            "new-window",
            ord("n"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Open a new window",
            None,
        )

        self.render_backend_selection = select_process_backend(
            settings.get_string("render-backend"), os.environ
        )
        self._render_fallback_toast_shown = False
        self._gpu_validation_session = None
        self._gpu_validation_setup_failures = ()
        logger.info(
            "Process video renderer configured=%s requested=%s source=%s "
            "creation_fallback=%s",
            self.render_backend_selection.configured.value,
            self.render_backend_selection.requested.value,
            self.render_backend_selection.source.value,
            self.render_backend_selection.allow_creation_fallback,
        )

        self.connect("shutdown", self._on_shutdown)

    def notify_render_fallback_once(self, window, reason: str):
        """Show at most one fallback notification for the whole process."""
        if self._render_fallback_toast_shown:
            return
        self._render_fallback_toast_shown = True
        logger.warning("GPU Next startup fallback: %s", reason)
        window.show_toast(
            _("GPU Next is unavailable. Using the standard renderer."),
            True,
        )

    def do_startup(self):
        self.mpris = MPRIS(self)

        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().props.color_scheme = Adw.ColorScheme.FORCE_DARK

        self._create_action("new-window", lambda *a: self.activate(), ["<primary>n"])
        self._create_action("quit", lambda *a: self.quit(), ["<primary>q"])
        self._create_action("about", self._on_about_action)
        self._create_action(
            "preferences", self.on_preferences_action, ["<primary>comma"]
        )

    def do_activate(self):
        win = CineWindow(application=self, is_activate=True)
        win.present()

    def do_open(self, files, n_files, hint):
        win: CineWindow = cast(CineWindow, self.props.active_window)
        open_new = settings.get_boolean("open-new-windows") or not win

        if open_new:
            win = CineWindow(application=self)
            win.start_page.set_visible(False)

            first_video_path = None
            for gfile in files:
                first_video_path = self.find_first_file(gfile)

                if first_video_path:
                    break

            if first_video_path:
                try:
                    cmd = [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=width,height:stream_side_data=rotation",
                        "-of",
                        "csv=s=x:p=0",
                        first_video_path,
                    ]
                    output = subprocess.check_output(
                        cmd, text=True, timeout=2, stderr=subprocess.DEVNULL
                    ).strip()

                    if output:
                        # "1920x1080x-90" or just "1920x1080"
                        parts = output.splitlines()[0].split("x")

                        width = int(parts[0])
                        height = int(parts[1])

                        rotation_value = parts[2].strip() if len(parts) > 2 else ""
                        rotation = int(rotation_value) if rotation_value else 0

                        if abs(rotation) in (90, 270):
                            w = height
                            h = width
                        else:
                            w = width
                            h = height

                        win.set_window_size(w, h)
                except Exception:
                    logger.exception("Metadata probe failed")
            win.present()
        else:
            win.present()
            if is_same_playlist(win.mpv.playlist):
                win.mpv.write_watch_later_config()
            win.mpv.stop()

        if (
            self.gpu_validation_config is not None
            and self._gpu_validation_session is None
        ):
            from .gpu_validation import prepare_validation_player

            self._gpu_validation_setup_failures = prepare_validation_player(
                win.mpv, self.gpu_validation_config.mode
            )

        for gfile in files:
            path = gfile.get_path() or gfile.get_uri()
            if path:
                win.mpv.loadfile(path, "append-play")

        for window in self.get_windows():
            w = cast(CineWindow, window)
            # Pause previous opened windows
            w.mpv.pause = w != win

        win.hide_ui_timeout()

        if (
            self.gpu_validation_config is not None
            and self._gpu_validation_session is None
        ):
            from .gpu_validation import GpuValidationSession

            self._gpu_validation_session = GpuValidationSession(
                win, self.gpu_validation_config
            )
            GLib.idle_add(self._gpu_validation_session.start)

    def find_first_file(self, gfile, visited=None):
        """Local-only recursive search."""
        if gfile.get_uri_scheme() != "file":
            return None

        if visited is None:
            visited = set()

        path = gfile.get_path()
        if not path or path in visited:
            return None
        visited.add(path)

        try:
            info = gfile.query_info(
                "standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None
            )
            f_type = info.get_file_type()

            if f_type == Gio.FileType.REGULAR:
                return path

            if f_type == Gio.FileType.DIRECTORY:
                enumerator = gfile.enumerate_children(
                    "standard::name,standard::type",
                    Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                    None,
                )

                subdirectories = []
                for child in enumerator:
                    child_type = child.get_file_type()
                    name = child.get_name()

                    if name.startswith("."):
                        continue

                    if child_type == Gio.FileType.REGULAR:
                        return gfile.get_child(name).get_path()
                    elif child_type == Gio.FileType.DIRECTORY:
                        subdirectories.append(gfile.get_child(name))

                for folder in subdirectories:
                    found = self.find_first_file(folder, visited)
                    if found:
                        return found
        except Exception:
            logger.exception("find_first_file failed")
        return None

    # From showtime
    def do_handle_local_options(self, options: GLib.VariantDict):
        """Handle local command line arguments."""
        self.register()  # This is so props.is_remote works

        if self.props.is_remote:
            if options.contains("new-window"):
                return -1

            print("Cine is running, to open a new window, run with --new-window.")
            return 0

        return -1

    def on_preferences_action(self, *args):
        """Callback for the app.preferences action."""
        preferences = Preferences(self.props.active_window)
        preferences.present(self.props.active_window)

    def _on_about_action(self, *args):
        """Callback for the app.about action."""
        APP_VERSION = sys.modules["__main__"].VERSION
        about = Adw.AboutDialog(
            application_name=_("CineHDR"),
            application_icon="io.github.rusmikev.CineHDR",
            developer_name="Diego Povliuk",
            developers=["Diego Povliuk", "rusmikev"],
            comments=_("Unofficial fork of Cine with HDR playback support, improved with AI."),
            version=APP_VERSION,
            copyright="© 2026 Diego Povliuk (Original App)\n© 2026 rusmikev (HDR modifications)",
            issue_url="https://github.com/rusmikev/CineHDR/issues",
            license_type=Gtk.License.GPL_3_0,
        )
        try:
            # Translators: Replace "translator-credits" with your name/username, and optionally an email or URL.
            about.set_translator_credits(_("translator-credits"))
        except NameError:
            pass

        about.add_acknowledgement_section(
            None,
            [
                "MPV https://mpv.io/",
                "python-mpv https://pypi.org/project/python-mpv/",
                "Celluloid https://celluloid-player.github.io/",
                "Showtime https://apps.gnome.org/Showtime/",
                "Workbench https://apps.gnome.org/Workbench/",
            ],
        )

        about.add_link(
            "Sponsor upstream author on GitHub",
            "https://github.com/sponsors/diegopvlk",
        )

        about.add_link(
            "Donate to upstream author (PayPal)",
            "https://www.paypal.com/donate?hosted_button_id=DVL7H35GA66X6",
        )

        about.add_link(
            "Doar / Pix (upstream author): diego.pvlk@gmail.com",
            "diego.pvlk@gmail.com",
        )

        about.add_other_app(
            "io.github.diegopvlk.Dosage", "Dosage (by upstream author)", "Keep track of your treatments"
        )

        about.add_other_app(
            "io.github.diegopvlk.Tomatillo", "Tomatillo (by upstream author)", "Focus better, work smarter"
        )

        about.present(self.props.active_window)

    def _create_action(self, name, callback, shortcuts=None):
        """Add an application action."""
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)

    def _on_shutdown(self, *args):
        if self._gpu_validation_session is not None:
            self._gpu_validation_session.abort()
        for win in self.get_windows():
            win.close()


def main(version):
    """The application's entry point."""
    app = CineApplication()
    return app.run(sys.argv)
