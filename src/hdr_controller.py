# hdr_controller.py
#
# Copyright 2026 rusmikev / Diego Povliuk
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
from .hdr_detection import (
    is_hdr_content,
    check_hdr_support,
    get_hdr_unsupported_reason,
    get_dovi_info,
    get_monitor_hdr_state,
)

# Single source of truth for the HDR mode values and the peak-brightness
# presets. hdr_menu.py builds its dropdowns from these tuples, so the
# index <-> value mapping used by the UI is the same object tested in
# tests/test_hdr.py (no duplicated tables).
HDR_MODES = ("auto", "force-hdr", "force-sdr")
HDR_PEAK_PRESETS = ("auto", "200", "400", "600", "1000", "1600")


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
    """Load full HDR configuration from GSettings without deprecated hdr-enabled key."""
    try:
        settings = _get_hdr_settings()
        if not settings:
            raise RuntimeError("GSettings schema not available")
        mode = settings.get_string("hdr-mode")
        if not mode or mode not in HDR_MODES:
            mode = "auto"
        return {
            "hdr_mode": mode,
            "hdr_enabled": (mode != "force-sdr"),
            "hdr_target_peak": settings.get_string("hdr-target-peak"),
            # Legacy key: the gamut control was removed because the GL texture
            # color state (Rec.2100) fixes the primaries to BT.2020. The key is
            # still read/written so existing user settings round-trip cleanly.
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
        elif "hdr_enabled" in config:
            settings.set_string("hdr-mode", "auto" if config["hdr_enabled"] else "force-sdr")
        if "hdr_target_peak" in config:
            settings.set_string("hdr-target-peak", str(config["hdr_target_peak"]))
        if "hdr_target_prim" in config:
            settings.set_string("hdr-target-prim", str(config["hdr_target_prim"]))
    except (GLib.Error, AttributeError) as e:
        logging.error(f"Error saving HDR config to GSettings: {e}")


def load_hdr_setting() -> bool:
    """Load boolean HDR enabled toggle from GSettings."""
    return load_hdr_mode() != "force-sdr"


def save_hdr_setting(enabled: bool):
    """Save boolean HDR enabled toggle to GSettings."""
    save_hdr_mode("auto" if enabled else "force-sdr")


def load_hdr_mode() -> str:
    """Load string HDR mode from GSettings."""
    try:
        settings = _get_hdr_settings()
        if not settings:
            return "auto"
        mode = settings.get_string("hdr-mode")
        if mode in HDR_MODES:
            return mode
    except Exception:
        pass
    return "auto"


def save_hdr_mode(mode: str):
    """Save string HDR mode to GSettings."""
    try:
        if mode not in HDR_MODES:
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
        self._disconnected = False

        config = load_hdr_config()
        self._hdr_mode = config["hdr_mode"]
        self._hdr_target_peak = config["hdr_target_peak"]
        self._is_hdr_content = False
        self._output_hint = None
        self._hdr_support_warned = False
        self._dovi_info: Optional[dict] = None
        self._dovi_warned = False

        self._initial_mpv_props = {}
        for prop in ("target-colorspace-hint", "target-trc", "target-prim", "target-peak"):
            try:
                self._initial_mpv_props[prop] = self.mpv[prop]
            except Exception:
                pass

        try:
            self._gsettings = _get_hdr_settings()
            if self._gsettings:
                self._gsettings.connect("changed::hdr-mode", self._on_gsettings_changed)
                self._gsettings.connect("changed::hdr-target-peak", self._on_gsettings_changed)
        except Exception:
            self._gsettings = None

        self._mpv_observers = []

        @self.mpv.property_observer("video-params")
        def _on_video_params(_name, params):
            # See mpv docs: video-params property contains stream color metadata (primaries, gamma, sig-peak)
            is_hdr = is_hdr_content(params)
            # Dolby Vision profile comes from the *track* properties; video-params
            # only carries the already-mapped colorimetry (bt.2020/pq) that
            # libplacebo writes for every single-layer DoVi frame.
            dovi = get_dovi_info(params, self.mpv)

            changed = False
            if self._is_hdr_content != is_hdr:
                self._is_hdr_content = is_hdr
                changed = True
            if self._dovi_info != dovi:
                self._dovi_info = dovi
                self._dovi_warned = False
                changed = True
            if changed:
                idle_add_once(self.apply_hdr_settings)

            if hasattr(self, "on_content_change_cb") and self.on_content_change_cb:
                idle_add_once(self.on_content_change_cb)
        self._mpv_observers.append(("video-params", _on_video_params))

        self.apply_hdr_settings()

    def _on_gsettings_changed(self, settings: Gio.Settings, key: str):
        if key == "hdr-mode":
            self._hdr_mode = settings.get_string("hdr-mode")
        elif key == "hdr-target-peak":
            self._hdr_target_peak = settings.get_string("hdr-target-peak")
        self.apply_hdr_settings()
        if self.on_change_cb:
            self.on_change_cb()

    def apply_hdr_settings(self):
        """Apply tone mapping parameters and target primaries for HDR playback."""
        if getattr(self, "_disconnected", False) or not self.mpv:
            return
        if self.is_hdr_active:
            target_peak = self._hdr_target_peak
            if target_peak not in HDR_PEAK_PRESETS:
                target_peak = "auto"
            peak_val = "auto" if target_peak == "auto" else int(float(target_peak))

            # The published GL texture carries Gdk.ColorState Rec.2100 PQ, i.e.
            # GTK/the compositor decode it as BT.2020 + PQ by contract. The
            # encoding primaries therefore MUST be bt.2020 — anything else
            # (dci-p3, bt.709) would be reinterpreted as BT.2020 and shift all
            # colors (F7). This is why there is no user-facing gamut option.
            target_prim = "bt.2020"

            # hdr-compute-peak is intentionally left untouched: mpv's default
            # ("auto") already enables per-frame peak detection when tone
            # mapping is active (numeric target-peak) and skips the extra GPU
            # pass in true pass-through (target-peak=auto).
            props = [
                ("target-colorspace-hint", "yes"),
                ("target-trc", "pq"),
                ("target-prim", target_prim),
                ("target-peak", peak_val),
            ]
        else:
            # Safe SDR fallback: restore initial mpv profile or defaults (P2-13)
            defaults = {
                "target-colorspace-hint": "no",
                "target-prim": "auto",
                "target-peak": "auto",
                "target-trc": "auto",
            }
            props = []
            for prop, default_val in defaults.items():
                val = getattr(self, "_initial_mpv_props", {}).get(prop)
                if val is None:
                    val = default_val
                props.append((prop, val))

        for prop, val in props:
            try:
                self.mpv[prop] = val
            except Exception as e:
                logging.warning(f"Failed to set mpv property '{prop}' to '{val}': {e}")

        # Runs on every (re)apply so the "HDR requested but unavailable"
        # warning is actually reachable — previously it was only invoked from
        # a code path that already required HDR support to be present.
        self.check_unsupported_warning()
        self.check_dovi_warning()

        if self.on_change_cb:
            self.on_change_cb()

    @property
    def hdr_mode(self) -> str:
        return self._hdr_mode

    @hdr_mode.setter
    def hdr_mode(self, value: str):
        if value not in HDR_MODES:
            value = "auto"
        if self._hdr_mode != value:
            self._hdr_mode = value
            self.apply_hdr_settings()

    @property
    def hdr_enabled(self) -> bool:
        return self._hdr_mode != "force-sdr"

    @hdr_enabled.setter
    def hdr_enabled(self, value: bool):
        new_mode = "auto" if value else "force-sdr"
        if not value and self._hdr_mode == "force-hdr":
            new_mode = "auto"
        self.hdr_mode = new_mode

    @property
    def hdr_target_peak(self) -> str:
        return self._hdr_target_peak

    @hdr_target_peak.setter
    def hdr_target_peak(self, value: str):
        if self._hdr_target_peak != str(value):
            self._hdr_target_peak = str(value)
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
    def dovi_profile(self) -> Optional[int]:
        """Dolby Vision profile of the active video track, if any."""
        return (self._dovi_info or {}).get("profile")

    @property
    def dovi_level(self) -> Optional[int]:
        return (self._dovi_info or {}).get("level")

    @property
    def dovi_detected(self) -> bool:
        return self._dovi_info is not None

    @property
    def dovi_unsupported(self) -> bool:
        """True when the stream's Dolby Vision profile cannot be rendered here."""
        return bool((self._dovi_info or {}).get("unsupported"))

    @property
    def output_hint(self):
        """Connector name of the monitor the video widget currently sits on."""
        return self._output_hint

    def set_output_hint(self, connector):
        """Called by MpvVideoWidget on realize / enter-monitor. A change can
        flip the auto-mode decision (SDR screen <-> HDR screen), so settings
        are re-applied."""
        connector = str(connector) if connector else None
        if self._output_hint != connector:
            self._output_hint = connector
            self.apply_hdr_settings()

    @property
    def is_hdr_active(self) -> bool:
        """Returns True if HDR color state should be applied to the GL texture."""
        if not check_hdr_support():
            return False
        # Capability gate, deliberately ahead of the user's mode: an unshapeable
        # Dolby Vision profile (5) reaches us as IPT decoded with a BT.2020-NC
        # matrix, so tagging it Rec.2100 PQ shows broken colors *and* flips the
        # monitor into HDR. force-hdr cannot fix the picture, so it must not win
        # here. Note that video-params reports gamma=pq for these streams, so
        # is_hdr_content() alone would happily enable HDR.
        if self.dovi_unsupported:
            return False
        if self._hdr_mode == "force-sdr":
            return False
        if self._hdr_mode == "force-hdr":
            # Explicit user override: unlike the DoVi gate above, the picture
            # here is *valid* — passing PQ to an SDR output merely trades
            # mpv's tone mapping for the compositor's simpler conversion, so
            # the user's choice is respected.
            return True
        # Quality gate for auto mode only: a capable compositor with monitor
        # HDR switched *off* converts PQ -> SDR itself, which looks worse
        # than mpv's tone mapping. Only a definitive "monitor is SDR" blocks
        # HDR; an unknown state (None) preserves previous behaviour.
        if get_monitor_hdr_state(self._output_hint) is False:
            return False
        return self._is_hdr_content

    def check_dovi_warning(self):
        """Log once when HDR is refused because of an unrenderable DoVi profile."""
        if not self.dovi_unsupported or self._dovi_warned:
            return
        self._dovi_warned = True
        logging.warning(
            "Dolby Vision Profile %s detected. The libmpv render API uses mpv's "
            "legacy GPU renderer, which cannot apply the Dolby Vision RPU reshaping "
            "(implemented only by libplacebo / vo=gpu-next), so the decoded frame is "
            "not Rec.2100 PQ. Falling back to SDR tone mapping instead of tagging "
            "unshaped IPT data as HDR.",
            self.dovi_profile,
        )

    def check_unsupported_warning(self, gdk_display: Any = None):
        """Log a warning if HDR is requested and content is HDR, but display/GTK lacks support."""
        requested = (self._hdr_mode == "force-hdr") or (self._hdr_mode == "auto" and self._is_hdr_content)
        if requested and not check_hdr_support():
            if not self._hdr_support_warned:
                reason = get_hdr_unsupported_reason(gdk_display)
                logging.warning(f"HDR playback is active but target output is unsupported: {reason}. Falling back to SDR tonemapping.")
                self._hdr_support_warned = True

    def disconnect(self):
        """Disconnect GSettings and libmpv property observers."""
        self._disconnected = True
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


