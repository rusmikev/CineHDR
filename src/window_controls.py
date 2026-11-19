# window_controls.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Playback bar controls, volume, loop state, tooltip, and thumbnail preview for CineWindow."""

import bisect
from gettext import gettext as _
from typing import Any, cast

import gi
import mpv

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from .mpv_gl_area import ThumbPreviewGLArea
from .utils import format_time, get_window_w_h


def _setup_tooltip_layers(win: Any) -> None:
    win.popover_content_box = Gtk.Box()
    win.popover_content_box.props.orientation = Gtk.Orientation.VERTICAL

    win.time_tooltip_label = Gtk.Label()
    win.time_tooltip_label.set_use_markup(True)
    win.time_tooltip_label.set_justify(Gtk.Justification.CENTER)
    win.time_tooltip_label.add_css_class("numeric")

    def create_layer_and_revealer(child, margin_bottom):
        layer = Gtk.Fixed(
            valign=Gtk.Align.END,
            margin_bottom=margin_bottom,
            can_focus=False,
            can_target=False,
        )
        revealer = Gtk.Revealer(
            css_name="time-tooltip",
            transition_duration=175,
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
        )
        revealer.set_child(child)
        layer.put(revealer, 0, 0)
        win.video_overlay.add_overlay(layer)
        return layer, revealer

    win.tooltip_label_layer, win.tooltip_label_revealer = (
        create_layer_and_revealer(win.time_tooltip_label, 38)
    )
    win.thumb_frame = Gtk.Frame()
    win.tooltip_thumb_layer, win.tooltip_thumb_revealer = (
        create_layer_and_revealer(win.thumb_frame, 72)
    )


def _setup_progress_controllers(win: Any) -> None:
    progress_hover = Gtk.EventControllerMotion()
    progress_hover.connect("enter", win._set_time_tooltip)
    progress_hover.connect("motion", win._on_progress_motion)
    progress_hover.connect("leave", win._hide_time_tooltip)
    win.video_progress_scale.add_controller(progress_hover)

    prog_mid_click = Gtk.GestureClick(button=2)
    prog_mid_click.connect("pressed", win._go_to_chapter_start)
    win.video_progress_scale.add_controller(prog_mid_click)

    for c in win.video_progress_scale.observe_controllers():
        if isinstance(c, Gtk.GestureDrag):
            c.connect("drag-begin", win._on_progress_pressed)
            c.connect("drag-end", win._on_progress_released)
        if isinstance(c, Gtk.GestureLongPress):
            c.connect("pressed", lambda *a: setattr(win, "_prog_fine_tune", True))
            c.connect("end", lambda *a: setattr(win, "_prog_fine_tune", False))

    ecs_flags = Gtk.EventControllerScrollFlags
    progress_ecs = Gtk.EventControllerScroll.new(ecs_flags.VERTICAL)
    progress_ecs.connect("scroll", win._on_progress_scroll)
    win.video_progress_scale.add_controller(progress_ecs)

    volume_ecs = Gtk.EventControllerScroll.new(ecs_flags.VERTICAL)
    win.volume_scale.add_controller(volume_ecs)
    volume_ecs.connect("scroll", win._on_mouse_scroll_volume)


def setup_playback_control_widgets(win: Any) -> None:
    """Configure playback control bars, sliders, tooltips, and popover autohide hooks."""
    from . import window as win_mod

    for widget in (
        win.controls_wrap_box,
        win.volume_box,
        win.volume_scale,
        win.progress_box,
        win.video_progress_scale,
        win.time_elapsed_label,
    ):
        widget.set_direction(Gtk.TextDirection.LTR)

    max_vol = cast(int, win.mpv.volume_max)
    win.volume_scale_adj.set_upper(max_vol)

    win.mute_handler_id = win.mute_toggle_btn.connect(
        "toggled",
        lambda btn: win.mpv.command_async("set", "mute", btn.get_active()),
    )

    vol_mid_click = Gtk.GestureClick(button=2)
    vol_mid_click.connect("pressed", lambda *a: win.mpv.command_async("cycle", "mute"))
    win.volume_menu_btn.add_controller(vol_mid_click)

    win.fullscreen_btn.connect(
        "clicked", lambda *a: win.mpv.command_async("cycle", "fullscreen")
    )

    vc_adj = win.volume_scale_adj
    win.volume_handler_id = win.volume_scale.connect(
        "value-changed",
        lambda *a: win.mpv.command_async("set", "volume", vc_adj.props.value),
    )

    if max_vol > 100:
        win.volume_scale.add_mark(100.0, Gtk.PositionType.BOTTOM, None)

    win.ab_loop_btn.connect("toggled", win._on_ab_loop_btn_toggled)
    win.loop_btn.connect("toggled", win._on_loop_toggled)
    win.video_progress_adj.connect("value-changed", win._on_progress_adjusted)

    _setup_tooltip_layers(win)
    win._set_time_margin()
    win._set_time_tooltip()
    _setup_progress_controllers(win)

    buttons = (
        win.primary_menu_btn,
        win.open_menu_btn,
        win.options_menu_btn,
        win.hdr_menu_btn,
        win.volume_menu_btn,
        win.subtitles_menu_btn,
        win.audio_tracks_menu_btn,
        win.video_tracks_menu_btn,
        win.chapters_menu_btn,
    )
    for btn in buttons:
        popover = btn.props.popover
        popover.connect("closed", win.hide_ui_timeout)
        if (
            btn == win.subtitles_menu_btn
            or btn == win.audio_tracks_menu_btn
            or btn == win.video_tracks_menu_btn
            or btn == win.chapters_menu_btn
        ):
            scrolled_w = popover.get_first_child().get_first_child()
            scrolled_w.set_max_content_height(422)
        if btn == win.open_menu_btn:

            def on_popv_closed(*args):
                if win_mod.is_same_playlist(win.mpv.playlist):
                    win.mpv.write_watch_later_config()

            popover.connect("closed", on_popv_closed)


def set_window_size_and_present(
    win: Any, width: float = 1920, height: float = 1080
) -> None:
    """Scale small videos up, apply target default window size, and present once."""
    scale_tiers = (
        (144, 1.6666),
        (240, 1.50),
        (360, 1.3333),
        (480, 1.15),
    )

    for threshold, multiplier in scale_tiers:
        if min(width, height) <= threshold:
            width *= multiplier
            height *= multiplier
            break

    new_w, new_h = get_window_w_h(win, width, height)
    win.set_default_size(new_w, new_h)
    win.present()
    win._presented = True


def setup_thumb_preview(win: Any) -> None:
    """Create or update thumbnail preview GLArea size and loaded media."""
    from . import window as win_mod

    if not win.thumb_area:
        win.thumb_area = ThumbPreviewGLArea(win.mpv.hwdec)
        win.thumb_frame.set_child(win.thumb_area)
        win.thumb_area.realize()

    v_width, v_height = 1280, 720
    try:
        if win.mpv.vid:
            win.mpv.wait_for_property("width", timeout=2)
            win.mpv.wait_for_property("height", timeout=2)
            v_width = cast(int, win.mpv.width)
            v_height = cast(int, win.mpv.height)
    except Exception:
        win_mod.logger.exception("Failed to get video w/h")

    if v_width >= v_height:
        width = 200
        height = int((v_height / v_width) * width)
        if height == width:
            width, height = 168, 168
    else:
        height = 168
        width = int((v_width / v_height) * height)

    win._thumb_w = width
    win.thumb_area.set_size_request(width, height)
    area = win.thumb_area
    area.stop() if win._is_audio else area.load_file(win._video_path)


def hide_time_tooltip(win: Any) -> None:
    win._prev_reveal = False
    win.tooltip_thumb_revealer.set_reveal_child(False)
    win.tooltip_label_revealer.set_reveal_child(False)


def set_time_tooltip(win: Any) -> None:
    win._prev_prog_motion_xy = (-1, -1)
    win._width = win.get_width()
    win._prog_width = win.video_progress_scale.get_width()
    win._prev_reveal = False


def move_time_tooltip(win: Any, revealer: Any, layer: Gtk.Fixed, x: float, tooltip_w: float) -> None:
    x_pos = max(0, min(x - (tooltip_w / 2) + 23, win._width - tooltip_w))
    layer.move(revealer, x_pos, 0)


def on_progress_motion(win: Any, x: float, y: float) -> None:
    from . import window as win_mod

    if (x, y) == win._prev_prog_motion_xy:
        return
    win._prev_prog_motion_xy = (x, y)

    if win._prog_fine_tune:
        win._hide_time_tooltip()
        return

    show_thumb = win.thumb_area is not None and not win._is_audio
    if not win._prev_reveal:
        win.tooltip_thumb_revealer.set_reveal_child(show_thumb)
        win.tooltip_label_revealer.set_reveal_child(True)
        win._prev_reveal = True

    if win._prog_width <= 0:
        return

    percentage = max(0, min(1, x / win._prog_width))
    win._hover_time = percentage * win._duration

    title = None
    if win._chapters:
        idx = bisect.bisect_right(win.chapter_times, win._hover_time) - 1
        if idx >= 0:
            win._curr_chapter_time = win.chapter_times[idx]
            title = win.chapter_titles[idx]
    else:
        win._curr_chapter_time = None

    time_str = format_time(win._hover_time)
    text = f"{time_str} ‐ <b>{title}</b>" if title else time_str
    win.time_tooltip_label.set_markup(text)
    label_w = win.tooltip_label_revealer.get_preferred_size()[1].width

    move_time_tooltip(win, win.tooltip_label_revealer, win.tooltip_label_layer, x, label_w)

    if win.thumb_area is None:
        return

    move_time_tooltip(
        win, win.tooltip_thumb_revealer, win.tooltip_thumb_layer, x, win._thumb_w + 12
    )
    win_mod.idle_add_once(win.thumb_area.seek, win._hover_time)


def on_progress_scroll(controller: Any, dy: float, scale: Gtk.Scale) -> bool:
    event: Gdk.ScrollEvent = controller.get_current_event()
    state = event.get_modifier_state()

    if state & Gdk.ModifierType.CONTROL_MASK:
        return True

    direction: Gdk.ScrollDirection = event.get_direction()
    rel_dir: Gdk.ScrollRelativeDirection = event.get_relative_direction()  # type: ignore
    is_natural: bool = rel_dir == Gdk.ScrollRelativeDirection.INVERTED  # type: ignore
    step = dy if direction == Gdk.ScrollDirection.SMOOTH else dy * 10
    if is_natural:
        step = -step

    adj = scale.get_adjustment()
    adj.set_value(adj.get_value() - step)
    return True


def update_volume_icon(win: Any) -> None:
    volume = cast(float, win.mpv.volume)
    is_muted = win.mpv.mute

    if is_muted or volume == 0:
        icon = "cine-volume-mute-symbolic"
    elif volume < 33:
        icon = "cine-volume-low-symbolic"
    elif volume < 66:
        icon = "cine-volume-mid-symbolic"
    elif volume <= 100.5:
        icon = "cine-volume-max-symbolic"
    else:
        icon = "cine-volume-overamp-symbolic"

    win.volume_menu_btn.props.icon_name = icon


def toggle_elapsed_remaining(win: Any) -> None:
    from . import window as win_mod

    win._show_remaining = not win._show_remaining
    win_mod.settings.set_boolean("show-remaining", win._show_remaining)
    pos = float(win.mpv.time_pos or 0)
    win._update_progress(pos, update_bar=False)
    win._set_time_margin()


def update_progress(win: Any, curr_time: float, update_bar: bool = True) -> None:
    curr_time = round(curr_time, 1)

    if update_bar and curr_time == win._prev_prog_time:
        return

    if update_bar:
        win.video_progress_adj.handler_block_by_func(win._on_progress_adjusted)
        win.video_progress_adj.props.value = curr_time
        win.video_progress_adj.handler_unblock_by_func(win._on_progress_adjusted)

    try:
        if win._show_remaining:
            duration = float(win.mpv.duration or 0)
            remaining = (duration - curr_time) if duration > curr_time else 0
            win.time_elapsed_label.props.label = f"-{format_time(remaining)}"
        else:
            win.time_elapsed_label.props.label = format_time(curr_time)
    except mpv.ShutdownError:
        pass

    win._prev_prog_time = curr_time


def update_play_pause_icon(win: Any, paused: bool) -> None:
    if win._shutting_down:
        return
    play = "cine-playback-start-symbolic"
    pause = "cine-playback-pause-symbolic"

    btn_icon = play if paused else pause
    win.play_pause_btn.set_icon_name(btn_icon)

    text = _("Play") if paused else _("Pause")
    win.play_pause_btn.update_property([Gtk.AccessibleProperty.LABEL], [text])

    win.icon_indicator.props.icon_name = pause if paused else play
    win._show_icon_indicator()
    win._mpris.update_playback_status(paused)


def update_duration(win: Any, duration: float) -> None:
    win.time_total_label.set_text(format_time(duration))

    if duration == 0:
        win.video_progress_scale.set_can_target(False)
        win.video_progress_scale.set_can_focus(False)
        win._hide_time_tooltip()
        return

    win.video_progress_scale.set_can_target(True)
    win.video_progress_scale.set_can_focus(True)
    win.video_progress_adj.set_upper(duration)

    if duration >= 86400:
        chars = 10
    elif duration >= 3600:
        chars = 7
    elif duration >= 600:
        chars = 6
    else:
        chars = 5

    win.time_elapsed_label.set_width_chars(chars)


def on_progress_pressed(win: Any) -> None:
    from . import window as win_mod

    try:
        win._playing_on_press = not win.mpv.pause
        if win._playing_on_press:
            win.skip_pause_obs_count += 1
            win.mpv.command_async("set", "pause", "yes")
    except Exception:
        win_mod.logger.exception("_on_progress_pressed failed")
        win.skip_pause_obs_count = 0


def on_progress_released(win: Any) -> None:
    from . import window as win_mod

    try:
        if win._playing_on_press:
            win.skip_pause_obs_count += 1
            win._playing_on_press = False
            win.mpv.command_async("set", "pause", "no")
    except Exception:
        win_mod.logger.exception("_on_progress_released failed")
        win.skip_pause_obs_count = 0


def on_ab_loop_btn_toggled(win: Any, button: Any) -> None:
    if not button or not button.get_active():
        win.mpv.ab_loop_a = False
        win.mpv.ab_loop_b = False
    else:
        win.mpv.command_async("ab-loop")


def update_loop_state(win: Any) -> None:
    from . import window as win_mod

    icon_loop = "cine-playlist-repeat-symbolic"
    icon_loop_file = "cine-repeat-file-symbolic"
    is_file = win.mpv.loop_file
    is_playlist = win.mpv.loop_playlist

    if is_playlist:
        win._loop_mode = "playlist"
        icon, tooltip, active = icon_loop, _("Loop Playlist"), True
    elif is_file:
        win._loop_mode = "file"
        icon, tooltip, active = icon_loop_file, _("Loop File"), True
    else:
        win._loop_mode = "off"
        icon, tooltip, active = icon_loop, _("Loop"), False

    win_mod.settings.set_string("loop-state", win._loop_mode)

    win.loop_btn.handler_block_by_func(win._on_loop_toggled)
    win.loop_btn.set_active(active)
    win.loop_btn.handler_unblock_by_func(win._on_loop_toggled)

    win.loop_btn.set_icon_name(icon)
    win.loop_btn.set_tooltip_text(tooltip)


def on_loop_toggled(win: Any) -> None:
    next_modes = {"off": "playlist", "playlist": "file", "file": "off"}
    target_mode = next_modes[win._loop_mode]
    if target_mode == "playlist":
        win.mpv.loop_file = "no"
        win.mpv.loop_playlist = "inf"
    elif target_mode == "file":
        win.mpv.loop_file = "inf"
        win.mpv.loop_playlist = "no"
    elif target_mode == "off":
        win.mpv.loop_file = "no"
        win.mpv.loop_playlist = "no"
