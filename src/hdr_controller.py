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

import logging
from typing import Any, Optional
from gi.repository import GObject, Gio, GLib
from .utils import idle_add_once
from .hdr_detection import is_hdr_content, check_hdr_support, get_hdr_unsupported_reason


def _get_hdr_settings() -> Optional[Gio.Settings]:
    """Get GSettings instance if schema is installed, else None."""
    try:
        schema_source = Gio.SettingsSchemaSource.get_default()
        if not schema_source or not schema_source.lookup("io.github.rusmikev.CineHDR", True):
            return None
        return Gio.Settings.new("io.github.rusmikev.CineHDR")
    except Exception:
        return None


def load_hdr_config() -> dict:
    """Load full HDR configuration from GSettings with migration from deprecated hdr-enabled."""
    try:
        settings = _get_hdr_settings()
        if not settings:
            raise RuntimeError("GSettings schema not available")
        mode = settings.get_string("hdr-mode")
        if not mode or mode not in ("auto", "force-hdr", "force-sdr"):
            mode = "auto"
        enabled = settings.get_boolean("hdr-enabled")
        if not enabled and mode == "auto":
            mode = "force-sdr"
            try:
                settings.set_string("hdr-mode", mode)
                settings.set_boolean("hdr-enabled", True)
            except GLib.Error as e:
                logging.warning(f"Failed to migrate deprecated hdr-enabled key: {e}")
        return {
            "hdr_mode": mode,
            "hdr_enabled": enabled,
            "hdr_target_peak": settings.get_string("hdr-target-peak"),
            "hdr_target_prim": settings.get_string("hdr-target-prim")
        }
    except (RuntimeError, GLib.Error, AttributeError) as e:
        logging.warning(f"Error loading HDR config from GSettings: {e}")
    return {
        "hdr_mode": "auto",
        "hdr_enabled": True,
        "hdr_target_peak": "auto",
        "hdr_target_prim": "auto"
    }


def save_hdr_config(config: dict):
    """Save updated HDR configuration dict to GSettings."""
    try:
        settings = _get_hdr_settings()
        if not settings:
            return
        if "hdr_mode" in config:
            settings.set_string("hdr-mode", str(config["hdr_mode"]))
        if "hdr_enabled" in config:
            settings.set_boolean("hdr-enabled", bool(config["hdr_enabled"]))
        if "hdr_target_peak" in config:
            settings.set_string("hdr-target-peak", str(config["hdr_target_peak"]))
        if "hdr_target_prim" in config:
            settings.set_string("hdr-target-prim", str(config["hdr_target_prim"]))
    except (GLib.Error, AttributeError) as e:
        logging.error(f"Error saving HDR config to GSettings: {e}")


def load_hdr_setting() -> bool:
    """Load boolean HDR enabled toggle from GSettings."""
    try:
        settings = _get_hdr_settings()
        if not settings:
            return True
        return settings.get_boolean("hdr-enabled")
    except Exception:
        return True


def save_hdr_setting(enabled: bool):
    """Save boolean HDR enabled toggle to GSettings."""
    try:
        settings = _get_hdr_settings()
        if not settings:
            return
        settings.set_boolean("hdr-enabled", bool(enabled))
    except (GLib.Error, AttributeError) as e:
        logging.error(f"Error saving hdr-enabled to GSettings: {e}")


def load_hdr_mode() -> str:
    """Load string HDR mode from GSettings."""
    try:
        settings = _get_hdr_settings()
        if not settings:
            return "auto"
        mode = settings.get_string("hdr-mode")
        if mode in ("auto", "force-hdr", "force-sdr"):
            return mode
    except Exception:
        pass
    return "auto"


def save_hdr_mode(mode: str):
    """Save string HDR mode to GSettings."""
    try:
        if mode not in ("auto", "force-hdr", "force-sdr"):
            mode = "auto"
        settings = _get_hdr_settings()
        if not settings:
            return
        settings.set_string("hdr-mode", mode)
    except (GLib.Error, AttributeError) as e:
        logging.error(f"Error saving hdr-mode to GSettings: {e}")


class HdrController(GObject.Object):
    """
    Controller managing HDR state, GSettings persistence, and libmpv color parameters.

    Decouples UI widgets and OpenGL renderers from business logic. Observes video stream
    metadata and display capabilities to automatically toggle HDR color signaling or fall
    back to SDR protection.
    """
    def __init__(self, mpv_player: Any, on_change_cb: Optional[Any] = None):
        super().__init__()
        self.mpv = mpv_player
        self.on_change_cb = on_change_cb

        config = load_hdr_config()
        self._hdr_mode = config["hdr_mode"]
        self._hdr_enabled = config["hdr_enabled"]
        self._hdr_target_peak = config["hdr_target_peak"]
        self._hdr_target_prim = config["hdr_target_prim"]
        self._is_hdr_content = False
        self._hdr_support_warned = False

        try:
            self._gsettings = _get_hdr_settings()
            if self._gsettings:
                self._gsettings.connect("changed::hdr-mode", self._on_gsettings_changed)
                self._gsettings.connect("changed::hdr-enabled", self._on_gsettings_changed)
                self._gsettings.connect("changed::hdr-target-prim", self._on_gsettings_changed)
                self._gsettings.connect("changed::hdr-target-peak", self._on_gsettings_changed)
        except Exception:
            self._gsettings = None

        self._mpv_observers = []

        @self.mpv.property_observer("video-params")
        def _on_video_params(_name, params):
            # See mpv docs: video-params property contains stream color metadata (primaries, gamma, sig-peak)
            is_hdr = is_hdr_content(params)
            if self._is_hdr_content != is_hdr:
                self._is_hdr_content = is_hdr
                idle_add_once(self.apply_hdr_settings)
                if hasattr(self, "on_content_change_cb") and self.on_content_change_cb:
                    idle_add_once(self.on_content_change_cb)
        self._mpv_observers.append(("video-params", _on_video_params))

        self.apply_hdr_settings()

    def _on_gsettings_changed(self, settings: Gio.Settings, key: str):
        if key == "hdr-mode":
            self._hdr_mode = settings.get_string("hdr-mode")
        elif key == "hdr-enabled":
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
        if self.is_hdr_active:
            target_peak = self._hdr_target_peak
            if target_peak not in ("auto", "200", "400", "600", "1000", "1600"):
                target_peak = "auto"
            peak_val = "auto" if target_peak == "auto" else int(float(target_peak))

            props = [
                ("target-colorspace-hint", "yes"),
                ("target-trc", "pq"),
                ("target-prim", self._hdr_target_prim),
                ("hdr-compute-peak", "yes"),
                ("target-peak", peak_val),
            ]
        else:
            # Safe SDR fallback
            props = [
                ("target-colorspace-hint", "no"),
                ("target-prim", "auto"),
                ("target-peak", "auto"),
                ("target-trc", "auto"),
                ("hdr-compute-peak", "auto"),
            ]

        for prop, val in props:
            try:
                self.mpv[prop] = val
            except Exception as e:
                logging.warning(f"Failed to set mpv property '{prop}' to '{val}': {e}")

        if self.on_change_cb:
            self.on_change_cb()

    @property
    def hdr_mode(self) -> str:
        return self._hdr_mode

    @hdr_mode.setter
    def hdr_mode(self, value: str):
        if value not in ("auto", "force-hdr", "force-sdr"):
            value = "auto"
        if self._hdr_mode != value:
            self._hdr_mode = value
            self._hdr_enabled = (value != "force-sdr")
            self.apply_hdr_settings()

    @property
    def hdr_enabled(self) -> bool:
        return self._hdr_enabled

    @hdr_enabled.setter
    def hdr_enabled(self, value: bool):
        self._hdr_enabled = value
        if not value and self._hdr_mode == "force-hdr":
            self._hdr_mode = "auto"
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
        if not check_hdr_support():
            return False
        if self._hdr_mode == "force-sdr" or not self._hdr_enabled:
            return False
        if self._hdr_mode == "force-hdr":
            return True
        return self._is_hdr_content

    def check_unsupported_warning(self, gdk_display: Any):
        """Log a warning if HDR is requested and content is HDR, but display/GTK lacks support."""
        requested = (self._hdr_mode == "force-hdr") or (self._hdr_mode == "auto" and self._hdr_enabled and self._is_hdr_content)
        if requested and not check_hdr_support():
            if not self._hdr_support_warned:
                reason = get_hdr_unsupported_reason(gdk_display)
                logging.warning(f"HDR playback is active but target output is unsupported: {reason}. Falling back to SDR tonemapping.")
                self._hdr_support_warned = True

    def disconnect(self):
        """Disconnect GSettings and libmpv property observers."""
        if getattr(self, "_gsettings", None):
            try:
                self._gsettings.disconnect_by_func(self._on_gsettings_changed)
            except Exception:
                pass
            self._gsettings = None

        for prop_name, handler in getattr(self, "_mpv_observers", []):
            try:
                self.mpv.unobserve_property(prop_name, handler)
            except Exception:
                pass
        self._mpv_observers = []


