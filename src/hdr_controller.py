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
import threading
import time
from typing import Any, NamedTuple, Optional
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

try:
    import gi
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk
except Exception:
    Gdk = None

# Single source of truth for the HDR mode values and the peak-brightness
# presets. hdr_menu.py builds its dropdowns from these tuples, so the
# index <-> value mapping used by the UI is the same object tested in
# tests/test_hdr.py (no duplicated tables).
HDR_MODES = ("auto", "force-hdr", "force-sdr")
HDR_PEAK_PRESETS = ("auto", "200", "400", "600", "1000", "1600")
DEFAULT_READBACK_TIMEOUT_SEC = 0.1


class CommittedOutputState(NamedTuple):
    """Immutable committed output state contract per ADR-0007."""
    epoch: int
    target_trc: str
    target_prim: str
    target_peak: Any
    fbo_format: str
    gdk_color_state: str
    is_float: bool
    is_hdr: bool


def has_float_format_support() -> bool:
    """Check if Gdk.MemoryFormat.R16G16B16A16_FLOAT is available without runtime queries."""
    if Gdk is None:
        return False
    mem_fmt = getattr(Gdk, "MemoryFormat", None)
    return bool(mem_fmt and hasattr(mem_fmt, "R16G16B16A16_FLOAT"))


def prop_matches(actual: Any, expected: Any) -> bool:
    """Compare an actual libmpv property readback with an expected target value."""
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    act_str = str(actual).strip().lower()
    exp_str = str(expected).strip().lower()
    if act_str == exp_str:
        return True
    try:
        if abs(float(actual) - float(expected)) < 1e-4:
            return True
    except (ValueError, TypeError):
        pass
    return False


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

        self._output_epoch: int = 0
        self._pending_epoch: Optional[int] = None
        self._publication_suppressed: bool = False
        self._state_gap_reason: Optional[str] = None
        self._committed_state: Optional[CommittedOutputState] = None
        self._readback_timeout_source_id: Optional[int] = None
        self._pending_readback_query_id: int = 0
        self._active_readback_epoch: Optional[int] = None
        self._active_readback_results: dict[str, Any] = {}
        self._active_readback_errors: dict[str, str] = {}
        self._active_readback_expected: dict[str, Any] = {}
        self._active_readback_state_args: dict[str, Any] = {}
        self._event_callback_registered = False

        if (
            isinstance(self.mpv, mpv.MPV)
            and hasattr(self.mpv, "_event_handle")
            and hasattr(self.mpv, "register_event_callback")
        ):
            try:
                self.mpv.register_event_callback(self._on_mpv_event)
                self._event_callback_registered = True
            except Exception as e:
                logging.debug("Could not register event callback on mpv: %s", e)

        self._initial_mpv_props = {}
        # target-colorspace-hint is deliberately absent: it is a no-op under
        # the libmpv render API (mpv does not own the swapchain), so CineHDR
        # neither sets nor restores it.
        for prop in ("target-trc", "target-prim", "target-peak"):
            try:
                val = self.mpv[prop]
                if val is not None:
                    self._initial_mpv_props[prop] = val
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

    def apply_hdr_settings(
        self,
        readback_timeout_sec: float = DEFAULT_READBACK_TIMEOUT_SEC,
    ):
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
            expected_trc = "pq"
            expected_prim = "bt.2020"
            expected_peak = peak_val
        else:
            # Safe SDR fallback: restore initial mpv profile or defaults
            self._effective_peak_source = "auto"
            expected_trc = getattr(self, "_initial_mpv_props", {}).get("target-trc") or "auto"
            expected_prim = getattr(self, "_initial_mpv_props", {}).get("target-prim") or "auto"
            expected_peak = getattr(self, "_initial_mpv_props", {}).get("target-peak") or "auto"

        has_float = has_float_format_support()
        desired_is_float = bool(hdr_output_active and has_float)
        desired_fbo_format = "GL_RGBA16F" if desired_is_float else "GL_RGBA8"
        desired_color_state = "rec2100-pq" if hdr_output_active else "srgb"

        curr = getattr(self, "_committed_state", None)
        if (
            curr is not None
            and not getattr(self, "_publication_suppressed", False)
            and getattr(self, "_pending_epoch", None) is None
            and curr.is_hdr == hdr_output_active
            and curr.target_trc == expected_trc
            and curr.target_prim == expected_prim
            and curr.target_peak == expected_peak
            and curr.fbo_format == desired_fbo_format
            and curr.gdk_color_state == desired_color_state
        ):
            self.check_unsupported_warning()
            self.check_dovi_warning()
            self.check_force_hdr_warning()
            self._log_telemetry(hdr_output_active, effective_target_peak)
            if self.on_change_cb:
                self.on_change_cb()
            return

        if getattr(self, "_readback_timeout_source_id", None) is not None:
            try:
                GLib.source_remove(self._readback_timeout_source_id)
            except Exception:
                pass
            self._readback_timeout_source_id = None

        self._output_epoch = getattr(self, "_output_epoch", 0) + 1
        epoch = self._output_epoch
        self._pending_epoch = epoch

        props = [
            ("target-trc", expected_trc),
            ("target-prim", expected_prim),
            ("target-peak", expected_peak),
        ]

        if not hasattr(self, "_applied_mpv_props"):
            self._applied_mpv_props = {}

        setter_failed = False
        setter_error_reason = None
        for prop, val in props:
            try:
                self.mpv[prop] = val
                self._applied_mpv_props[prop] = val
            except mpv.ShutdownError:
                self._pending_epoch = None
                return
            except Exception as e:
                setter_failed = True
                setter_error_reason = f"partial setter failure: failed to set mpv property '{prop}' to '{val}': {e}"
                logging.warning(setter_error_reason)
                break

        if setter_failed:
            self._pending_epoch = None
            self._publication_suppressed = True
            self._state_gap_reason = setter_error_reason
            if self.on_change_cb:
                self.on_change_cb()
            return

        state_args = {
            "expected_trc": expected_trc,
            "expected_prim": expected_prim,
            "expected_peak": expected_peak,
            "desired_fbo_format": desired_fbo_format,
            "desired_color_state": desired_color_state,
            "desired_is_float": desired_is_float,
            "hdr_output_active": hdr_output_active,
            "effective_target_peak": effective_target_peak,
        }
        self._start_nonblocking_readback(
            epoch=epoch,
            props=props,
            state_args=state_args,
            timeout_sec=readback_timeout_sec,
        )

    def _on_mpv_event(self, event: Any) -> None:
        try:
            if getattr(self, "_disconnected", False) or getattr(self, "_shutting_down", False):
                return
            eid = getattr(getattr(event, "event_id", None), "value", None)
            if eid == getattr(getattr(mpv, "MpvEventID", None), "GET_PROPERTY_REPLY", 3):
                userdata = getattr(event, "reply_userdata", 0)
                query_id = userdata >> 8
                if query_id != getattr(self, "_pending_readback_query_id", 0):
                    return
                prop_name = None
                val = None
                if hasattr(event, "data") and event.data:
                    prop_name = getattr(event.data, "name", None)
                    val = getattr(event.data, "value", None)
                err = getattr(event, "error", 0)
                GLib.idle_add(
                    self._on_nonblocking_property_reply,
                    query_id,
                    self._active_readback_epoch,
                    prop_name,
                    val,
                    err,
                )
        except Exception as e:
            logging.debug("Error in _on_mpv_event: %s", e)

    def _start_nonblocking_readback(
        self,
        epoch: int,
        props: list[tuple[str, Any]],
        state_args: dict[str, Any],
        timeout_sec: float = DEFAULT_READBACK_TIMEOUT_SEC,
    ) -> None:
        if getattr(self, "_readback_timeout_source_id", None) is not None:
            try:
                GLib.source_remove(self._readback_timeout_source_id)
            except Exception:
                pass
            self._readback_timeout_source_id = None

        self._active_readback_epoch = epoch
        self._active_readback_results = {}
        self._active_readback_errors = {}
        self._active_readback_expected = {p: v for p, v in props}
        self._active_readback_state_args = state_args
        self._pending_readback_query_id = getattr(self, "_pending_readback_query_id", 0) + 1
        query_id = self._pending_readback_query_id

        # Dispatch nonblocking readback without blocking the GTK main loop
        if (
            isinstance(self.mpv, mpv.MPV)
            and getattr(self, "_event_callback_registered", False)
            and hasattr(self.mpv, "_event_handle")
            and hasattr(mpv, "_mpv_get_property_async")
        ):
            # Production: truly nonblocking readback using native libmpv async API
            self._readback_timeout_source_id = GLib.timeout_add(
                max(1, int(timeout_sec * 1000)),
                self._on_readback_timeout,
                epoch,
                query_id,
                timeout_sec,
            )
            for idx, (prop, _exp) in enumerate(props):
                userdata = (query_id << 8) | idx
                c_name = prop.encode("utf-8")
                try:
                    mpv._mpv_get_property_async(
                        self.mpv._event_handle, userdata, c_name, mpv.MpvFormat.NODE
                    )
                except Exception as e:
                    GLib.idle_add(
                        self._on_nonblocking_property_reply,
                        query_id,
                        epoch,
                        prop,
                        None,
                        -1,
                    )
        else:
            # Test mock harness without native libmpv event loop:
            # Query mock object directly without spawning background worker threads.
            for p, _exp in props:
                try:
                    self._active_readback_results[p] = self.mpv[p]
                except Exception as exc:
                    self._active_readback_errors[p] = str(exc)

            all_matched = True
            mismatch_reason = None
            for prop, expected_val in props:
                if prop in self._active_readback_errors:
                    all_matched = False
                    mismatch_reason = f"readback failure: {self._active_readback_errors[prop]}"
                    break
                actual_val = self._active_readback_results.get(prop)
                if not prop_matches(actual_val, expected_val):
                    all_matched = False
                    mismatch_reason = (
                        f"mismatched readback for '{prop}': expected {expected_val!r}, got {actual_val!r}"
                    )
                    break

            if all_matched:
                self._committed_state = CommittedOutputState(
                    epoch=epoch,
                    target_trc=state_args["expected_trc"],
                    target_prim=state_args["expected_prim"],
                    target_peak=state_args["expected_peak"],
                    fbo_format=state_args["desired_fbo_format"],
                    gdk_color_state=state_args["desired_color_state"],
                    is_float=state_args["desired_is_float"],
                    is_hdr=state_args["hdr_output_active"],
                )
                self._pending_epoch = None
                self._publication_suppressed = False
                self._state_gap_reason = None

                self.check_unsupported_warning()
                self.check_dovi_warning()
                self.check_force_hdr_warning()
                self._log_telemetry(
                    state_args["hdr_output_active"], state_args["effective_target_peak"]
                )
                if self.on_change_cb:
                    self.on_change_cb()
            else:
                self._pending_epoch = None
                self._publication_suppressed = True
                if timeout_sec <= 0.0:
                    self._state_gap_reason = (
                        f"bounded readback timeout (0ms) for epoch {epoch}: {mismatch_reason}"
                    )
                else:
                    self._state_gap_reason = mismatch_reason
                logging.warning(self._state_gap_reason)
                if self.on_change_cb:
                    self.on_change_cb()

    def _on_mpv_event(self, event: Any) -> None:
        try:
            if getattr(self, "_disconnected", False) or getattr(self, "_shutting_down", False):
                return
            eid = getattr(getattr(event, "event_id", None), "value", None)
            if eid == getattr(getattr(mpv, "MpvEventID", None), "GET_PROPERTY_REPLY", 3):
                userdata = getattr(event, "reply_userdata", 0)
                query_id = userdata >> 8
                if query_id != getattr(self, "_pending_readback_query_id", 0):
                    return
                prop_name = None
                val = None
                if hasattr(event, "data") and event.data:
                    prop_name = getattr(event.data, "name", None)
                    val = getattr(event.data, "value", None)
                err = getattr(event, "error", 0)
                GLib.idle_add(
                    self._on_nonblocking_property_reply,
                    query_id,
                    self._active_readback_epoch,
                    prop_name,
                    val,
                    err,
                )
        except Exception as e:
            logging.debug("Error in _on_mpv_event: %s", e)

    def _on_nonblocking_property_reply(
        self,
        query_id: int,
        epoch: int,
        prop_name: Optional[str],
        val: Any,
        err: int,
    ) -> bool:
        if getattr(self, "_disconnected", False) or getattr(self, "_shutting_down", False):
            return GLib.SOURCE_REMOVE
        if query_id != getattr(self, "_pending_readback_query_id", 0) or epoch != self._output_epoch:
            return GLib.SOURCE_REMOVE

        clean_name = prop_name
        if clean_name and clean_name.startswith("options/"):
            clean_name = clean_name[len("options/"):]

        if not clean_name:
            for p in self._active_readback_expected:
                if p not in self._active_readback_results and p not in self._active_readback_errors:
                    clean_name = p
                    break

        if clean_name:
            if err != 0:
                self._active_readback_errors[clean_name] = f"readback error code {err}"
            else:
                self._active_readback_results[clean_name] = val

        self._evaluate_readback_results(query_id, epoch)
        return GLib.SOURCE_REMOVE

    def _evaluate_readback_results(self, query_id: int, epoch: int) -> None:
        if query_id != getattr(self, "_pending_readback_query_id", 0) or epoch != self._output_epoch:
            return

        expected_props = self._active_readback_expected
        if len(self._active_readback_results) + len(self._active_readback_errors) < len(expected_props):
            return

        if self._readback_timeout_source_id is not None:
            try:
                GLib.source_remove(self._readback_timeout_source_id)
            except Exception:
                pass
            self._readback_timeout_source_id = None

        all_matched = True
        mismatch_reason = None

        for prop, expected_val in expected_props.items():
            if prop in self._active_readback_errors:
                all_matched = False
                mismatch_reason = f"readback failure: {self._active_readback_errors[prop]}"
                break
            actual_val = self._active_readback_results.get(prop)
            if not prop_matches(actual_val, expected_val):
                all_matched = False
                mismatch_reason = (
                    f"mismatched readback for '{prop}': expected {expected_val!r}, got {actual_val!r}"
                )
                break

        state_args = self._active_readback_state_args
        if all_matched:
            self._committed_state = CommittedOutputState(
                epoch=epoch,
                target_trc=state_args["expected_trc"],
                target_prim=state_args["expected_prim"],
                target_peak=state_args["expected_peak"],
                fbo_format=state_args["desired_fbo_format"],
                gdk_color_state=state_args["desired_color_state"],
                is_float=state_args["desired_is_float"],
                is_hdr=state_args["hdr_output_active"],
            )
            self._pending_epoch = None
            self._publication_suppressed = False
            self._state_gap_reason = None

            self.check_unsupported_warning()
            self.check_dovi_warning()
            self.check_force_hdr_warning()
            self._log_telemetry(
                state_args["hdr_output_active"], state_args["effective_target_peak"]
            )
            if self.on_change_cb:
                self.on_change_cb()
        else:
            self._pending_epoch = None
            self._publication_suppressed = True
            self._state_gap_reason = mismatch_reason
            logging.warning(self._state_gap_reason)
            if self.on_change_cb:
                self.on_change_cb()

    def _on_readback_timeout(
        self, epoch: int, query_id: int, timeout_sec: float
    ) -> bool:
        self._readback_timeout_source_id = None
        if getattr(self, "_disconnected", False) or getattr(self, "_shutting_down", False):
            return GLib.SOURCE_REMOVE
        if query_id != getattr(self, "_pending_readback_query_id", 0) or epoch != self._output_epoch:
            return GLib.SOURCE_REMOVE

        if self._pending_epoch == epoch:
            self._pending_epoch = None
            self._publication_suppressed = True
            missing_props = [
                p for p in self._active_readback_expected
                if p not in self._active_readback_results and p not in self._active_readback_errors
            ]
            self._state_gap_reason = (
                f"bounded readback timeout ({int(timeout_sec * 1000)}ms) for epoch {epoch}: "
                f"timed out awaiting {missing_props}"
            )
            logging.warning(self._state_gap_reason)
            if self.on_change_cb:
                self.on_change_cb()
        return GLib.SOURCE_REMOVE

    def _log_telemetry(self, hdr_output_active: bool, effective_target_peak: Any):
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
        if get_monitor_hdr_state(self._output_hint, allow_probe=False) is not True:
            return False
        if not is_surface_submission_proven():
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

    @property
    def committed_output_state(self) -> CommittedOutputState:
        return self._committed_state

    @property
    def output_epoch(self) -> int:
        return self._output_epoch

    @property
    def is_publication_suppressed(self) -> bool:
        return self._publication_suppressed

    @property
    def is_publication_pending(self) -> bool:
        return self._pending_epoch is not None

    @property
    def state_gap_reason(self) -> Optional[str]:
        return self._state_gap_reason

    def disconnect(self):
        """Disconnect GSettings and libmpv property observers."""
        self._disconnected = True
        self._shutting_down = True
        self._pending_epoch = None
        if getattr(self, "_readback_timeout_source_id", None) is not None:
            try:
                GLib.source_remove(self._readback_timeout_source_id)
            except Exception:
                pass
            self._readback_timeout_source_id = None
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
