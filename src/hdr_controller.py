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
import mpv
from typing import Any, Optional
from gi.repository import GObject, Gio, GLib
from .utils import idle_add_once
from . import wayland_output_hdr
from .hdr_detection import (
    is_hdr_content,
    check_hdr_support,
    get_hdr_unsupported_reason,
    get_dovi_info,
    get_monitor_hdr_state,
    refresh_monitor_hdr_state,
    is_surface_submission_proven,
)

# Single source of truth for the HDR mode values and the peak-brightness
# presets. hdr_menu.py builds its dropdowns from these tuples, so the
# index <-> value mapping used by the UI is the same object tested in
# tests/test_hdr.py (no duplicated tables).
HDR_MODES = ("auto", "force-hdr", "force-sdr")
HDR_PEAK_PRESETS = ("auto", "200", "400", "600", "1000", "1600")


def is_tone_mapping_active(
    source_hdr: bool, hdr_output_active: bool, target_peak: object
) -> bool:
    """Describe both HDR-to-SDR and numeric-peak HDR-to-HDR mapping."""
    return bool(
        source_hdr
        and (not hdr_output_active or target_peak not in (None, "auto"))
    )


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
    """Save string HDR mode from GSettings."""
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
        self._last_video_params = None
        self._effective_peak_source = "auto"
        self._hdr_support_warned = False
        self._dovi_info: Optional[dict] = None
        self._dovi_warned = False
        self._force_hdr_warned = False
        self._supports_dovi_reshaping = False

        self._initial_mpv_props = {}
        # target-colorspace-hint is deliberately absent: it is a no-op under
        # the libmpv render API (mpv does not own the swapchain), so CineHDR
        # neither sets nor restores it.
        for prop in ("target-trc", "target-prim", "target-peak"):
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

        observer_decorator = getattr(self.mpv, "property_observer", None)
        if callable(observer_decorator):
            try:
                @observer_decorator("video-params")
                def _on_video_params(_name, params):
                    # See mpv docs: video-params property contains stream color metadata (primaries, gamma, sig-peak)
                    # Cache the raw dict: apply_hdr_settings() reads sig-peak from it
                    # for the automatic target-peak substitution. python-mpv has no
                    # reliable synchronous getter to use from there instead.
                    self._last_video_params = params if isinstance(params, dict) else None
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
            except Exception:
                pass

        # Initial background non-blocking monitor state probe
        self._monitor_poll_timer_id: Optional[int] = None
        self._monitor_probe_source = None
        self.request_monitor_probe()

        self.apply_hdr_settings()

    def request_monitor_probe(self):
        """Asynchronously probe Wayland monitor HDR states without blocking the UI loop."""
        if getattr(self, "_disconnected", False):
            return
        if getattr(self, "_monitor_probe_source", None):
            try:
                if hasattr(self._monitor_probe_source, "cancel"):
                    self._monitor_probe_source.cancel()
                elif hasattr(self._monitor_probe_source, "destroy"):
                    self._monitor_probe_source.destroy()
            except Exception:
                pass
            self._monitor_probe_source = None

        from .hdr_detection import async_refresh_monitor_hdr_state
        self._monitor_probe_source = async_refresh_monitor_hdr_state(
            on_complete=self._on_monitor_hdr_state_probed
        )

    def _on_monitor_hdr_state_probed(self, state: Optional[bool]):
        """Handle completion of the non-blocking monitor HDR probe."""
        self._monitor_probe_source = None
        if getattr(self, "_disconnected", False):
            return
        from .hdr_detection import check_hdr_support
        check_hdr_support(allow_probe=False)
        self.apply_hdr_settings()

    def check_monitor_hdr_state_change(self):
        """Called on monitor configuration changes to re-evaluate HDR output asynchronously."""
        self.request_monitor_probe()

    def _on_gsettings_changed(self, settings, key):
        if key == "hdr-mode":
            self.hdr_mode = settings.get_string("hdr-mode")
        elif key == "hdr-target-peak":
            self.hdr_target_peak = settings.get_string("hdr-target-peak")

    def apply_hdr_settings(self):
        """
        Configure mpv video output properties according to the resolved HDR mode.
        """
        if getattr(self, "_disconnected", False) or not self.mpv or not getattr(self, "_initialized", True):
            return
        hdr_output_active = self.is_hdr_active
        effective_target_peak = "auto"
        if hdr_output_active:
            target_peak = self._hdr_target_peak
            if target_peak not in HDR_PEAK_PRESETS:
                target_peak = "auto"
            
            if target_peak == "auto":
                # Automatic target-peak: when the monitor's own peak (from its
                # image description) is meaningfully below the stream's peak,
                # hand mpv the monitor value so it tone-maps *inside* PQ to the
                # panel's real capability instead of leaving the excess to the
                # compositor's clip. Tri-state discipline: unknown stream peak
                # or unknown monitor peak -> no substitution ("auto").
                peak_val = "auto"
                self._effective_peak_source = "auto"
                monitor_peak = self._monitor_peak_nits()
                stream_peak = self._stream_peak_nits()
                if monitor_peak and stream_peak:
                    # Threshold: 90% of the stream's peak. Below that, the
                    # monitor clearly cannot hit the content's highlights and
                    # mpv's tone curve produces a visibly better gradient than
                    # leaving it to the display's / compositor's hard roll-off.
                    if monitor_peak < stream_peak * 0.9:
                        peak_val = int(round(monitor_peak))
                        self._effective_peak_source = f"monitor ({peak_val} nits)"
                effective_target_peak = peak_val
            else:
                peak_val = int(float(target_peak))
                effective_target_peak = peak_val
                self._effective_peak_source = f"user preset ({peak_val} nits)"

            # Contract: GL texture color state (Rec.2100) fixes primaries to
            # BT.2020. mpv must render into that gamut; letting it default to
            # the monitor gamut would cause GDK to convert twice and distort
            # colors.
            # hdr-compute-peak is intentionally left untouched: mpv's default
            # ("auto") already enables per-frame peak detection when tone
            # mapping is active (numeric target-peak) and skips the extra GPU
            # pass in true pass-through (target-peak=auto).
            props = [
                ("target-trc", "pq"),
                ("target-prim", "bt.2020"),
                ("target-peak", peak_val),
            ]
        else:
            # Safe SDR fallback: restore initial mpv profile or defaults
            self._effective_peak_source = "auto"
            defaults = {
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

        if not hasattr(self, "_applied_mpv_props"):
            self._applied_mpv_props = {}

        props_changed = False
        for prop, val in props:
            if self._applied_mpv_props.get(prop) == val:
                continue
            try:
                self.mpv[prop] = val
                self._applied_mpv_props[prop] = val
                props_changed = True
            except mpv.ShutdownError:
                # Property observers can deliver their final empty state after
                # CineHDR has asked libmpv to quit. This is normal shutdown,
                # not an HDR configuration failure.
                return
            except Exception as e:
                logging.warning(f"Failed to set mpv property '{prop}' to '{val}': {e}")

        self.check_unsupported_warning()
        self.check_dovi_warning()
        self.check_force_hdr_warning()

        import json
        telemetry = {
            "source_hdr": self._is_hdr_content,
            "target_trc": "pq" if hdr_output_active else "auto",
            "target_peak": effective_target_peak,
            "tone_mapping_active": is_tone_mapping_active(
                self._is_hdr_content,
                hdr_output_active,
                effective_target_peak,
            ),
            "display_hdr": get_monitor_hdr_state(self._output_hint, allow_probe=False),
            "hdr_mode": self._hdr_mode,
            "dovi_profile": self.dovi_profile,
        }
        if getattr(self, "_last_telemetry", None) != telemetry:
            self._last_telemetry = telemetry
            logging.info(f"HDR Pipeline Telemetry: {json.dumps(telemetry)}")

        if self.on_change_cb:
            self.on_change_cb()

    def set_hdr_mode(self, mode: str):
        """Set HDR operating mode ('auto', 'force-hdr', 'force-sdr') and save to GSettings."""
        if mode not in HDR_MODES:
            return
        if self._hdr_mode != mode:
            self._hdr_mode = mode
            self._force_hdr_warned = False
            self._hdr_support_warned = False
            save_hdr_mode(mode)
            self.apply_hdr_settings()
            if self.on_change_cb:
                self.on_change_cb()

    def set_target_peak(self, peak: str):
        """Set target peak brightness preset and save to GSettings."""
        if peak not in HDR_PEAK_PRESETS:
            return
        if self._hdr_target_peak != peak:
            self._hdr_target_peak = peak
            config = load_hdr_config()
            config["hdr_target_peak"] = peak
            save_hdr_config(config)
            self.apply_hdr_settings()
            if self.on_change_cb:
                self.on_change_cb()

    @property
    def hdr_mode(self) -> str:
        return self._hdr_mode

    @hdr_mode.setter
    def hdr_mode(self, value: str):
        """In-memory setter for validator/temporary overrides. Does NOT write GSettings."""
        if value not in HDR_MODES:
            value = "auto"
        if self._hdr_mode != value:
            self._hdr_mode = value
            self._force_hdr_warned = False
            self._hdr_support_warned = False
            self.apply_hdr_settings()
            if self.on_change_cb:
                self.on_change_cb()

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
    def supports_dovi_reshaping(self) -> bool:
        """Capability flag: True when active renderer and version-qualified stack support DoVi reshaping."""
        return getattr(self, "_supports_dovi_reshaping", False)

    @supports_dovi_reshaping.setter
    def supports_dovi_reshaping(self, value: bool) -> None:
        self._supports_dovi_reshaping = bool(value)

    @property
    def dovi_unsupported(self) -> bool:
        """True when the stream's Dolby Vision profile cannot be rendered here."""
        if self.supports_dovi_reshaping:
            return False
        return bool((self._dovi_info or {}).get("unsupported"))

    def _stream_peak_nits(self):
        """Peak of the playing stream in nits from cached video-params, or
        None when unknown (no guessed defaults — unknown must change
        nothing)."""
        params = self._last_video_params
        if not isinstance(params, dict):
            return None
        try:
            sig_peak = float(params.get("sig-peak"))
        except (TypeError, ValueError):
            return None
        if sig_peak <= 0:
            return None
        return sig_peak * 203.0

    def _monitor_peak_nits(self):
        """Peak luminance of the output the video sits on, in nits.

        Uses the connector hint when available; without a hint, falls back to
        the single HDR output if there is exactly one (unambiguous), else
        None. Only HDR outputs count — in force-hdr-onto-SDR the compositor
        converts and its peak is meaningless here."""
        try:
            states = wayland_output_hdr.get_output_hdr_states(allow_probe=False)
        except Exception:
            return None
        if not states:
            return None
        info = None
        if self._output_hint and self._output_hint in states:
            info = states[self._output_hint]
        else:
            hdr_infos = [i for i in states.values() if i.hdr]
            if len(hdr_infos) == 1:
                info = hdr_infos[0]
        if info is None or not info.hdr:
            return None
        if info.max_lum and info.max_lum > 0:
            return float(info.max_lum)
        return None

    @property
    def effective_peak_source(self) -> str:
        """Human-readable origin of the current target-peak value
        ('auto' | 'monitor (N nits)' | 'user preset (N nits)') — read-only,
        shown in the diagnostics dialog."""
        return self._effective_peak_source

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
        if not check_hdr_support(allow_probe=False):
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
        # Quality gate for auto mode per ADR-0007:
        # Since GTK currently does not provide a way to prove Wayland surface
        # submission of colorimetry, and testing shows it may fail to pass the
        # surface description even when the compositor supports it, 'auto' mode
        # conservatively falls back to SDR tone mapping unless surface submission
        # is proven. 'force-hdr' remains an experimental override.
        if not is_surface_submission_proven():
            return False
        if get_monitor_hdr_state(self._output_hint, allow_probe=False) is not True:
            return False
        return self._is_hdr_content

    def check_dovi_warning(self):
        """Log once when HDR is refused because of an unrenderable DoVi profile."""
        if not self.dovi_unsupported or self._dovi_warned:
            return
        self._dovi_warned = True
        logging.warning(
            "Dolby Vision Profile %s detected. Dolby Vision RPU reshaping is "
            "disabled until the active embedded renderer passes CineHDR's "
            "validation gate, so the decoded frame is not treated as Rec.2100 "
            "PQ. Falling back to SDR tone mapping instead of tagging unshaped "
            "IPT data as HDR.",
            self.dovi_profile,
        )

    def check_force_hdr_warning(self):
        """Log a warning if force-hdr is used and the monitor is SDR."""
        if self._hdr_mode == "force-hdr" and not self._force_hdr_warned:
            if get_monitor_hdr_state(self._output_hint, allow_probe=False) is False:
                self._force_hdr_warned = True
                logging.warning(
                    "Force HDR mode is active, but the target monitor is reporting SDR. "
                    "HDR metadata will be sent to the compositor, which may lead to incorrect colors "
                    "due to compositor-side conversions. HDR Signaling: forced, Display HDR: OFF, "
                    "Tone mapping: bypassed."
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
        if hasattr(self, "_monitor_poll_timer_id") and self._monitor_poll_timer_id:
            try:
                GLib.source_remove(self._monitor_poll_timer_id)
            except Exception:
                pass
            self._monitor_poll_timer_id = None

        if hasattr(self, "_monitor_probe_source") and self._monitor_probe_source:
            try:
                if hasattr(self._monitor_probe_source, "cancel"):
                    self._monitor_probe_source.cancel()
                elif hasattr(self._monitor_probe_source, "destroy"):
                    self._monitor_probe_source.destroy()
            except Exception:
                pass
            self._monitor_probe_source = None

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
