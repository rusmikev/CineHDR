# hdr_controller.py
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

"""
HDR state controller and configuration management.

This module decouples user interface components (menus, window badges) and
OpenGL rendering widgets from HDR business logic. It handles:
1. GSettings persistence for HDR configuration.
2. Observing libmpv stream properties (video-params, transfer characteristics).
3. Computing tone mapping rules and safe SDR fallbacks.
"""

from gi.repository import GObject, Gio
from .utils import idle_add_once
from .hdr_detection import is_hdr_content, check_hdr_support


def _get_hdr_settings():
    return Gio.Settings.new("io.github.rusmikev.CineHDR")


def load_hdr_config() -> dict:
    """Load full HDR configuration from GSettings."""
    try:
        settings = _get_hdr_settings()
        return {
            "hdr_enabled": settings.get_boolean("hdr-enabled"),
            "hdr_target_peak": settings.get_string("hdr-target-peak"),
            "hdr_target_prim": settings.get_string("hdr-target-prim")
        }
    except Exception as e:
        print(f"Error loading HDR config from GSettings: {e}")
    return {
        "hdr_enabled": True,
        "hdr_target_peak": "auto",
        "hdr_target_prim": "auto"
    }


def save_hdr_config(config: dict):
    """Save updated HDR configuration dict to GSettings."""
    try:
        settings = _get_hdr_settings()
        if "hdr_enabled" in config:
            settings.set_boolean("hdr-enabled", bool(config["hdr_enabled"]))
        if "hdr_target_peak" in config:
            settings.set_string("hdr-target-peak", str(config["hdr_target_peak"]))
        if "hdr_target_prim" in config:
            settings.set_string("hdr-target-prim", str(config["hdr_target_prim"]))
    except Exception as e:
        print(f"Error saving HDR config to GSettings: {e}")


def load_hdr_setting() -> bool:
    """Load boolean HDR enabled toggle from GSettings."""
    try:
        settings = _get_hdr_settings()
        return settings.get_boolean("hdr-enabled")
    except Exception:
        return True


def save_hdr_setting(enabled: bool):
    """Save boolean HDR enabled toggle to GSettings."""
    try:
        settings = _get_hdr_settings()
        settings.set_boolean("hdr-enabled", bool(enabled))
    except Exception as e:
        print(f"Error saving hdr-enabled to GSettings: {e}")


class HdrController(GObject.Object):
    """
    Controller managing HDR state, GSettings persistence, and libmpv color parameters.

    Decouples UI widgets and OpenGL renderers from business logic. Observes video stream
    metadata and display capabilities to automatically toggle HDR color signaling or fall
    back to SDR protection.
    """
    def __init__(self, mpv_player, on_change_cb=None):
        super().__init__()
        self.mpv = mpv_player
        self.on_change_cb = on_change_cb

        config = load_hdr_config()
        self._hdr_enabled = config["hdr_enabled"]
        self._hdr_target_peak = config["hdr_target_peak"]
        self._hdr_target_prim = config["hdr_target_prim"]
        self._is_hdr_content = False
        self._hdr_support_warned = False

        try:
            self._gsettings = _get_hdr_settings()
            self._gsettings.connect("changed::hdr-enabled", self._on_gsettings_changed)
            self._gsettings.connect("changed::hdr-target-prim", self._on_gsettings_changed)
            self._gsettings.connect("changed::hdr-target-peak", self._on_gsettings_changed)
        except Exception:
            self._gsettings = None

        @self.mpv.property_observer("video-params")
        def _on_video_params(_name, params):
            is_hdr = is_hdr_content(params)
            if self._is_hdr_content != is_hdr:
                self._is_hdr_content = is_hdr
                idle_add_once(self.apply_hdr_settings)

        @self.mpv.property_observer("display-hdr")
        @self.mpv.property_observer("target-trc")
        @self.mpv.property_observer("icc-profile")
        def _on_mpv_color_param_changed(_name, _value):
            idle_add_once(self.apply_hdr_settings)

        self.apply_hdr_settings()

    def _on_gsettings_changed(self, settings, key):
        if key == "hdr-enabled":
            self._hdr_enabled = settings.get_boolean("hdr-enabled")
        elif key == "hdr-target-prim":
            self._hdr_target_prim = settings.get_string("hdr-target-prim")
        elif key == "hdr-target-peak":
            self._hdr_target_peak = settings.get_string("hdr-target-peak")
        self.apply_hdr_settings()
        if self.on_change_cb:
            self.on_change_cb()

    def apply_hdr_settings(self):
        """Apply tone mapping parameters and target primaries for HDR playback."""
        try:
            hdr_supported = check_hdr_support()
            if self._hdr_enabled and self._is_hdr_content and hdr_supported:
                self.mpv["target-colorspace-hint"] = "yes"
                self.mpv["target-trc"] = "pq"
                self.mpv["target-prim"] = self._hdr_target_prim
                self.mpv["hdr-compute-peak"] = "yes"
                target_peak = self._hdr_target_peak
                if target_peak not in ("auto", "200", "400", "600", "1000", "1600"):
                    target_peak = "auto"

                if target_peak == "auto":
                    self.mpv["target-peak"] = "auto"
                else:
                    self.mpv["target-peak"] = int(float(target_peak))
            else:
                # Safe SDR fallback
                self.mpv["target-colorspace-hint"] = "no"
                self.mpv["target-prim"] = "auto"
                self.mpv["target-peak"] = "auto"
                self.mpv["target-trc"] = "auto"
                self.mpv["hdr-compute-peak"] = "auto"
        except Exception as e:
            print(f"Error applying HDR settings: {e}")
        if self.on_change_cb:
            self.on_change_cb()

    @property
    def hdr_enabled(self) -> bool:
        return self._hdr_enabled

    @hdr_enabled.setter
    def hdr_enabled(self, value: bool):
        self._hdr_enabled = value
        self.apply_hdr_settings()

    @property
    def hdr_target_peak(self) -> str:
        return self._hdr_target_peak

    @hdr_target_peak.setter
    def hdr_target_peak(self, value: str):
        self._hdr_target_peak = value
        self.apply_hdr_settings()

    @property
    def hdr_target_prim(self) -> str:
        return self._hdr_target_prim

    @hdr_target_prim.setter
    def hdr_target_prim(self, value: str):
        self._hdr_target_prim = value
        self.apply_hdr_settings()

    @property
    def is_hdr_content(self) -> bool:
        return self._is_hdr_content

    @is_hdr_content.setter
    def is_hdr_content(self, value: bool):
        if self._is_hdr_content != value:
            self._is_hdr_content = value
            self.apply_hdr_settings()

    @property
    def is_hdr_active(self) -> bool:
        """Returns True if HDR color state should be applied to the GL texture."""
        return self._hdr_enabled and self._is_hdr_content and check_hdr_support()

    def check_unsupported_warning(self, gdk_display):
        """Log a warning if HDR is requested and content is HDR, but display/GTK lacks support."""
        if self._hdr_enabled and self._is_hdr_content and not check_hdr_support():
            if not self._hdr_support_warned:
                reason = "Gdk.ColorState is not available (requires GTK >= 4.16)"
                from gi.repository import Gdk
                if hasattr(Gdk.ColorState, "get_rec2100_pq"):
                    if gdk_display and "Wayland" not in gdk_display.__class__.__name__:
                        reason = f"HDR signaling is not supported under {gdk_display.__class__.__name__} (requires Wayland)"
                    else:
                        reason = "Wayland compositor does not support HDR/color management"
                print(f"WARNING: HDR playback is active but target output is unsupported: {reason}. Falling back to SDR tonemapping.")
                self._hdr_support_warned = True

    def disconnect(self):
        """Disconnect GSettings observers."""
        if getattr(self, "_gsettings", None):
            try:
                self._gsettings.disconnect_by_func(self._on_gsettings_changed)
            except Exception:
                pass
            self._gsettings = None
