# wayland_output_hdr.py
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
Per-output HDR state via the color-management-v1 protocol.

wayland_cm_probe.py answers "does the compositor *speak* color management?".
This module answers the follow-up question that decides picture quality:
"is the monitor actually *in* HDR mode right now?". A capable compositor
with monitor HDR switched off still converts a Rec.2100 PQ surface to SDR
itself — a plain colorimetric transform that looks worse than mpv's tone
mapping. Auto mode must therefore prefer mpv tone mapping until the output
really is HDR.

Mechanism (all on GTK's existing ``wl_display``, private event queue, same
connection-sharing pattern as wayland_cm_probe):

  1. enumerate the registry, bind ``wp_color_manager_v1`` (a no-op listener
     swallows its ``supported_*``/``done`` capability burst);
  2. for every ``GdkMonitor``, obtain GTK's own ``wl_output`` proxy via
     ``gdk_wayland_monitor_get_wl_output()`` and request
     ``get_output`` -> ``wp_color_management_output_v1``;
  3. ``get_image_description`` -> wait for ``ready`` (or ``failed``);
  4. ``get_information`` -> collect ``tf_named`` / ``primaries_named`` /
     ``luminances`` until ``done``.

Because libwayland-client dispatches events for foreign interfaces through
libffi using the ``wl_interface``/``wl_message`` tables, those tables for the
four ``wp_*`` interfaces are constructed here in ctypes. Only the request
opcodes this module actually marshals are declared (libwayland validates
``opcode < method_count`` and nothing else on the client side); event tables
are complete and exact — a wrong event signature would corrupt the dispatch.

Classification of one output (:func:`classify_hdr`):

  * transfer function ``st2084_pq`` or ``hlg``  -> HDR;
  * otherwise, ``luminances`` with ``max_lum > reference_lum`` -> HDR
    (covers a hypothetical extended-linear HDR description);
  * otherwise -> SDR.

Return contract of :func:`probe_outputs`: a dict keyed by connector name
(``DP-1``...) on success, or ``None`` whenever anything at all prevents a
definitive answer (not Wayland, no color management, libwayland or libgtk
symbols missing, dispatch failure). Callers must treat ``None`` as unknown
and change nothing — the same tri-state discipline as wayland_cm_probe.

The result is cached for a short TTL (:data:`CACHE_TTL_SECONDS`): the state
*does* change at runtime when the user toggles monitor HDR in system
settings, ``HdrController.is_hdr_active`` is evaluated per rendered frame,
and one probe costs three compositor round-trips per output. A persistent
``image_description_changed`` listener integrated into the GLib main loop is
the follow-up planned in docs/ROADMAP.md.
"""

import logging
import sys
import time
from typing import Callable, Dict, Optional

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from . import wayland_cm_probe  # noqa: F401
from . import wayland_output_hdr_sync as _sync
from .wayland_cm_probe import (
    _WL_DISPLAY_GET_REGISTRY,  # noqa: F401
    _configure_symbols,  # noqa: F401
    _get_wl_display_ptr,  # noqa: F401
    _load_libwayland,  # noqa: F401
)
from .wayland_cm_protocol import (
    _CB_HU,  # noqa: F401
    _CB_I8,  # noqa: F401
    _CB_REG_GLOBAL,  # noqa: F401
    _CB_U,  # noqa: F401
    _CB_US,  # noqa: F401
    _CB_UU,  # noqa: F401
    _CB_UUU,  # noqa: F401
    _CB_VOID,  # noqa: F401
    _CM_OUTPUT_GET_IMAGE_DESCRIPTION,  # noqa: F401
    _DESTROY,  # noqa: F401
    _DONE_CB,  # noqa: F401
    _IMG_GET_INFORMATION,  # noqa: F401
    _MGR_GET_OUTPUT,  # noqa: F401
    _TF_NAMES,  # noqa: F401
    _WL_REGISTRY_BIND,  # noqa: F401
    PRIMARIES_BT2020,  # noqa: F401
    PRIMARIES_SRGB,  # noqa: F401
    TF_BT1886,  # noqa: F401
    TF_COMPOUND_POWER_2_4,  # noqa: F401
    TF_EXT_LINEAR,  # noqa: F401
    TF_GAMMA22,  # noqa: F401
    TF_HLG,  # noqa: F401
    TF_SRGB,  # noqa: F401
    TF_ST2084_PQ,  # noqa: F401
    OutputHdrInfo,
    _CallbackListener,  # noqa: F401
    _InterfaceTable,  # noqa: F401
    _WlInterface,  # noqa: F401
    _WlMessage,  # noqa: F401
    _get_monitor_outputs,  # noqa: F401
    _make_listener,  # noqa: F401
    _marshal_destroy,  # noqa: F401
    _sig_arg_count,  # noqa: F401
    classify_hdr,  # noqa: F401
)
from .wayland_output_hdr_async import (
    STATE_DONE,  # noqa: F401
    STATE_INIT,  # noqa: F401
    STATE_WAIT_DESCRIPTIONS,  # noqa: F401
    STATE_WAIT_INFOS,  # noqa: F401
    STATE_WAIT_REGISTRY,  # noqa: F401
    WaylandOutputHdrSource,
)

from .gtk_cm_policy import CmCaps

CACHE_TTL_SECONDS = 2.0


# ──────────────────────────────────────────────────────────────
# The probe
# ──────────────────────────────────────────────────────────────


_backend_disabled = False
_backend_error_count = 0


def probe_outputs() -> Optional[Dict[str, OutputHdrInfo]]:
    return _sync.probe_outputs(facade=sys.modules[__name__])


def _probe_outputs_unsafe() -> Optional[Dict[str, OutputHdrInfo]]:
    return _sync._probe_outputs_unsafe(facade=sys.modules[__name__])


def _query_one_output(lib, display, queue, mgr, table, keep, out_ptr) -> Optional[OutputHdrInfo]:
    return _sync._query_one_output(lib, display, queue, mgr, table, keep, out_ptr)


# ──────────────────────────────────────────────────────────────
# TTL cache + public accessor
# ──────────────────────────────────────────────────────────────

_cache_value: Optional[Dict[str, OutputHdrInfo]] = None
_cache_cm_caps: Optional[CmCaps] = None
_cache_time: float = 0.0
_cache_valid: bool = False


def _update_cache(results: Optional[Dict[str, OutputHdrInfo]], cm_caps: Optional[CmCaps] = None):
    global _cache_value, _cache_cm_caps, _cache_time, _cache_valid
    _cache_value = results
    if results is None:
        _cache_cm_caps = None
    elif cm_caps is not None:
        _cache_cm_caps = cm_caps
    _cache_time = time.monotonic()
    _cache_valid = True
    try:
        from .hdr_detection import invalidate_support_decision

        invalidate_support_decision()
    except ImportError as exc:
        logging.debug("hdr_detection unavailable when invalidating support cache: %s", exc)


def invalidate(invalidate_caps: bool = True):
    global _cache_value, _cache_cm_caps, _cache_time, _cache_valid
    _cache_value = None
    if invalidate_caps:
        _cache_cm_caps = None
    _cache_time = 0.0
    _cache_valid = False


def get_cm_caps(allow_probe: bool = False) -> Optional[CmCaps]:
    global _cache_cm_caps
    if _cache_cm_caps is not None:
        return _cache_cm_caps
    if allow_probe:
        refresh_output_hdr_states()
    return _cache_cm_caps


def refresh_output_hdr_states() -> Optional[Dict[str, OutputHdrInfo]]:
    """Explicitly perform probe_outputs() and update the cache.

    Must be called from background timers or lifecycle event handlers,
    never directly from the per-frame render path.
    """
    global _cache_value, _cache_cm_caps, _cache_time, _cache_valid
    val = probe_outputs()
    _cache_value = val
    if val is None:
        _cache_cm_caps = None
    _cache_time = time.monotonic()
    _cache_valid = True
    return _cache_value


def async_refresh_output_hdr_states(
    on_complete: Optional[Callable[[Optional[Dict[str, OutputHdrInfo]]], None]] = None,
    timeout_sec: float = 2.0,
) -> Optional[GLib.Source]:
    """Asynchronously probe outputs using a non-blocking GLib.Source state machine.

    Never blocks the main thread with wl_display_roundtrip_queue. Updates the cache
    and invokes on_complete(results) upon completion.
    """
    try:
        source = WaylandOutputHdrSource(on_complete=on_complete, timeout_sec=timeout_sec)
        source.attach(None)
        return source
    except Exception as e:
        logging.debug("wayland_output_hdr: failed to create async probe source: %s", e)
        if on_complete:
            try:
                on_complete(None)
            except Exception:
                pass
        return None


def get_output_hdr_states(allow_probe: bool = False) -> Optional[Dict[str, OutputHdrInfo]]:
    """Return output HDR states from the cache.

    If allow_probe is True and the cache is expired/uninitialized, probe_outputs()
    is executed. If allow_probe is False (default, for hot render paths), only the
    existing cached value is returned without performing any blocking Wayland roundtrips.
    """
    global _cache_value, _cache_time, _cache_valid
    now = time.monotonic()
    if _cache_valid and (now - _cache_time) < CACHE_TTL_SECONDS:
        return _cache_value
    if allow_probe:
        return refresh_output_hdr_states()
    return _cache_value if _cache_valid else None


def get_monitor_hdr_state(
    connector: Optional[str] = None, allow_probe: bool = False
) -> Optional[bool]:
    """Tri-state HDR answer for one connector (or the aggregate).

    * connector known in the probe result -> that output's state;
    * no connector hint -> True if *any* output is HDR (never block a window
      that might be on the HDR screen), False only when every output is SDR;
    * probe unavailable -> None (callers change nothing).
    """
    states = get_output_hdr_states(allow_probe=allow_probe)
    if states is None:
        return None
    if connector and connector in states:
        return states[connector].hdr
    if not states:
        return None
    return any(info.hdr for info in states.values())
