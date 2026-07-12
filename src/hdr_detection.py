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


_cached_support = None


def invalidate_hdr_support_cache():
    global _cached_support
    _cached_support = None


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



def get_dovi_profile(params: dict = None, mpv_player: Any = None) -> Optional[str]:
    """
    Detect if the active video stream contains Dolby Vision metadata and return the profile string.

    Checks current track properties (`current-tracks/video/dolby-vision-profile` and
    `dolby-vision-level`) when `mpv_player` is provided, and falls back to inspecting
    colormatrix/primaries (`colormatrix == 'dolbyvision'`).

    Note: Under vo=libmpv (render API), Dolby Vision RPU processing is not supported.
    This function is strictly for informational display in diagnostics and does NOT
    trigger HDR Rec.2100 PQ pass-through for Profile 5 streams.
    """
    profile = None
    level = None

    if mpv_player is not None:
        try:
            def _query_prop(name: str):
                if hasattr(mpv_player, "_get_property"):
                    res = mpv_player._get_property(name)
                    if res is not None:
                        return res
                if hasattr(mpv_player, "get_property"):
                    res = mpv_player.get_property(name)
                    if res is not None:
                        return res
                try:
                    return mpv_player[name]
                except Exception:
                    return None

            p = _query_prop("current-tracks/video/dolby-vision-profile")
            if not p:
                tracks = _query_prop("track-list") or []
                if isinstance(tracks, list):
                    for t in tracks:
                        if isinstance(t, dict) and t.get("type") == "video" and t.get("selected"):
                            p = t.get("dolby-vision-profile")
                            level = t.get("dolby-vision-level")
                            break
            else:
                level = _query_prop("current-tracks/video/dolby-vision-level")

            if p is not None and str(p).strip() and str(p).strip() != "0":
                profile = str(p).strip().lstrip("0") or "0"
        except Exception:
            pass

    if not profile and params and isinstance(params, dict):
        for key in ("dolby-vision-profile", "dovi-profile"):
            val = params.get(key)
            if val is not None and str(val).strip() and str(val).strip() != "0":
                profile = str(val).strip().lstrip("0") or "0"
                break

    if profile:
        if level is not None and str(level).strip() and str(level).strip() != "0":
            return f"{profile} (Level {str(level).strip()})"
        return profile

    if params and isinstance(params, dict):
        colormatrix = str(params.get("colormatrix", "")).lower()
        primaries = str(params.get("primaries", "")).lower()
        if colormatrix == "dolbyvision" or "dolbyvision" in colormatrix or primaries == "dolbyvision" or "dolbyvision" in primaries:
            return "5 (colormatrix: dolbyvision)"

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
    return "Wayland compositor does not support HDR/color management"

