# window.py
#
# Copyright 2025 Diego Povliuk
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

import bisect
import logging
import re
import shlex
from gettext import gettext as _
from typing import Any, cast

import gi
import mpv

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("GObject", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .hdr_menu import HdrMenuButton
from .mpris import MPRIS
from .mpv_gl_area import ThumbPreviewGLArea
from .options import OptionsMenuButton
from .playlist import PlaylistItemObj
from .preferences import settings, sync_mpv_with_settings
from .save_session import (
    is_same_playlist,
    restore_last_playlist,
    save_last_playlist_file as save_last_playlist_file,
)
from .shortcuts import INTERNAL_BINDINGS
from .utils import (
    CONFIG_DIR,
    INPUT_CONF,
    KEY_REMAP,
    MBTN_MAP,
    SCREENSHOT_DIR,
    WATCH_HISTORY_JSONL,
    PrimaryClick,
    SecondaryClick,
    append_modifiers,
    format_time,
    get_mouse_bindings,
    has_host_permission as has_host_permission,
    idle_add_once,
    is_local_path as is_local_path,
    load_optional_validation,
    parse_bindings,
    timeout_add_once,
    timeout_add_seconds_once,
)
from .video_widget import MpvVideoWidget
from .window_diagnostics import (
    handle_first_media_frame_published,
    handle_first_video_frame,
    init_diagnostics_exporter,
    notify_diagnostics_shutdown,
)
from .window_media import (
    handle_drop,
    navigate_playlist,
    on_shuffle_toggled as media_on_shuffle_toggled,
    open_add_dialog,
    open_folder_dialog,
    open_playlist_dialog,
    open_url_dialog,
    present_history,
    present_shortcuts,
    save_session,
    splice_playlist as media_splice_playlist,
    sync_can_prev_next,
    update_chapter_marks_and_menu,
    update_track_menus,
)
from .window_observers import setup_window_observers

logger = logging.getLogger(__name__)
gtk_setts: Gtk.Settings | None = Gtk.Settings.get_default()


def safe_mpv_file_error_label(value: object) -> str:
    """Retain mpv's short error label without logging a media path."""
    if not isinstance(value, (bytes, str, int)) or isinstance(value, bool):
        return "unavailable"
    label = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    return label if re.fullmatch(r"[A-Za-z0-9 _-]{1,80}", label) else "unavailable"


def make_mpv_log_handler(owner, mpv_logger=None):
    """Create an mpv log handler that tracks Dolby Vision RPU warnings without spamming or invoking GTK."""
    if mpv_logger is None:
        mpv_logger = logging.getLogger("mpv")

    def _mpv_log_handler(level: str, prefix: str, message: str):
        if "Multiple Dolby Vision RPUs" in message:
            owner.dovi_rpu_warning_count += 1
            clean_msg = message.strip()
            if owner.dovi_rpu_warning_text is None:
                if prefix and not clean_msg.startswith(prefix):
                    owner.dovi_rpu_warning_text = f"{prefix}: {clean_msg}"
                else:
                    owner.dovi_rpu_warning_text = clean_msg
            if not owner._dovi_rpu_logged:
                owner._dovi_rpu_logged = True
                mpv_logger.warning(
                    "[%s] %s (further duplicate RPU warnings suppressed)",
                    prefix,
                    clean_msg,
                )
            return
        lvl = logging.WARNING if level in ("warn", "error", "fatal") else logging.DEBUG
        mpv_logger.log(lvl, "[%s] %s", prefix, message.rstrip())

    return _mpv_log_handler


@Gtk.Template(resource_path="/io/github/rusmikev/CineHDR/window.ui")
class CineWindow(Adw.ApplicationWindow):
    __gtype_name__ = "CineWindow"

    window_handle: Gtk.WindowHandle = Gtk.Template.Child()
    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    video_overlay: Gtk.Overlay = Gtk.Template.Child()
    start_page: Adw.StatusPage = Gtk.Template.Child()
    revealer_icon_indicator: Gtk.Revealer = Gtk.Template.Child()
    icon_indicator: Gtk.Image = Gtk.Template.Child()
    title_widget: Adw.WindowTitle = Gtk.Template.Child()
    headerbar: Adw.HeaderBar = Gtk.Template.Child()
    controls_box: Gtk.Box = Gtk.Template.Child()
    controls_wrap_box: Adw.WrapBox = Gtk.Template.Child()
    controls_separator: Gtk.Separator = Gtk.Template.Child()
    audio_only_icon: Gtk.Image = Gtk.Template.Child()
    revealer_ui: Gtk.Revealer = Gtk.Template.Child()
    revealer_drop_indicator: Gtk.Revealer = Gtk.Template.Child()
    drop_label: Gtk.Label = Gtk.Template.Child()
    drop_icon: Gtk.Image = Gtk.Template.Child()
    spinner: Adw.Spinner = Gtk.Template.Child()
    context_popover_menu: Gtk.PopoverMenu = Gtk.Template.Child()

    open_menu_btn: Gtk.MenuButton = Gtk.Template.Child()
    primary_menu_btn: Gtk.MenuButton = Gtk.Template.Child()
    previous_btn: Gtk.Button = Gtk.Template.Child()
    play_pause_btn: Gtk.Button = Gtk.Template.Child()
    next_btn: Gtk.Button = Gtk.Template.Child()
    volume_menu_btn: Gtk.MenuButton = Gtk.Template.Child()
    mute_toggle_btn: Gtk.ToggleButton = Gtk.Template.Child()
    volume_box: Gtk.Box = Gtk.Template.Child()
    volume_scale: Gtk.Scale = Gtk.Template.Child()
    volume_scale_adj: Gtk.Adjustment = Gtk.Template.Child()
    subtitles_menu_btn: Gtk.MenuButton = Gtk.Template.Child()
    subtitles_menu: Gio.Menu = Gtk.Template.Child()
    audio_tracks_menu_btn: Gtk.MenuButton = Gtk.Template.Child()
    audio_tracks_menu: Gio.Menu = Gtk.Template.Child()
    video_tracks_menu_btn: Gtk.MenuButton = Gtk.Template.Child()
    video_tracks_menu: Gio.Menu = Gtk.Template.Child()
    chapters_menu_btn: Gtk.MenuButton = Gtk.Template.Child()
    chapters_menu: Gio.Menu = Gtk.Template.Child()
    options_menu_btn: OptionsMenuButton = Gtk.Template.Child()
    hdr_menu_btn: HdrMenuButton = Gtk.Template.Child()
    shuffle_toggle_btn: Gtk.ToggleButton = Gtk.Template.Child()
    ab_loop_btn: Gtk.ToggleButton = Gtk.Template.Child()
    loop_btn: Gtk.ToggleButton = Gtk.Template.Child()
    fullscreen_btn: Gtk.Button = Gtk.Template.Child()
    time_elapsed_label: Gtk.Label = Gtk.Template.Child()
    progress_box: Gtk.Box = Gtk.Template.Child()
    video_progress_scale: Gtk.Scale = Gtk.Template.Child()
    video_progress_adj: Gtk.Adjustment = Gtk.Template.Child()
    time_total_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self, is_activate=False, **kwargs):
        super().__init__(**kwargs)
        self.app: Adw.Application = cast(Adw.Application, kwargs.get("application"))
        self._mpris: MPRIS = self.app.mpris  # type: ignore
        self._is_gpu_validation: bool = (
            getattr(self.app, "gpu_validation_config", None) is not None
        )

        Gtk.WindowGroup().add_window(self)

        self._visible_dialog: Adw.Dialog | None = None
        self.playlist_ls: Gio.ListStore = Gio.ListStore.new(PlaylistItemObj)
        self._playlist_debounce_id: int = 0
        self._playlist_prev_pos: int
        self.prev_shuffle: bool = False
        self.playlist_changed: bool = False
        self.has_some_doc_path: bool = False
        self.can_go_prev: bool = False
        self.can_go_next: bool = False
        self._loop_mode: str = "off"
        self._chapters: list = []
        self._curr_chapter_time = None
        self._actions: dict[str, Gio.SimpleAction] = {}
        self._prev_motion_xy: tuple = (0, 0)
        self._hover_time: float = 0.0
        self._show_remaining: bool = settings.get_boolean("show-remaining")
        self._prev_prog_time: float = -1.0
        self._prev_prog_motion_xy: tuple = (0, 0)
        self._inhibit_cookie: int = 0
        self._video_path: str | None = None
        self._is_startup: bool = True
        self._is_audio: bool = False
        self._space_hold_id: int = 0
        self._space_holding: bool = False
        self._space_pressed: bool = False
        self._left_clk: int = settings.get_int("left-click")
        self._right_clk: int = settings.get_int("right-click")
        self._click_delay_id: int = 0
        _ck_time: int = gtk_setts.props.gtk_double_click_time if gtk_setts else 400
        self._click_time: int = max(_ck_time, min(200, 425))
        self._click_holding: bool = False
        self._prev_speed: float = 1.0
        self._wheel_accum_x: float = 0.0
        self._wheel_accum_y: float = 0.0
        self._hide_icon_indicator: bool = True
        self.skip_pause_obs_count: int = 0
        self._playing_on_press: bool = False
        self._duration: float = 0.0
        self.thumb_area: ThumbPreviewGLArea | None = None
        self._thumb_w: int = 1280
        self.is_local_path: bool = True
        self._prog_fine_tune: bool = False
        self._error_count: int = 0
        self._pressed_combos: set[str] = set()
        self._hide_timeout_id: int = 0
        self._is_fullscreen: bool = False
        self._is_inactive: bool = False
        self._mpv_ctx: mpv.MpvRenderContext

        mpv_logger = logging.getLogger("mpv")

        self.dovi_rpu_warning_count: int = 0
        self.dovi_rpu_warning_text: str | None = None
        self._dovi_rpu_logged: bool = False
        self._shutting_down: bool = False
        self._media_load_generation: int = 0
        self._diagnostics_exporter = init_diagnostics_exporter(
            __package__, logger, load_optional_validation
        )

        self._mpv_log_handler = make_mpv_log_handler(self, mpv_logger)

        self.mpv = mpv.MPV(
            log_handler=self._mpv_log_handler,
            loglevel="warn",
            audio_client_name=_("CineHDR"),
            screenshot_directory=SCREENSHOT_DIR,
            screenshot_template="cine_%n",
            config=True,
            config_dir=CONFIG_DIR,
            input_builtin_bindings=False,
            input_vo_keyboard=True,
            load_scripts=True,
            audio_display="embedded-first",
            audio_file_auto="fuzzy",
            sub_auto="fuzzy",
            sub_file_paths="sub:subs:subtitles:Sub:Subs:Subtitles:srt:srts:Srt:Srts",
            sub_border_size=2,
            sub_shadow_offset=0.6,
            sub_border_color="#B6000000",
            sub_shadow_color="#97000000",
            sub_color="#ebebeb",
            sub_use_margins=False,
            sub_font="Adwaita Sans SemiBold",
            osd_font="Adwaita Sans",
            osd_bold=True,
            osd_bar=False,
            osd_blur=1,
            osd_border_size=1.5,
            osd_shadow_offset=0.6,
            osd_border_color="#BE000000",
            osd_shadow_color="#1B000000",
            osd_margin_x=66,
            osd_margin_y=66,
            volume_max=150,
            keep_open=True,
            ytdl=True,
            ytdl_raw_options="yes-playlist=",
            cursor_autohide_fs_only=True,
            autocreate_playlist="filter",
            save_watch_history=True,
            watch_history_path=WATCH_HISTORY_JSONL,
        )

        self._video_area = MpvVideoWidget(
            self.mpv,
            render_backend_selection=self.app.render_backend_selection,  # type: ignore
        )
        if hasattr(self._video_area, "setup_window_integration"):
            self._video_area.setup_window_integration(self)
        self.offload: Gtk.GraphicsOffload = Gtk.GraphicsOffload(child=self._video_area)
        self.offload.set_black_background(True)
        self.video_overlay.set_child(self.offload)

        self.offload.set_enabled(
            Gtk.GraphicsOffloadEnabled.ENABLED
            if settings.get_boolean("graphics-offload")
            else Gtk.GraphicsOffloadEnabled.DISABLED
        )
        if self.mpv["window-maximized"] or settings.get_boolean("is-maximized"):
            self.maximize()

        self.conf_hwdec = list(filter(lambda x: x != "no", cast(list, self.mpv.hwdec)))
        self.mpv["vo"] = "libmpv"
        self.mpv["osc"] = "no"
        self.mpv["load-console"] = "no"
        self.mpv.command("change-list", "watch-later-options", "remove", "vid")
        self.mpv.command("change-list", "watch-later-options", "remove", "aid")
        self.mpv.command("change-list", "watch-later-options", "remove", "volume")
        self.mpv.command("change-list", "watch-later-options", "remove", "sub-scale")
        self.mpv.command("change-list", "watch-later-options", "remove", "ab-loop-a")
        self.mpv.command("change-list", "watch-later-options", "remove", "ab-loop-b")

        self._setup_actions()
        self._setup_widgets()
        self._setup_observers()

        try:
            self.mpv.command("load-input-conf", f"memory://{INTERNAL_BINDINGS}")
            self.mpv.command("load-input-conf", INPUT_CONF)
        except Exception:
            logger.exception("load-input-conf failed")

        self._input_bindings = cast(dict, self.mpv.input_bindings)
        self._mouse_binds: dict = get_mouse_bindings(self._input_bindings)
        self._nonrepeat_keys, self._has_enter_binding, self._has_kp_enter_binding = (
            parse_bindings(self._input_bindings)
        )

        sync_mpv_with_settings(self)

        if (
            settings.get_boolean("save-session")
            and is_activate
            and not self._is_gpu_validation
        ):
            restore_last_playlist(self, self.app, self.mpv)

    def _setup_actions(self):
        self._create_action("clear-and-add", self._on_clear_and_add)
        self._create_action_stateful("select-subtitle", self._on_subtitle_selected, "i")
        self._create_action_stateful("select-audio", self._on_audio_selected, "i")
        self._create_action_stateful("select-video", self._on_video_selected, "i")
        self._create_action_stateful("select-chapter", self._on_chapter_selected, "i")
        self._create_action("add-sub-tracks", self._on_add_sub_dialog)
        self._create_action("add-audio-tracks", self._on_add_audio_dialog)
        self._create_action("add-playlist-files", self.on_add_playlist_dialog)
        self._create_action("open-folder", self.on_open_folder_dialog)
        self._create_action("open-url", self._on_open_url)
        self._create_action("add-url", self.on_add_url)
        self._create_action("open-history", self._present_history)
        self._create_action("add-playlist-folder", self.on_open_folder_dialog)
        self._create_action("open-playlist-dialog", self._on_open_playlist)
        self._create_action("open-sub-menu", self._on_open_sub_menu)
        self._create_action("open-audio-menu", self._on_open_audio_menu)
        self._create_action("open-chapters-menu", self._on_open_chapters_menu)
        self._create_action("save-session", self._on_save_session)
        self._create_action(
            "save-session-close", lambda *a: self._on_save_session(close=True)
        )

        self.app.set_accels_for_action("win.open-folder", ["<primary>i"])
        self.app.set_accels_for_action("win.open-url", ["<primary>u"])
        self.app.set_accels_for_action("win.add-url", ["<shift><primary>u"])
        self.app.set_accels_for_action("win.open-history", ["<primary>h"])
        self.app.set_accels_for_action("win.add-playlist-folder", ["<shift><primary>i"])
        self.app.set_accels_for_action("win.open-playlist-dialog", ["<primary>p"])
        self.app.set_accels_for_action("win.clear-and-add", ["<primary>o"])
        self.app.set_accels_for_action("win.add-playlist-files", ["<shift><primary>o"])
        self.app.set_accels_for_action("win.open-sub-menu", ["<primary>s"])
        self.app.set_accels_for_action("win.open-audio-menu", ["<primary>a"])
        self.app.set_accels_for_action("win.open-chapters-menu", ["<primary>c"])
        self.app.set_accels_for_action("win.save-session", ["<shift><primary>s"])
        self.app.set_accels_for_action("win.save-session-close", ["<shift>q"])

        self._create_action("quit", lambda *a: self.close())
        self.app.set_accels_for_action("win.quit", ["q", "<primary>w"])

        self._create_action("custom-shortcuts", self._present_shortcuts)
        self.app.set_accels_for_action("win.custom-shortcuts", ["<primary>question"])
        self.app.set_accels_for_action("app.shortcuts", [])

        self._create_action("play-pause", self._cycle_pause)
        self._create_action("previous", self.on_previous_clicked)
        self._create_action("next", self.on_next_clicked)

    def _present_shortcuts(self, *args):
        present_shortcuts(self)

    def _present_history(self, *args):
        present_history(self)

    def _setup_widgets(self):
        for widget in [
            self.controls_wrap_box,
            self.volume_box,
            self.volume_scale,
            self.progress_box,
            self.video_progress_scale,
            self.time_elapsed_label,
        ]:
            widget.set_direction(Gtk.TextDirection.LTR)

        max_vol = cast(int, self.mpv.volume_max)
        self.volume_scale_adj.set_upper(max_vol)

        self.mute_handler_id = self.mute_toggle_btn.connect(
            "toggled",
            lambda btn: self.mpv.command_async("set", "mute", btn.get_active()),
        )

        vol_mid_click = Gtk.GestureClick(button=2)
        vol_mid_click.connect(
            "pressed",
            lambda *a: self.mpv.command_async("cycle", "mute"),
        )
        self.volume_menu_btn.add_controller(vol_mid_click)

        self.fullscreen_btn.connect(
            "clicked",
            lambda *a: self.mpv.command_async("cycle", "fullscreen"),
        )

        vc_adj = self.volume_scale_adj
        self.volume_handler_id = self.volume_scale.connect(
            "value-changed",
            lambda *a: self.mpv.command_async("set", "volume", vc_adj.props.value),
        )

        if max_vol > 100:
            self.volume_scale.add_mark(100.0, Gtk.PositionType.BOTTOM, None)

        self.ab_loop_btn.connect("toggled", self._on_ab_loop_btn_toggled)
        self.loop_btn.connect("toggled", self._on_loop_toggled)

        self.video_progress_adj.connect("value-changed", self._on_progress_adjusted)
        self.popover_content_box = Gtk.Box()
        self.popover_content_box.props.orientation = Gtk.Orientation.VERTICAL

        self.time_tooltip_label = Gtk.Label()
        self.time_tooltip_label.set_use_markup(True)
        self.time_tooltip_label.set_justify(Gtk.Justification.CENTER)
        self.time_tooltip_label.add_css_class("numeric")

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
            self.video_overlay.add_overlay(layer)
            return layer, revealer

        self.tooltip_label_layer, self.tooltip_label_revealer = (
            create_layer_and_revealer(self.time_tooltip_label, 38)
        )

        self.thumb_frame = Gtk.Frame()
        self.tooltip_thumb_layer, self.tooltip_thumb_revealer = (
            create_layer_and_revealer(self.thumb_frame, 72)
        )

        self._set_time_margin()
        self._set_time_tooltip()
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_event, "keypress")
        key_controller.connect("key-released", self._on_key_event, "keyup")
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.add_controller(key_controller)

        progress_hover = Gtk.EventControllerMotion()
        progress_hover.connect("enter", self._set_time_tooltip)
        progress_hover.connect("motion", self._on_progress_motion)
        progress_hover.connect("leave", self._hide_time_tooltip)
        self.video_progress_scale.add_controller(progress_hover)

        prog_mid_click = Gtk.GestureClick(button=2)
        prog_mid_click.connect("pressed", self._go_to_chapter_start)
        self.video_progress_scale.add_controller(prog_mid_click)

        for c in self.video_progress_scale.observe_controllers():
            if isinstance(c, Gtk.GestureDrag):
                c.connect("drag-begin", self._on_progress_pressed)
                c.connect("drag-end", self._on_progress_released)
            if isinstance(c, Gtk.GestureLongPress):
                c.connect("pressed", lambda *a: setattr(self, "_prog_fine_tune", True))
                c.connect("end", lambda *a: setattr(self, "_prog_fine_tune", False))

        ecs_flags = Gtk.EventControllerScrollFlags

        progress_ecs = Gtk.EventControllerScroll.new(ecs_flags.VERTICAL)
        progress_ecs.connect("scroll", self._on_progress_scroll)
        self.video_progress_scale.add_controller(progress_ecs)

        overlay_ecs = Gtk.EventControllerScroll.new(ecs_flags.BOTH_AXES)
        volume_ecs = Gtk.EventControllerScroll.new(ecs_flags.VERTICAL)
        self.video_overlay.add_controller(overlay_ecs)
        overlay_ecs.connect("scroll", self._on_mouse_scroll)
        self.volume_scale.add_controller(volume_ecs)
        volume_ecs.connect("scroll", self._on_mouse_scroll_volume)

        self.clk_rect = Gdk.Rectangle()
        for btn_num, MBTN in MBTN_MAP.items():
            click_gesture = Gtk.GestureClick(button=btn_num)
            click_gesture.connect("pressed", self._on_click_pressed, MBTN)
            click_gesture.connect("released", self._on_click_released, MBTN)
            self.toast_overlay.add_controller(click_gesture)

        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_click_hold)
        long_press.connect("end", self._cancel_click_hold)
        long_press.connect("cancelled", self._cancel_click_hold)
        self.window_handle.add_controller(long_press)

        for c in self.window_handle.observe_controllers():
            if isinstance(c, Gtk.GestureClick):
                c.set_propagation_phase(Gtk.PropagationPhase.NONE)
                c.set_button(9)

        @self._connect("notify::visible-dialog")
        def on_vis_dialog_change(*args):
            if dialog := self.get_visible_dialog():
                self._visible_dialog = dialog
                self.set_cursor_from_name(None)
                self._cancel_click_hold()
                self._space_holding = False
                self._set_space_holding(False)
            else:
                self._visible_dialog = None
            self.hide_ui_timeout()

        @self._connect("notify::is-active")
        def on_is_active_change(*args):
            if self.props.is_active:
                timeout_add_once(200, setattr, self, "_is_inactive", False)
            else:
                self._cancel_click_hold()
                self._space_holding = False
                self._set_space_holding(False)
                self._is_inactive = True

        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.set_gtypes([Gdk.FileList, str])
        drop_target.connect("enter", self._on_drop_enter)
        drop_target.connect("leave", self._on_drop_leave)
        drop_target.connect("drop", self._on_drop)
        self.video_overlay.add_controller(drop_target)

        self.motion_header_controls = Gtk.EventControllerMotion()
        self.motion_header_controls.connect("motion", self._on_mouse_motion)
        self.revealer_ui.add_controller(self.motion_header_controls)

        self.motion_header = Gtk.EventControllerMotion()
        self.motion_controls = Gtk.EventControllerMotion()
        self.headerbar.add_controller(self.motion_header)
        self.controls_box.add_controller(self.motion_controls)

        self.motion_controls_separator = Gtk.EventControllerMotion()
        self.controls_separator.add_controller(self.motion_controls_separator)

        @self._connect("notify::maximized")
        def on_maximized_change(*args):
            settings.set_boolean("is-maximized", self.props.maximized)

        self.connect("notify::fullscreened", self._set_fs_state)

        buttons = [
            self.primary_menu_btn,
            self.open_menu_btn,
            self.options_menu_btn,
            self.hdr_menu_btn,
            self.volume_menu_btn,
            self.subtitles_menu_btn,
            self.audio_tracks_menu_btn,
            self.video_tracks_menu_btn,
            self.chapters_menu_btn,
        ]
        for btn in buttons:
            popover = btn.props.popover
            popover.connect("closed", self.hide_ui_timeout)

            if btn == self.open_menu_btn:

                def on_popv_closed(*args):
                    if is_same_playlist(self.mpv.playlist):
                        self.mpv.write_watch_later_config()

                popover.connect("closed", on_popv_closed)


    def _set_fs_state(self, _window, _gparam):
        is_fullscreen = self.props.fullscreened

        try:
            if not is_fullscreen:
                self.mpv.fullscreen = is_fullscreen
        except mpv.ShutdownError:
            pass

        if not gtk_setts:
            return

        layout = gtk_setts.get_property("gtk-decoration-layout")

        if is_fullscreen:
            left_side, _colon, _right_side = layout.partition(":")
            layout = "close:" if "close" in left_side else ":close"

        self.headerbar.set_decoration_layout(layout)

    def _hide_time_tooltip(self, *args):
        def hide():
            self.tooltip_thumb_revealer.set_reveal_child(False)
            self.tooltip_label_revealer.set_reveal_child(False)

        idle_add_once(hide)

    def _show_ui(self):
        self.set_cursor_from_name(None)
        self.revealer_ui.set_reveal_child(True)

    def hide_ui_timeout(self, *args, s=2):
        if self._hide_timeout_id:
            GLib.source_remove(self._hide_timeout_id)
        self._hide_timeout_id = timeout_add_seconds_once(s, self._hide_ui)

    def _hide_ui(self, *args):
        try:
            self._hide_timeout_id = 0
            controls_hover = self.motion_controls.props.contains_pointer
            header_hover = self.motion_header.props.contains_pointer

            active_or_hover = (
                self.mpv.idle_active
                or header_hover
                or controls_hover
                or self.primary_menu_btn.props.active
                or self.open_menu_btn.props.active
                or self.options_menu_btn.props.active
                or self.hdr_menu_btn.props.active
                or self.volume_menu_btn.props.active
                or self.subtitles_menu_btn.props.active
                or self.audio_tracks_menu_btn.props.active
                or self.video_tracks_menu_btn.props.active
                or self.chapters_menu_btn.props.active
            )
            if not active_or_hover:
                self.revealer_ui.set_reveal_child(False)
                self._hide_time_tooltip()

            if (
                (self._is_fullscreen or not self.mpv["cursor-autohide-fs-only"])
                and not active_or_hover
                and not self.props.dialogs
            ):
                self.set_cursor_from_name("none")
        except mpv.ShutdownError:
            return

    def _on_mouse_motion(self, controller: Gtk.EventControllerMotion, x, y):
        if None not in (x, y):
            if (x, y) == self._prev_motion_xy or self._click_holding:
                return

            self._prev_motion_xy = (x, y)
            self._show_ui()
            self.hide_ui_timeout()

            if event := controller.get_current_event():
                state = event.get_modifier_state()
                if state & Gdk.ModifierType.CONTROL_MASK:
                    mpv_x = int(x * self.props.scale_factor)
                    mpv_y = int(y * self.props.scale_factor)
                    self.mpv.command_async("mouse", mpv_x, mpv_y)

    def _update_track_menus(self, track_list):
        update_track_menus(self, track_list)

    def _create_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        self._actions[name] = action

    def _create_action_stateful(self, name, callback, target_type):
        if target_type != "i":
            raise TypeError("_create_action_stateful int only")
        action = Gio.SimpleAction.new_stateful(
            name,
            GLib.VariantType.new(target_type),
            GLib.Variant("i", 0),
        )
        action.connect("activate", callback)
        self.add_action(action)
        self._actions[name] = action

    def _on_open_playlist(self, *args):
        open_playlist_dialog(self)

    def on_open_folder_dialog(self, action, *args):
        return open_folder_dialog(self, action)

    def _on_clear_and_add(self, _action, _param):
        open_add_dialog(self, _("Open Files"), "clear-and-add")

    def on_add_playlist_dialog(self, _action, _param):
        open_add_dialog(self, _("Add Files"), "playlist-add")
        return Gdk.EVENT_STOP

    def _on_add_sub_dialog(self, _action, _param):
        open_add_dialog(self, _("Add Subtitle"), "sub-add")

    def _on_add_audio_dialog(self, _action, _param):
        open_add_dialog(self, _("Add Audio"), "audio-add")

    def _on_open_sub_menu(self, *args):
        self._show_ui()
        self.subtitles_menu_btn.popup()

    def _on_open_audio_menu(self, *args):
        self._show_ui()
        self.audio_tracks_menu_btn.popup()

    def _on_open_chapters_menu(self, *args):
        if not self.mpv.chapters:
            return
        self._show_ui()
        self.chapters_menu_btn.popup()

    def _on_save_session(self, *args, close=False):
        if self._is_gpu_validation:
            logger.info("Ignoring session save during GPU validation")
            return
        save_session(self, close=close)

    def _on_open_url(self, *args, add=False):
        open_url_dialog(self, add=add)

    def on_add_url(self, *args):
        open_url_dialog(self, add=True)
        return Gdk.EVENT_STOP

    def setup_thumb_preview(self):
        if not self.thumb_area:
            self.thumb_area = ThumbPreviewGLArea(self.mpv.hwdec)
            self.thumb_frame.set_child(self.thumb_area)
            self.thumb_area.realize()

        v_width, v_height = 1280, 720

        try:
            if self.mpv.vid:
                self.mpv.wait_for_property("width", timeout=2)
                self.mpv.wait_for_property("height", timeout=2)
                v_width = cast(int, self.mpv.width)
                v_height = cast(int, self.mpv.height)
        except Exception:
            logger.exception("Failed to get video w/h")

        if v_width >= v_height:  # Horizontal or square
            width = 200
            height = int((v_height / v_width) * width)
            if height == width:
                width, height = 168, 168
        else:  # Vertical
            height = 168
            width = int((v_width / v_height) * height)

        self._thumb_w = width
        self.thumb_area.set_size_request(width, height)
        a = self.thumb_area
        a.stop() if self._is_audio else a.load_file(self._video_path)

    def _hide_time_tooltip(self, *args):
        self._prev_reveal = False
        self.tooltip_thumb_revealer.set_reveal_child(False)
        self.tooltip_label_revealer.set_reveal_child(False)

    def _set_time_tooltip(self, *args):
        self._prev_prog_motion_xy = (-1, -1)  # triggers _on_progress_motion
        self._width = self.get_width()
        self._prog_width = self.video_progress_scale.get_width()
        self._prev_reveal = False

    def _move_time_tooltip(self, revealer, layer: Gtk.Fixed, x, tooltip_w):
        x_pos = max(0, min(x - (tooltip_w / 2) + 23, self._width - tooltip_w))
        layer.move(revealer, x_pos, 0)

    def _on_progress_motion(self, _controller, x, y):
        if (x, y) == self._prev_prog_motion_xy:
            return
        self._prev_prog_motion_xy = (x, y)

        if self._prog_fine_tune:
            self._hide_time_tooltip()
            return

        show_thumb = self.thumb_area is not None and not self._is_audio

        if not self._prev_reveal:
            self.tooltip_thumb_revealer.set_reveal_child(show_thumb)
            self.tooltip_label_revealer.set_reveal_child(True)
            self._prev_reveal = True

        if self._prog_width <= 0:
            return

        percentage = max(0, min(1, x / self._prog_width))
        self._hover_time = percentage * self._duration

        title = None
        if self._chapters:
            idx = bisect.bisect_right(self.chapter_times, self._hover_time) - 1
            if idx >= 0:
                self._curr_chapter_time = self.chapter_times[idx]
                title = self.chapter_titles[idx]
        else:
            self._curr_chapter_time = None

        time_str = format_time(self._hover_time)
        text = f"{time_str} ‐ <b>{title}</b>" if title else time_str
        self.time_tooltip_label.set_markup(text)
        label_w = self.tooltip_label_revealer.get_preferred_size()[1].width

        self._move_time_tooltip(
            self.tooltip_label_revealer, self.tooltip_label_layer, x, label_w
        )

        if self.thumb_area is None:
            return

        self._move_time_tooltip(
            self.tooltip_thumb_revealer, self.tooltip_thumb_layer, x, self._thumb_w + 12
        )

        idle_add_once(self.thumb_area.seek, self._hover_time)

    def _go_to_chapter_start(self, *args):
        if self._curr_chapter_time is not None:
            self.mpv.command_async("seek", self._curr_chapter_time, "absolute")

    def _on_progress_scroll(self, controller, _dx, dy):
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

        adj = self.video_progress_scale.get_adjustment()
        progress = adj.get_value()
        new_progress = progress - step
        adj.set_value(new_progress)

        return True

    def _update_volume_icon(self):
        volume = cast(float, self.mpv.volume)
        is_muted = self.mpv.mute

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

        self.volume_menu_btn.props.icon_name = icon

    def _set_time_margin(self):
        self.time_elapsed_label.props.margin_end = 3 if self._show_remaining else 0

    @Gtk.Template.Callback()
    def _toggle_elapsed_remaining(self, _btn):
        self._show_remaining = not self._show_remaining
        settings.set_boolean("show-remaining", self._show_remaining)
        pos = float(self.mpv.time_pos or 0)
        self._update_progress(pos, update_bar=False)
        self._set_time_margin()

    def _update_progress(self, curr_time, update_bar=True):
        curr_time = round(curr_time, 1)

        if update_bar and curr_time == self._prev_prog_time:
            return

        if update_bar:
            self.video_progress_adj.handler_block_by_func(self._on_progress_adjusted)
            self.video_progress_adj.props.value = curr_time
            self.video_progress_adj.handler_unblock_by_func(self._on_progress_adjusted)

        try:
            if self._show_remaining:
                duration = float(self.mpv.duration or 0)
                remaining = (duration - curr_time) if duration > curr_time else 0
                self.time_elapsed_label.props.label = f"-{format_time(remaining)}"
            else:
                self.time_elapsed_label.props.label = format_time(curr_time)
        except mpv.ShutdownError:
            pass

        self._prev_prog_time = curr_time

    def _update_chapter_marks_and_menu(self, chapters):
        update_chapter_marks_and_menu(self, chapters)

    @Gtk.Template.Callback()
    def on_previous_clicked(self, *args):
        navigate_playlist(self, -1)

    @Gtk.Template.Callback()
    def on_next_clicked(self, *args):
        navigate_playlist(self, +1)

    def _on_subtitle_selected(self, action, parameter):
        self.mpv.command_async("set", "sub-visibility", "yes")
        track_id = parameter.get_int32()
        self.mpv.sid = track_id if track_id > 0 else "no"
        action.set_state(parameter)

    def _on_audio_selected(self, action, parameter):
        track_id = parameter.get_int32()
        self.mpv.aid = track_id
        action.set_state(parameter)

    def _on_video_selected(self, action, parameter):
        track_id = parameter.get_int32()
        self.mpv.vid = track_id
        action.set_state(parameter)

    def _on_chapter_selected(self, action, parameter):
        chapter_index = parameter.get_int32()
        self.mpv.chapter = chapter_index
        action.set_state(parameter)

    @Gtk.Template.Callback()
    def _sync_chapter_menu_selected(self, *args):
        if action := self.lookup_action("select-chapter"):
            action.set_state(  # pyright: ignore[reportAttributeAccessIssue]
                GLib.Variant("i", self.mpv.chapter)
            )

    def _update_play_pause_icon(self, paused):
        if self._shutting_down:
            return
        play = "cine-playback-start-symbolic"
        pause = "cine-playback-pause-symbolic"

        btn_icon = play if paused else pause
        self.play_pause_btn.set_icon_name(btn_icon)

        text = _("Play") if paused else _("Pause")
        self.play_pause_btn.update_property([Gtk.AccessibleProperty.LABEL], [text])

        self.icon_indicator.props.icon_name = pause if paused else play
        self._show_icon_indicator()
        self._mpris.update_playback_status(paused)

    def _update_duration(self, duration):
        self.time_total_label.set_text(format_time(duration))

        if duration == 0:
            self.video_progress_scale.set_can_target(False)
            self.video_progress_scale.set_can_focus(False)
            self._hide_time_tooltip()
            return

        self.video_progress_scale.set_can_target(True)
        self.video_progress_scale.set_can_focus(True)

        self.video_progress_adj.set_upper(duration)

        if duration >= 86400:
            chars = 10
        elif duration >= 3600:
            chars = 7
        elif duration >= 600:
            chars = 6
        else:
            chars = 5

        self.time_elapsed_label.set_width_chars(chars)

    @Gtk.Template.Callback()
    def _cycle_pause(self, *args):
        self.mpv.command_async("cycle", "pause")

    def _on_progress_pressed(self, *args):
        try:
            self._playing_on_press = not self.mpv.pause
            if self._playing_on_press:
                self.skip_pause_obs_count += 1
                self.mpv.command_async("set", "pause", "yes")
        except Exception:
            logger.exception("_on_progress_pressed failed")
            self.skip_pause_obs_count = 0

    def _on_progress_released(self, *args):
        try:
            if self._playing_on_press:
                self.skip_pause_obs_count += 1
                self._playing_on_press = False
                self.mpv.command_async("set", "pause", "no")
        except Exception:
            logger.exception("_on_progress_released failed")
            self.skip_pause_obs_count = 0

    def _on_progress_adjusted(self, adjustment):
        self.mpv.command_async("seek", adjustment.props.value, "absolute")

    @Gtk.Template.Callback()
    def _on_shuffle_toggled(self, button):
        media_on_shuffle_toggled(self, button)

    def _on_ab_loop_btn_toggled(self, button):
        if not button or not button.get_active():
            self.mpv.ab_loop_a = False
            self.mpv.ab_loop_b = False
        else:
            self.mpv.command_async("ab-loop")

    def _update_loop_state(self):
        icon_loop = "cine-playlist-repeat-symbolic"
        icon_loop_file = "cine-repeat-file-symbolic"
        is_file = self.mpv.loop_file
        is_playlist = self.mpv.loop_playlist

        if is_playlist:
            self._loop_mode = "playlist"
            icon, tooltip, active = icon_loop, _("Loop Playlist"), True
        elif is_file:
            self._loop_mode = "file"
            icon, tooltip, active = icon_loop_file, _("Loop File"), True
        else:
            self._loop_mode = "off"
            icon, tooltip, active = icon_loop, _("Loop"), False

        settings.set_string("loop-state", self._loop_mode)

        self.loop_btn.handler_block_by_func(self._on_loop_toggled)
        self.loop_btn.set_active(active)
        self.loop_btn.handler_unblock_by_func(self._on_loop_toggled)

        self.loop_btn.set_icon_name(icon)
        self.loop_btn.set_tooltip_text(tooltip)

    def _on_loop_toggled(self, _button):
        next_modes = {"off": "playlist", "playlist": "file", "file": "off"}
        curr_mode = self._loop_mode
        target_mode = next_modes[curr_mode]
        if target_mode == "playlist":
            self.mpv.loop_file = "no"
            self.mpv.loop_playlist = "inf"
        elif target_mode == "file":
            self.mpv.loop_file = "inf"
            self.mpv.loop_playlist = "no"
        elif target_mode == "off":
            self.mpv.loop_file = "no"
            self.mpv.loop_playlist = "no"

    def _sync_can_prev_next(self):
        sync_can_prev_next(self)

    def _on_drop_enter(self, _target, _x, _y):
        self.revealer_drop_indicator.set_reveal_child(True)
        return True

    def _on_drop_leave(self, _target):
        self.revealer_drop_indicator.set_reveal_child(False)

    def _on_drop(self, _target, value, _x, _y):
        handle_drop(self, value)

    def _sync_fullscreen(self, mpv_is_fs: bool):
        self._is_fullscreen = mpv_is_fs
        self.fullscreen() if mpv_is_fs else self.unfullscreen()

    def _set_space_holding(self, hold):
        if hold:
            self._space_hold_id = 0
            if self._click_holding:
                return

            # prevent being able to open menus when clicking buttons while holding spacebar
            # because that causes issues with the internal gtk button handling (space activates it)
            # and it becomes impossible to activate anything again in the window with mouse clicks
            # unless a menu popover is opened again (with keyboard enter)
            self.set_can_target(False)

            self._space_holding = True

            try:
                self.mpv.pause = False
                self._prev_speed = cast(float, self.mpv.speed)
                new_speed = self._prev_speed * 2
                self.mpv.speed = new_speed
                self.mpv.show_text(f"{new_speed:g}× ⯈⯈", "100000000")
            except mpv.ShutdownError:
                pass
        else:
            self.set_can_target(True)

            if self._space_hold_id:
                GLib.source_remove(self._space_hold_id)
                self._space_hold_id = 0

            if self._space_pressed:
                self._space_pressed = False
                try:
                    self.mpv.speed = self._prev_speed
                    self.mpv.show_text(f"{self.mpv.speed:g}×")
                except mpv.ShutdownError:
                    pass

    def _on_key_event(self, _controller, keyval, _keycode, state, event_type):
        self.skip_pause_obs_count = 0
        key_name = Gdk.keyval_name(keyval)

        if self._space_holding and event_type == "keyup":
            self._set_space_holding(False)

        enter = key_name == "Return" and not self._has_enter_binding
        kp_enter = key_name == "KP_Enter" and not self._has_kp_enter_binding

        if key_name in ("Tab", "ISO_Left_Tab") or (enter or kp_enter):
            self.revealer_ui.set_reveal_child(True)
            self.hide_ui_timeout(s=3)
            self._set_space_holding(False)
            return

        clean_state = state & Gtk.accelerator_get_default_mod_mask()
        accel = Gtk.accelerator_name(keyval, clean_state)
        shortcuts_accel = "<Shift><Control>question"
        if self.app.get_actions_for_accel(accel) or accel == shortcuts_accel:
            self._set_space_holding(False)
            return

        mpv_key = chr(Gdk.keyval_to_unicode(keyval))
        mpv_key = KEY_REMAP.get(key_name, mpv_key)

        mods = []
        append_modifiers(state, mods)

        combo = "+".join(mods + [mpv_key])

        if event_type == "keypress":
            if combo in self._nonrepeat_keys and combo in self._pressed_combos:
                return True
            self._pressed_combos.add(combo)
        elif event_type == "keyup":
            self._pressed_combos.discard(combo)

        if combo == "SPACE":
            if event_type == "keypress":
                if self._space_pressed:
                    return True

                self._space_pressed = True

                self._space_hold_id = timeout_add_once(
                    500, self._set_space_holding, True
                )
            elif event_type == "keyup":
                if self._space_hold_id:
                    GLib.source_remove(self._space_hold_id)
                    self._space_hold_id = 0

                if not self._space_holding:
                    self.mpv.command_async("keypress", "SPACE")
                    if self._space_pressed:
                        self._space_pressed = False

            self._space_holding = False
            return True

        try:
            self.mpv.command_async(event_type, combo)
            return True
        except mpv.ShutdownError:
            pass

    def _on_click_pressed(self, gesture, _n_press, x, y, button):
        self.skip_pause_obs_count = 0


        if button == "MBTN_LEFT":
            self._left_clk = settings.get_int("left-click")
        elif button == "MBTN_RIGHT":
            self._right_clk = settings.get_int("right-click")
            if (
                self._right_clk == SecondaryClick.CONTEXT_MENU
                and not self.start_page.props.visible
            ):
                rect = Gdk.Rectangle()
                rect.x = x
                rect.y = y
                self.context_popover_menu.set_pointing_to(rect)
                self.context_popover_menu.popup()
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return

        if button != "MBTN_LEFT":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        # Back and forward dont trigger _on_click_released when video is playing (??)
        if button in ("MBTN_BACK", "MBTN_FORWARD"):
            self.mpv.command_async("keypress", button)
            return

        self._show_ui()
        self.hide_ui_timeout()

    def _on_click_hold(self, gesture, *args):
        try:
            if self._space_holding or self._is_hovering_ui():
                return

            self._click_holding = True
            self.mpv.pause = False
            self._prev_speed = cast(float, self.mpv.speed)
            new_speed = self._prev_speed * 2
            self.mpv.speed = new_speed
            self.mpv.show_text(f"{new_speed:g}× ⯈⯈", "100000000")
        except mpv.ShutdownError:
            pass

    def _run_command(self, cmd):
        try:
            for sub_cmd in cmd.split(";"):
                args = shlex.split(sub_cmd.strip())
                self.mpv.command_async(*args)
        except Exception:
            logger.exception("_run_command failed")

    def _on_click_released(self, gesture, n_press, _x, _y, button):
        ignored_btn = button in ("MBTN_BACK", "MBTN_FORWARD")
        ignore_left = (
            self._is_inactive
            and button == "MBTN_LEFT"
            and self._left_clk == PrimaryClick.FOCUS_PLAY_PAUSE
        )

        if ignored_btn or ignore_left or self._is_hovering_ui():
            return

        is_secondary_pause = (
            button == "MBTN_RIGHT" and self._right_clk == SecondaryClick.PLAY_PAUSE
        )

        if self._click_delay_id:
            GLib.source_remove(self._click_delay_id)
            self._click_delay_id = 0

        if n_press == 1 and not self._click_holding:
            if button == "MBTN_LEFT" and self._left_clk != PrimaryClick.BYPASS:

                def click():
                    self._cycle_pause()
                    self._click_delay_id = 0

                self._click_delay_id = timeout_add_once(self._click_time, click)
                return
        elif n_press == 2 and (cmd_str_dbl := self._mouse_binds.get(f"{button}_DBL")):
            self._run_command(cmd_str_dbl)
            return

        if is_secondary_pause:
            self._cycle_pause()
        elif cmd_str := self._mouse_binds.get(button):
            self._run_command(cmd_str)

    def _cancel_click_hold(self, *args):
        if not self._click_holding:
            return
        try:
            self.mpv.speed = self._prev_speed
            self.mpv.show_text(f"{self.mpv.speed:g}×")
            self._click_holding = False
        except mpv.ShutdownError:
            pass

    def _on_mouse_scroll(self, controller, dx, dy):
        self.skip_pause_obs_count = 0
        event: Gdk.ScrollEvent = controller.get_current_event()
        state = event.get_modifier_state()

        if event.get_unit() == Gdk.ScrollUnit.SURFACE:  # Touchpad
            # Scale it down so it doesn't fire rapidly
            dx *= 0.1
            dy *= 0.1

        self._wheel_accum_x += dx
        self._wheel_accum_y += dy

        rel_dir: Gdk.ScrollRelativeDirection = event.get_relative_direction()  # type: ignore
        is_natural: bool = rel_dir == Gdk.ScrollRelativeDirection.INVERTED  # type: ignore
        UP: str = "WHEEL_DOWN" if is_natural else "WHEEL_UP"
        DOWN: str = "WHEEL_UP" if is_natural else "WHEEL_DOWN"
        LEFT: str = "WHEEL_RIGHT" if is_natural else "WHEEL_LEFT"
        RIGHT: str = "WHEEL_LEFT" if is_natural else "WHEEL_RIGHT"
        wheel: str | None = None

        mods = []
        append_modifiers(state, mods)

        # Only trigger if scrolled a full 'unit'
        if abs(self._wheel_accum_y) >= 1:
            wheel = UP if self._wheel_accum_y < 0 else DOWN
            self._wheel_accum_y = 0.0
        elif abs(self._wheel_accum_x) >= 1:
            wheel = RIGHT if self._wheel_accum_x > 0 else LEFT
            self._wheel_accum_x = 0.0

        if wheel:
            combo = "+".join(mods + [wheel])
            self.mpv.command_async("keypress", combo)

        return True

    def _on_mouse_scroll_volume(self, controller, _dx, dy):
        event: Gdk.ScrollEvent = controller.get_current_event()
        direction: Gdk.ScrollDirection = event.get_direction()
        rel_dir: Gdk.ScrollRelativeDirection = event.get_relative_direction()  # type: ignore
        is_natural: bool = rel_dir == Gdk.ScrollRelativeDirection.INVERTED  # type: ignore
        max_vol = cast(float, self.mpv.volume_max)
        step = dy if direction == Gdk.ScrollDirection.SMOOTH else dy * 5

        if is_natural:
            step = -step

        adj = self.volume_scale.get_adjustment()
        volume = adj.get_value()
        new_vol = int(volume - step)
        new_vol = max(adj.get_lower(), min(new_vol, max_vol))
        adj.set_value(new_vol)

        return True

    def _is_hovering_ui(self):
        controls_hover = self.motion_controls.props.contains_pointer
        header_hover = self.motion_header.props.contains_pointer
        separator_hover = self.motion_controls_separator.props.contains_pointer
        hovering = (controls_hover or header_hover) and not separator_hover
        return hovering


    def _release_inhibit(self):
        cookie = self._inhibit_cookie
        if not cookie:
            return
        self._inhibit_cookie = 0
        if self.app.get_is_registered():
            try:
                self.app.uninhibit(cookie)
            except Exception:
                logger.exception("Failed to uninhibit application")

    def _sync_inhibit(self):
        if self._shutting_down:
            return
        if not self.app.get_is_registered():
            return
        try:
            should_inhibit = not self.mpv.pause and not self.mpv.idle_active
        except (mpv.ShutdownError, AttributeError):
            should_inhibit = False

        if should_inhibit and self._inhibit_cookie == 0:
            try:
                self._inhibit_cookie = self.app.inhibit(
                    self,
                    Gtk.ApplicationInhibitFlags.IDLE,
                    "Playing Media",
                )
            except Exception:
                logger.exception("Failed to inhibit application")
        elif not should_inhibit and self._inhibit_cookie != 0:
            self._release_inhibit()

    def _show_icon_indicator(self):
        try:
            if self.mpv.idle_active or self._click_delay_id:
                return
        except mpv.ShutdownError:
            pass

        if not self._hide_icon_indicator:
            self.revealer_icon_indicator.set_reveal_child(True)
            timeout_add_once(385, self.revealer_icon_indicator.set_reveal_child, False)

    def on_first_media_frame_published(
        self,
        media_frame: Any,
        context_generation: int | None = None,
        load_generation: int | None = None,
    ):
        handle_first_media_frame_published(
            self,
            media_frame,
            context_generation=context_generation,
            load_generation=load_generation,
        )

    def on_first_video_frame(self):
        handle_first_video_frame(self)

    def do_close_request(self) -> bool:
        self._shutting_down = True
        self._release_inhibit()
        if self._playlist_debounce_id > 0:
            GLib.source_remove(self._playlist_debounce_id)
            self._playlist_debounce_id = 0
        notify_diagnostics_shutdown(self)

        try:
            if hasattr(self, "_video_area") and hasattr(self._video_area, "shutdown_render_context"):
                self._video_area.shutdown_render_context()
        except Exception as e:
            logging.error(f"Error shutting down render context: {e}")

        try:
            same_playlist = is_same_playlist(self.mpv.playlist)
            save_pos = settings.get_boolean("save-video-position")
            if self._is_gpu_validation:
                # Validation seeks must never overwrite the user's real
                # watch-later position for the sample.
                self.mpv.quit()
            elif same_playlist or save_pos:
                self.mpv.quit_watch_later()
            else:
                self.mpv.quit()
            try:
                self.mpv.wait_for_shutdown(timeout=3)
            except TimeoutError:
                logger.warning("Timed out waiting for mpv core shutdown after 3 seconds")
        except mpv.ShutdownError:
            pass

        if self._inhibit_cookie:
            self._release_inhibit()
        return False

    def splice_playlist(self):
        media_splice_playlist(self)

    def show_toast(self, label, force_dismiss=False):
        toast = Adw.Toast(title=label, timeout=2)
        self.toast_overlay.dismiss_all()
        self.toast_overlay.add_toast(toast)
        if force_dismiss:
            timeout_add_seconds_once(2, toast.dismiss)

    def _setup_observers(self):
        setup_window_observers(self)

    def _connect(self, signal_name):
        return lambda func: self.connect(signal_name, func)
