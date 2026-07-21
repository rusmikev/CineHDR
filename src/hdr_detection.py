# hdr_detection.py
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
HDR content detection and Wayland/GTK capability checking module.

This module isolates the logic required to determine whether:
1. The playback stream contains HDR video metadata (transfer characteristics, peak luma).
2. The current desktop environment, windowing system (Wayland), and GTK version
   support high dynamic range (HDR) color signaling and 16-bit floating point buffers.
"""

import gi
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk
from typing import Optional, Any

from . import wayland_cm_probe
from . import wayland_output_hdr


_cached_support = None


def invalidate_hdr_support_cache():
    global _cached_support
    _cached_support = None
    # The compositor capability probe and the per-output HDR state cache
    # each cache independently; keep all three in lockstep so a monitor
    # hot-plug / re-realize re-evaluates everything.
    wayland_cm_probe.invalidate()
    wayland_output_hdr.invalidate()


def get_monitor_hdr_state(connector: Optional[str] = None) -> Optional[bool]:
    """Tri-state: is the monitor (or any monitor) actually in HDR mode?

    True/False come from reading the output's image description through
    wp_color_manager_v1 (PQ/HLG transfer function, or peak > reference
    luminance); None means the state could not be determined and callers
    must not change their behaviour. Consulted by HdrController to keep
    auto mode on mpv tone mapping while monitor HDR is switched off, and by
    the diagnostics dialog.
    """
    try:
        return wayland_output_hdr.get_monitor_hdr_state(connector)
    except Exception:
        return None


def get_compositor_cm_support() -> Optional[bool]:
    """Tri-state: does the Wayland compositor advertise color management?

    True/False come from enumerating the compositor's registry globals
    (wp_color_manager_v1 / xx_color_manager_v4); None means the probe could
    not run (e.g. not Wayland, or libwayland-client unavailable) and the
    answer is unknown. Exposed for the diagnostics dialog.
    """
    return wayland_cm_probe.probe_color_management()


def check_hdr_support() -> bool:
    global _cached_support
    if _cached_support is not None:
        return _cached_support
    _cached_support = _check_hdr_support_uncached()
    return _cached_support


def _check_hdr_support_uncached() -> bool:
    """
    Check if the current desktop session and GTK runtime support HDR rendering.
    """
    try:
        import os
        if os.environ.get("GSK_RENDERER", "").lower() == "gl":
            return False
        if not hasattr(Gdk, "ColorState") or not hasattr(Gdk.ColorState, "get_rec2100_pq"):
            return False
        if not hasattr(Gdk, "MemoryFormat") or not hasattr(Gdk.MemoryFormat, "R16G16B16A16_FLOAT"):
            return False

        display = Gdk.Display.get_default()
        if not display:
            return False

        display_name = getattr(display, "get_name", lambda: "")()
        is_wayland = ("wayland" in str(display_name).lower() or "wayland" in display.__class__.__name__.lower())
        if not is_wayland:
            return False

        # Verify that the display/compositor supports RGBA / color management
        if hasattr(display, "is_composited") and not display.is_composited():
            return False
        if hasattr(display, "is_rgba") and not display.is_rgba():
            return False

        # Check if dmabuf formats are available (indicates modern Wayland buffer sharing and protocol support)
        if hasattr(display, "get_dmabuf_formats"):
            dmabuf = display.get_dmabuf_formats()
            if dmabuf is not None and hasattr(dmabuf, "get_n_formats") and dmabuf.get_n_formats() == 0:
                return False

        # Ask the compositor itself: without a color-management global
        # (wp_color_manager_v1 / xx_color_manager_v4) GTK cannot hand a
        # Rec.2100 PQ surface to the compositor and will silently convert
        # PQ -> sRGB with a plain colorimetric transform, which looks worse
        # than mpv's tone mapping. Only a definitive "no" from the registry
        # blocks HDR; an inconclusive probe (None) preserves the previous
        # heuristic behaviour.
        if wayland_cm_probe.probe_color_management() is False:
            return False

        return True
    except Exception:
        return False


def is_hdr_content(params: dict) -> bool:
    """
    Determine whether the video stream currently playing in libmpv is HDR.

    Criteria evaluated from mpv 'video-params' / 'video-out-params':
    - Transfer function (gamma / trc): PQ (st2084), HLG, or S-Log curves.
    - Signal peak luminance (sig-peak): Greater than 1.0 (relative to SDR reference 100 nits).

    Note: Primaries being BT.2020 alone without HDR gamma or peak > 1.0 is NOT considered
    HDR (prevents false triggering on WCG SDR content).

    Args:
        params (dict): Dictionary of video parameters from mpv property observer.

    Returns:
        bool: True if stream requires HDR tone mapping / color state, False otherwise.
    """
    if not params or not isinstance(params, dict):
        return False

    gamma = params.get("gamma", "")
    sig_peak = params.get("sig-peak", 1.0)
    try:
        sig_peak = float(sig_peak) if sig_peak is not None else 1.0
    except (ValueError, TypeError):
        sig_peak = 1.0

    hdr_gammas = ("pq", "hlg", "st2084", "slog", "slog2", "slog3")
    if gamma in hdr_gammas or sig_peak > 1.0:
        return True
    return False



# ──────────────────────────────────────────────────────────────
# Dolby Vision
# ──────────────────────────────────────────────────────────────
#
# Profiles whose picture cannot be rendered correctly through the libmpv render
# API, i.e. for which HDR pass-through must be refused:
#
#   * Profile 5 is single-layer IPT (IPTPQc2). Showing it correctly requires the
#     RPU reshaping, which only libplacebo implements (mpv --vo=gpu-next).
#     CineHDR renders through the libmpv render API (vo=libmpv), which uses
#     mpv's legacy GPU renderer: that renderer explicitly reverts the Dolby
#     Vision mapping (mp_image_params_restore_dovi_mapping(), video/out/gpu/
#     video.c) and its YUV->RGB matrix treats DOLBYVISION as "not supported",
#     falling back to BT.2020-NC (video/csputils.c). The resulting RGB is wrong,
#     so tagging it Rec.2100 PQ would show broken colors *and* switch the
#     monitor into HDR mode. mpv's SDR tone mapping is the lesser evil.
#
#   * Profiles 7 and 8 carry an HDR10-compatible base layer: after the same
#     revert mpv renders correct BT.2020 + PQ and only the dynamic metadata is
#     lost. They must keep working in HDR.
DOVI_UNSUPPORTED_PROFILES = (5,)


def _query_mpv_prop(mpv_player: Any, name: str) -> Any:
    """
    Read a single mpv property; return None if unset, unavailable or erroring.

    Each accessor is attempted independently: python-mpv raises when a property
    is unavailable (the normal case for `dolby-vision-profile` on non-DV files),
    and a raising first accessor must not stop the remaining ones from running.
    """
    if mpv_player is None:
        return None

    for getter in ("_get_property", "get_property"):
        fn = getattr(mpv_player, getter, None)
        if not callable(fn):
            continue
        try:
            res = fn(name)
        except Exception:
            continue
        if res is not None:
            return res

    try:
        return mpv_player[name]
    except Exception:
        return None


def _coerce_profile(value: Any) -> Optional[int]:
    """Return a positive int from an mpv property value, else None."""
    try:
        num = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def get_dovi_info(params: dict = None, mpv_player: Any = None) -> Optional[dict]:
    """
    Describe the Dolby Vision metadata of the active video track.

    Returns None when the stream carries no Dolby Vision metadata, otherwise:

        {"profile": int | None,    # None when the profile could not be read
         "level": int | None,
         "unsupported": bool}      # True only for a *confirmed* unsupported profile

    The profile number is read from mpv's track properties
    (`current-tracks/video/dolby-vision-profile`), the only reliable source:
    `video-params` never carries it.

    A colormatrix of "dolbyvision" is used only as a fallback *presence* signal.
    libplacebo's pl_map_avdovi_metadata() sets colormatrix=dolbyvision,
    primaries=bt.2020 and transfer=pq for every single-layer Dolby Vision frame,
    so the fingerprint matches profile 5 and profile 8 alike and cannot tell them
    apart. When the profile is unknown the stream is reported as detected but
    unidentified and the pipeline is left alone: refusing HDR on a guess would
    needlessly downgrade a perfectly playable profile 8 stream.
    """
    level = None
    profile = _coerce_profile(
        _query_mpv_prop(mpv_player, "current-tracks/video/dolby-vision-profile")
    )

    if profile is None:
        tracks = _query_mpv_prop(mpv_player, "track-list")
        if isinstance(tracks, (list, tuple)):
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                if track.get("type") == "video" and track.get("selected"):
                    profile = _coerce_profile(track.get("dolby-vision-profile"))
                    level = _coerce_profile(track.get("dolby-vision-level"))
                    break
    else:
        level = _coerce_profile(
            _query_mpv_prop(mpv_player, "current-tracks/video/dolby-vision-level")
        )

    if profile is not None:
        return {
            "profile": profile,
            "level": level,
            "unsupported": profile in DOVI_UNSUPPORTED_PROFILES,
        }

    if isinstance(params, dict):
        if "dolbyvision" in str(params.get("colormatrix", "")).lower():
            return {"profile": None, "level": None, "unsupported": False}

    return None


def get_hdr_unsupported_reason(display: Gdk.Display = None) -> str:
    """
    Return a descriptive string explaining why HDR is not supported on this system.

    Args:
        display (Gdk.Display, optional): GTK display object to inspect.

    Returns:
        str: Human-readable explanation of missing HDR requirements.
    """
    import os
    if os.environ.get("GSK_RENDERER", "").lower() == "gl":
        return "GSK_RENDERER=gl forces legacy 8-bit OpenGL rendering without HDR support"
    if not hasattr(Gdk, "ColorState") or not hasattr(Gdk.ColorState, "get_rec2100_pq") or not hasattr(Gdk, "MemoryFormat") or not hasattr(Gdk.MemoryFormat, "R16G16B16A16_FLOAT"):
        return "Gdk.ColorState is not available (requires GTK >= 4.16)"
    if not display:
        display = Gdk.Display.get_default()
    if not display:
        return "No active Gdk.Display found"
    display_name = getattr(display, "get_name", lambda: "")()
    is_wayland = ("wayland" in str(display_name).lower() or "wayland" in display.__class__.__name__.lower())
    if not is_wayland:
        return f"HDR signaling is not supported under {display.__class__.__name__} (requires Wayland)"
    if wayland_cm_probe.probe_color_management() is False:
        return (
            "Wayland compositor does not advertise a color management protocol "
            "(wp_color_manager_v1) — HDR pass-through is impossible, using mpv tone mapping"
        )
    return "Wayland compositor does not support HDR/color management"

