# hdr_detection.py
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
HDR content detection and Wayland/GTK capability checking module.

This module isolates the logic required to determine whether:
1. The playback stream contains HDR video metadata (transfer characteristics, peak luma).
2. The current desktop environment, windowing system (Wayland), and GTK version
   support high dynamic range (HDR) color signaling and 16-bit floating point buffers.
"""

import gi
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk


def check_hdr_support() -> bool:
    """
    Check if the current desktop session and GTK runtime support HDR rendering.

    HDR output in GTK 4 requires:
    - GTK >= 4.16 with Gdk.ColorState support (specifically get_rec2100_pq).
    - 16-bit floating-point memory format (Gdk.MemoryFormat.R16G16B16A16_FLOAT).
    - Wayland display server (X11 protocol lacks HDR color signaling extensions).
    - Compositor support for RGBA visual channels and dmabuf buffer sharing.

    Returns:
        bool: True if HDR rendering is safely supported by the system, False otherwise.
    """
    try:
        if not hasattr(Gdk.ColorState, "get_rec2100_pq"):
            return False
        if not hasattr(Gdk.MemoryFormat, "R16G16B16A16_FLOAT"):
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



def get_hdr_unsupported_reason(display: Gdk.Display = None) -> str:
    """
    Return a descriptive string explaining why HDR is not supported on this system.

    Args:
        display (Gdk.Display, optional): GTK display object to inspect.

    Returns:
        str: Human-readable explanation of missing HDR requirements.
    """
    if not hasattr(Gdk.ColorState, "get_rec2100_pq") or not hasattr(Gdk.MemoryFormat, "R16G16B16A16_FLOAT"):
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

