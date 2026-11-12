# video_widget_lifecycle.py
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

"""Display, monitor, realize, and context-restore helpers for MpvVideoWidget."""

import logging
from typing import Any, Callable

from .gl_bindings import (
    GL_RENDERER,
    GL_SHADING_LANGUAGE_VERSION,
    GL_VENDOR,
    GL_VERSION,
)


def update_cached_hdr_support(widget: Any, check_hdr_support_fn: Callable[..., bool]) -> None:
    """Refresh the widget's cached HDR support state without blocking probes."""
    from .hdr_detection import invalidate_hdr_support_cache

    invalidate_hdr_support_cache(invalidate_compositor=False)
    old_support = getattr(widget, "_cached_hdr_support", None)
    widget._cached_hdr_support = check_hdr_support_fn(allow_probe=False)
    widget._cached_hdr_support_valid = True
    widget._push_output_hint()
    widget._update_cached_scale_and_limits()
    if hasattr(widget, "hdr_controller"):
        widget.hdr_controller.request_monitor_probe()
        if old_support is not None and old_support != widget._cached_hdr_support:
            widget.hdr_controller.apply_hdr_settings()
            widget.queue_draw()


def update_cached_scale_and_limits(widget: Any) -> None:
    """Cache current monitor scale factor and physical pixel bounds."""
    scale = (
        float(widget.props.scale_factor)
        if hasattr(widget, "props") and hasattr(widget.props, "scale_factor")
        else 1.0
    )
    max_w = 0
    max_h = 0
    try:
        native = widget.get_native()
        surface = native.get_surface() if native and hasattr(native, "get_surface") else None
        display = widget.get_display()
        if surface and display and hasattr(display, "get_monitor_at_surface"):
            monitor = display.get_monitor_at_surface(surface)
            if monitor:
                if hasattr(monitor, "get_scale"):
                    scale = float(monitor.get_scale())
                elif hasattr(monitor, "get_scale_factor"):
                    scale = float(monitor.get_scale_factor())
                geom = monitor.get_geometry()
                if geom and geom.width > 0 and geom.height > 0:
                    max_w = int(round(geom.width * scale))
                    max_h = int(round(geom.height * scale))
    except Exception:
        pass
    widget._cached_scale = scale
    widget._cached_max_width = max_w
    widget._cached_max_height = max_h


def handle_hdr_controller_change(widget: Any, check_hdr_support_fn: Callable[..., bool]) -> None:
    """Consume a completed controller probe and schedule redraw if epoch changed."""
    from .hdr_detection import invalidate_support_decision

    invalidate_support_decision()
    widget._cached_hdr_support = check_hdr_support_fn(allow_probe=False)
    widget._cached_hdr_support_valid = True

    controller = getattr(widget, "hdr_controller", None)
    if controller:
        if getattr(controller, "is_publication_suppressed", False):
            widget.queue_draw()
            return
        committed = getattr(controller, "committed_output_state", None)
        if committed is not None:
            if getattr(widget, "_committed_render_epoch", None) != committed.epoch:
                widget._schedule_render(force_redraw=True)
    widget.queue_draw()


def push_output_hint(widget: Any) -> None:
    """Forward the active monitor connector name to HdrController."""
    if not hasattr(widget, "hdr_controller"):
        return
    connector = None
    try:
        native = widget.get_native()
        surface = native.get_surface() if native and hasattr(native, "get_surface") else None
        display = widget.get_display()
        if surface and display and hasattr(display, "get_monitor_at_surface"):
            monitor = display.get_monitor_at_surface(surface)
            if monitor and hasattr(monitor, "get_connector"):
                connector = monitor.get_connector()
    except Exception:
        connector = None
    try:
        widget.hdr_controller.set_output_hint(connector)
    except Exception:
        pass


def reset_render_session_state(widget: Any) -> None:
    """Clear facts owned by any previous GL context before touching a new one."""
    widget.render_backend_active = None
    widget.render_backend_fallback_reason = None
    widget.render_session_status = "not-initialized"
    widget.render_failure_reason = None
    widget.render_runtime = {}
    widget.render_target_format = None
    widget.render_target_depth = None
    widget.render_color_state = None
    widget._render_restart_required_reason = None


def reset_realized_frame_flags(widget: Any) -> None:
    """Reset per-realization frame tracking flags after make_current succeeds."""
    widget._shutting_down = False
    widget._render_failed = False
    widget._first_frame_logged = False
    widget._first_media_frame_logged = False
    widget._first_media_frame_exported = False
    widget._exported_media_generation = None
    widget._committed_render_epoch = None


def connect_realize_signals(widget: Any) -> None:
    """Connect monitor-list and surface-monitor signals for HDR cache updates."""
    display = widget.get_display()
    if display and hasattr(display, "get_monitors"):
        monitors = display.get_monitors()
        if monitors and hasattr(monitors, "connect"):
            try:
                widget._monitor_signal_id = monitors.connect(
                    "items-changed", widget._update_cached_hdr_support
                )
                widget._connected_monitors = monitors
            except Exception:
                pass

    try:
        native = widget.get_native()
        if native and hasattr(native, "get_surface"):
            surface = native.get_surface()
            if surface and hasattr(surface, "connect"):
                widget._connected_surface = surface
                for signal_name in ("enter-monitor", "leave-monitor"):
                    widget._surface_signal_ids.append(
                        surface.connect(signal_name, widget._update_cached_hdr_support)
                    )
    except Exception:
        pass


def record_realized_runtime(
    widget: Any,
    mpv_mod: Any,
    backend_state: Any,
    *,
    render_runtime_diagnostics_fn: Callable[..., dict[str, str]],
    get_gl_string_fn: Callable[[int], str],
) -> None:
    """Record active backend state, GL runtime strings, and fallback notice."""
    widget.render_backend_fallback_reason = backend_state.fallback_reason
    widget.render_session_status = (
        "startup-fallback" if backend_state.fallback_reason else "active"
    )
    logging.info(
        "libmpv render backend requested=%s active=%s fallback_reason=%s",
        backend_state.requested.value,
        backend_state.active,
        backend_state.fallback_reason or "none",
    )
    runtime = render_runtime_diagnostics_fn(mpv_mod, widget.mpv)
    runtime.update(
        {
            "gl_vendor": get_gl_string_fn(GL_VENDOR),
            "gl_renderer": get_gl_string_fn(GL_RENDERER),
            "gl_version": get_gl_string_fn(GL_VERSION),
            "glsl_version": get_gl_string_fn(GL_SHADING_LANGUAGE_VERSION),
        }
    )
    widget.render_runtime = runtime
    logging.info(
        "Render runtime libmpv=%s mpv=%s libplacebo=%s ffmpeg=%s "
        "configuration=%s OpenGL=%s / %s / %s",
        runtime["libmpv_path"],
        runtime["mpv_version"],
        runtime["libplacebo_version"],
        runtime["ffmpeg_version"],
        runtime["mpv_configuration"],
        runtime["gl_vendor"],
        runtime["gl_renderer"],
        runtime["gl_version"],
    )
    if backend_state.fallback_reason and widget._window:
        app = getattr(widget._window, "app", None)
        notify = getattr(app, "notify_render_fallback_once", None)
        if notify:
            notify(widget._window, backend_state.fallback_reason)


def capture_video_selection_for_context_restore(
    widget: Any, mpv_prop_fn: Callable[..., Any]
) -> None:
    """Remember the active video selection before libmpv tears its VO down."""
    widget._video_selection_for_context_restore = None
    try:
        current_video = getattr(widget.mpv, "vid", None)
        if current_video in (False, None, "no", "auto") or (
            isinstance(current_video, (int, str)) and str(current_video).isdigit() is False
        ):
            return
        configured_val = mpv_prop_fn(widget.mpv, "options/vid", "")
        configured_video = (
            str(configured_val).strip().lower() if configured_val is not None else ""
        )
        widget._video_selection_for_context_restore = (
            "auto" if configured_video in ("auto", "-1") else str(int(current_video))
        )
    except Exception:
        logging.exception("Failed to capture the video track before context recreation")


def handle_first_frame_confirmed(widget: Any, idle_add_once_fn: Callable[..., Any]) -> None:
    """Schedule window first-video-frame callback when a frame is confirmed."""
    window = getattr(widget, "_window", None)
    if window and hasattr(window, "on_first_video_frame"):
        try:
            idle_add_once_fn(window.on_first_video_frame)
        except Exception:
            logging.exception("Failed to schedule first video frame notification")
