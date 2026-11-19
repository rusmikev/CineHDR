# window_input.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard, pointer, scroll, fullscreen, and UI-visibility handling for CineWindow."""

import shlex
from typing import Any, cast

import gi
import mpv

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from .utils import (
    KEY_REMAP,
    MBTN_MAP,
    PrimaryClick,
    SecondaryClick,
    append_modifiers,
)


def _setup_click_and_hold_controllers(win: Any) -> None:
    win.clk_rect = Gdk.Rectangle()
    for btn_num, MBTN in MBTN_MAP.items():
        click_gesture = Gtk.GestureClick(button=btn_num)
        click_gesture.connect("pressed", win._on_click_pressed, MBTN)
        click_gesture.connect("released", win._on_click_released, MBTN)
        win.toast_overlay.add_controller(click_gesture)

    long_press = Gtk.GestureLongPress.new()
    long_press.connect("pressed", win._on_click_hold)
    long_press.connect("end", win._cancel_click_hold)
    long_press.connect("cancelled", win._cancel_click_hold)
    win.window_handle.add_controller(long_press)

    for c in win.window_handle.observe_controllers():
        if isinstance(c, Gtk.GestureClick):
            c.set_propagation_phase(Gtk.PropagationPhase.NONE)
            c.set_button(9)


def _setup_window_state_signals(win: Any) -> None:
    from . import window as win_mod

    @win._connect("notify::visible-dialog")
    def on_vis_dialog_change(*args):
        if dialog := win.get_visible_dialog():
            win._visible_dialog = dialog
            win.set_cursor_from_name(None)
            win._cancel_click_hold()
            win._space_holding = False
            win._set_space_holding(False)
        else:
            win._visible_dialog = None
        win.hide_ui_timeout()

    @win._connect("notify::is-active")
    def on_is_active_change(*args):
        if win.props.is_active:
            win_mod.timeout_add_once(200, setattr, win, "_is_inactive", False)
        else:
            win._cancel_click_hold()
            win._space_holding = False
            win._set_space_holding(False)
            win._is_inactive = True

    @win._connect("notify::maximized")
    def on_maximized_change(*args):
        win_mod.settings.set_boolean("is-maximized", win.props.maximized)

    win.connect("notify::fullscreened", win._set_fs_state)


def _setup_drop_and_motion_controllers(win: Any) -> None:
    drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
    drop_target.set_gtypes([Gdk.FileList, str])
    drop_target.connect("enter", win._on_drop_enter)
    drop_target.connect("leave", win._on_drop_leave)
    drop_target.connect("drop", win._on_drop)
    win.video_overlay.add_controller(drop_target)

    win.motion_header_controls = Gtk.EventControllerMotion()
    win.motion_header_controls.connect("motion", win._on_mouse_motion)
    win.revealer_ui.add_controller(win.motion_header_controls)

    win.motion_header = Gtk.EventControllerMotion()
    win.motion_controls = Gtk.EventControllerMotion()
    win.headerbar.add_controller(win.motion_header)
    win.controls_box.add_controller(win.motion_controls)

    win.motion_controls_separator = Gtk.EventControllerMotion()
    win.controls_separator.add_controller(win.motion_controls_separator)


def setup_input_controllers_and_window_signals(win: Any) -> None:
    """Attach keyboard, scroll, click, drag-drop, motion, and window state handlers."""
    key_controller = Gtk.EventControllerKey()
    key_controller.connect("key-pressed", win._on_key_event, "keypress")
    key_controller.connect("key-released", win._on_key_event, "keyup")
    key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    win.add_controller(key_controller)

    ecs_flags = Gtk.EventControllerScrollFlags
    overlay_ecs = Gtk.EventControllerScroll.new(ecs_flags.BOTH_AXES)
    win.video_overlay.add_controller(overlay_ecs)
    overlay_ecs.connect("scroll", win._on_mouse_scroll)

    _setup_click_and_hold_controllers(win)
    _setup_window_state_signals(win)
    _setup_drop_and_motion_controllers(win)


def set_fs_state(win: Any) -> None:
    """Sync headerbar decoration layout and mpv fullscreen flag with GTK fullscreen state."""
    from . import window as win_mod

    is_fullscreen = win.props.fullscreened

    try:
        if not is_fullscreen:
            win.mpv.fullscreen = is_fullscreen
    except mpv.ShutdownError:
        pass

    if not win_mod.gtk_setts:
        return

    layout = win_mod.gtk_setts.get_property("gtk-decoration-layout")

    if is_fullscreen:
        left_side, _colon, _right_side = layout.partition(":")
        layout = "close:" if "close" in left_side else ":close"

    win.headerbar.set_decoration_layout(layout)


def sync_fullscreen(win: Any, mpv_is_fs: bool) -> None:
    """Apply mpv fullscreen state changes to the GTK window."""
    win._is_fullscreen = mpv_is_fs
    win.fullscreen() if mpv_is_fs else win.unfullscreen()


def show_ui(win: Any) -> None:
    """Reveal overlay chrome and restore the default pointer cursor."""
    win.set_cursor_from_name(None)
    win.revealer_ui.set_reveal_child(True)


def hide_ui_timeout(win: Any, s: int = 2) -> None:
    """Schedule hiding the overlay UI after ``s`` seconds of inactivity."""
    from . import window as win_mod

    if win._hide_timeout_id:
        win_mod.GLib.source_remove(win._hide_timeout_id)
    win._hide_timeout_id = win_mod.timeout_add_seconds_once(s, win._hide_ui)


def hide_ui(win: Any) -> None:
    """Hide overlay controls and cursor when playback UI is idle and unfocused."""
    try:
        win._hide_timeout_id = 0
        controls_hover = win.motion_controls.props.contains_pointer
        header_hover = win.motion_header.props.contains_pointer

        active_or_hover = (
            win.mpv.idle_active
            or header_hover
            or controls_hover
            or win.primary_menu_btn.props.active
            or win.open_menu_btn.props.active
            or win.options_menu_btn.props.active
            or win.hdr_menu_btn.props.active
            or win.volume_menu_btn.props.active
            or win.subtitles_menu_btn.props.active
            or win.audio_tracks_menu_btn.props.active
            or win.video_tracks_menu_btn.props.active
            or win.chapters_menu_btn.props.active
        )
        if not active_or_hover:
            win.revealer_ui.set_reveal_child(False)
            win._hide_time_tooltip()

        if (
            (win._is_fullscreen or not win.mpv["cursor-autohide-fs-only"])
            and not active_or_hover
            and not win.props.dialogs
        ):
            win.set_cursor_from_name("none")
    except mpv.ShutdownError:
        return


def on_mouse_motion(win: Any, controller: Gtk.EventControllerMotion, x: float, y: float) -> None:
    """Handle pointer motion inside the window overlay and forward Ctrl+motion to mpv."""
    if None not in (x, y):
        if (x, y) == win._prev_motion_xy or win._click_holding:
            return

        win._prev_motion_xy = (x, y)
        win._show_ui()
        win.hide_ui_timeout()

        if event := controller.get_current_event():
            state = event.get_modifier_state()
            if state & Gdk.ModifierType.CONTROL_MASK:
                mpv_x = int(x * win.props.scale_factor)
                mpv_y = int(y * win.props.scale_factor)
                win.mpv.command_async("mouse", mpv_x, mpv_y)


def is_hovering_ui(win: Any) -> bool:
    """Return True if the pointer hovers header or playback controls (not separator)."""
    controls_hover = win.motion_controls.props.contains_pointer
    header_hover = win.motion_header.props.contains_pointer
    separator_hover = win.motion_controls_separator.props.contains_pointer
    return (controls_hover or header_hover) and not separator_hover


def set_space_holding(win: Any, hold: bool) -> None:
    """Toggle 2x speed-hold mode for long-pressing the Space key."""
    from . import window as win_mod

    if hold:
        win._space_hold_id = 0
        if win._click_holding:
            return

        # prevent being able to open menus when clicking buttons while holding spacebar
        # because that causes issues with the internal gtk button handling (space activates it)
        # and it becomes impossible to activate anything again in the window with mouse clicks
        # unless a menu popover is opened again (with keyboard enter)
        win.set_can_target(False)

        win._space_holding = True

        try:
            win.mpv.pause = False
            win._prev_speed = cast(float, win.mpv.speed)
            new_speed = win._prev_speed * 2
            win.mpv.speed = new_speed
            win.mpv.show_text(f"{new_speed:g}× ⯈⯈", "100000000")
        except mpv.ShutdownError:
            pass
    else:
        win.set_can_target(True)

        if win._space_hold_id:
            win_mod.GLib.source_remove(win._space_hold_id)
            win._space_hold_id = 0

        if win._space_pressed:
            win._space_pressed = False
            try:
                win.mpv.speed = win._prev_speed
                win.mpv.show_text(f"{win.mpv.speed:g}×")
            except mpv.ShutdownError:
                pass


def on_key_event(win: Any, keyval: int, state: Any, event_type: str) -> bool | None:
    """Route GTK key press/release events to shortcuts, Space speed-hold, or mpv input."""
    from . import window as win_mod

    win.skip_pause_obs_count = 0
    key_name = Gdk.keyval_name(keyval)

    if win._space_holding and event_type == "keyup":
        win._set_space_holding(False)

    enter = key_name == "Return" and not win._has_enter_binding
    kp_enter = key_name == "KP_Enter" and not win._has_kp_enter_binding

    if key_name in ("Tab", "ISO_Left_Tab") or (enter or kp_enter):
        win.revealer_ui.set_reveal_child(True)
        win.hide_ui_timeout(s=3)
        win._set_space_holding(False)
        return None

    clean_state = state & Gtk.accelerator_get_default_mod_mask()
    accel = Gtk.accelerator_name(keyval, clean_state)
    shortcuts_accel = "<Shift><Control>question"
    if win.app.get_actions_for_accel(accel) or accel == shortcuts_accel:
        win._set_space_holding(False)
        return None

    mpv_key = chr(Gdk.keyval_to_unicode(keyval))
    mpv_key = KEY_REMAP.get(key_name, mpv_key)

    mods: list[str] = []
    append_modifiers(state, mods)

    combo = "+".join(mods + [mpv_key])

    if event_type == "keypress":
        if combo in win._nonrepeat_keys and combo in win._pressed_combos:
            return True
        win._pressed_combos.add(combo)
    elif event_type == "keyup":
        win._pressed_combos.discard(combo)

    if combo == "SPACE":
        if event_type == "keypress":
            if win._space_pressed:
                return True

            win._space_pressed = True

            win._space_hold_id = win_mod.timeout_add_once(
                500, win._set_space_holding, True
            )
        elif event_type == "keyup":
            if win._space_hold_id:
                win_mod.GLib.source_remove(win._space_hold_id)
                win._space_hold_id = 0

            if not win._space_holding:
                win.mpv.command_async("keypress", "SPACE")
                if win._space_pressed:
                    win._space_pressed = False

        win._space_holding = False
        return True

    try:
        win.mpv.command_async(event_type, combo)
        return True
    except mpv.ShutdownError:
        return None


def on_click_pressed(win: Any, gesture: Any, x: float, y: float, button: str) -> None:
    """Handle mouse button press for context menu, side buttons, and UI wake."""
    from . import window as win_mod

    win.skip_pause_obs_count = 0

    if button == "MBTN_LEFT":
        win._left_clk = win_mod.settings.get_int("left-click")
    elif button == "MBTN_RIGHT":
        win._right_clk = win_mod.settings.get_int("right-click")
        if (
            win._right_clk == SecondaryClick.CONTEXT_MENU
            and not win.start_page.props.visible
        ):
            rect = Gdk.Rectangle()
            rect.x = x
            rect.y = y
            win.context_popover_menu.set_pointing_to(rect)
            win.context_popover_menu.popup()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return

    if button != "MBTN_LEFT":
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
    # Back and forward dont trigger _on_click_released when video is playing (??)
    if button in ("MBTN_BACK", "MBTN_FORWARD"):
        win.mpv.command_async("keypress", button)
        return

    win._show_ui()
    win.hide_ui_timeout()


def on_click_hold(win: Any) -> None:
    """Enable temporary 2x playback speed while long-pressing the video area."""
    try:
        if win._space_holding or win._is_hovering_ui():
            return

        win._click_holding = True
        win.mpv.pause = False
        win._prev_speed = cast(float, win.mpv.speed)
        new_speed = win._prev_speed * 2
        win.mpv.speed = new_speed
        win.mpv.show_text(f"{new_speed:g}× ⯈⯈", "100000000")
    except mpv.ShutdownError:
        pass


def run_mpv_command(win: Any, cmd: str) -> None:
    """Split and dispatch semicolon-separated mpv commands from input bindings."""
    from . import window as win_mod

    try:
        for sub_cmd in cmd.split(";"):
            args = shlex.split(sub_cmd.strip())
            win.mpv.command_async(*args)
    except Exception:
        win_mod.logger.exception("_run_command failed")


def on_click_released(win: Any, n_press: int, button: str) -> None:
    """Dispatch single/double click actions and mouse bindings on button release."""
    from . import window as win_mod

    ignored_btn = button in ("MBTN_BACK", "MBTN_FORWARD")
    ignore_left = (
        win._is_inactive
        and button == "MBTN_LEFT"
        and win._left_clk == PrimaryClick.FOCUS_PLAY_PAUSE
    )

    if ignored_btn or ignore_left or win._is_hovering_ui():
        return

    is_secondary_pause = (
        button == "MBTN_RIGHT" and win._right_clk == SecondaryClick.PLAY_PAUSE
    )

    if is_secondary_pause:
        win._cycle_pause()
        return

    if win._click_delay_id:
        win_mod.GLib.source_remove(win._click_delay_id)
        win._click_delay_id = 0

    if (
        n_press == 1
        and not win._click_holding
        and button == "MBTN_LEFT"
        and win._left_clk != PrimaryClick.BYPASS
    ):

        def click():
            win._cycle_pause()
            win._click_delay_id = 0

        win._click_delay_id = win_mod.timeout_add_once(win._click_time, click)
        return
    elif n_press == 2:
        win.mpv.command_async("keypress", button + "_DBL")
        return

    win.mpv.command_async("keypress", button)


def cancel_click_hold(win: Any) -> None:
    """Restore previous playback speed when a long-press ends or is cancelled."""
    if not win._click_holding:
        return
    try:
        win.mpv.speed = win._prev_speed
        win.mpv.show_text(f"{win.mpv.speed:g}×")
        win._click_holding = False
    except mpv.ShutdownError:
        pass


def on_mouse_scroll(win: Any, controller: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
    """Translate wheel/touchpad scroll deltas into mpv WHEEL_* keypress commands."""
    win.skip_pause_obs_count = 0
    event: Gdk.ScrollEvent = controller.get_current_event()
    state = event.get_modifier_state()

    if event.get_unit() == Gdk.ScrollUnit.SURFACE:  # Touchpad
        # Scale it down so it doesn't fire rapidly
        dx *= 0.1
        dy *= 0.1

    win._wheel_accum_x += dx
    win._wheel_accum_y += dy

    rel_dir: Gdk.ScrollRelativeDirection = event.get_relative_direction()  # type: ignore
    is_natural: bool = rel_dir == Gdk.ScrollRelativeDirection.INVERTED  # type: ignore
    up_btn: str = "WHEEL_DOWN" if is_natural else "WHEEL_UP"
    down_btn: str = "WHEEL_UP" if is_natural else "WHEEL_DOWN"
    left_btn: str = "WHEEL_RIGHT" if is_natural else "WHEEL_LEFT"
    right_btn: str = "WHEEL_LEFT" if is_natural else "WHEEL_RIGHT"
    wheel: str | None = None

    mods: list[str] = []
    append_modifiers(state, mods)

    # Only trigger if scrolled a full 'unit'
    if abs(win._wheel_accum_y) >= 1:
        wheel = up_btn if win._wheel_accum_y < 0 else down_btn
        win._wheel_accum_y = 0.0
    elif abs(win._wheel_accum_x) >= 1:
        wheel = right_btn if win._wheel_accum_x > 0 else left_btn
        win._wheel_accum_x = 0.0

    if wheel:
        combo = "+".join(mods + [wheel])
        win.mpv.command_async("keypress", combo)

    return True


def on_mouse_scroll_volume(win: Any, controller: Gtk.EventControllerScroll, dy: float) -> bool:
    """Adjust volume adjustment on scroll over the volume button/box."""
    event: Gdk.ScrollEvent = controller.get_current_event()
    direction: Gdk.ScrollDirection = event.get_direction()
    rel_dir: Gdk.ScrollRelativeDirection = event.get_relative_direction()  # type: ignore
    is_natural: bool = rel_dir == Gdk.ScrollRelativeDirection.INVERTED  # type: ignore
    max_vol = cast(float, win.mpv.volume_max)
    step = dy if direction == Gdk.ScrollDirection.SMOOTH else dy * 5

    if is_natural:
        step = -step

    adj = win.volume_scale.get_adjustment()
    volume = adj.get_value()
    new_vol = int(volume - step)
    new_vol = max(adj.get_lower(), min(new_vol, max_vol))
    adj.set_value(new_vol)

    return True
