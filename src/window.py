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

import logging
import re
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
from .preferences import get_init_mpv_settings, settings, sync_mpv_with_settings
from .save_session import (
    is_same_playlist as is_same_playlist,
    restore_last_playlist,
    save_last_playlist_file as save_last_playlist_file,
)
from .shortcuts import INTERNAL_BINDINGS
from .utils import (
    CONFIG_DIR,
    INPUT_CONF,
    SCREENSHOT_DIR,
    WATCH_HISTORY_JSONL,
    has_host_permission as has_host_permission,
    idle_add_once as idle_add_once,
    is_local_path as is_local_path,
    load_optional_validation,
    parse_bindings,
    timeout_add_once,
    timeout_add_seconds_once,
)
from .video_widget import MpvVideoWidget
from .window_controls import (
    hide_time_tooltip,
    on_ab_loop_btn_toggled,
    on_loop_toggled,
    on_progress_motion,
    on_progress_pressed,
    on_progress_released,
    on_progress_scroll,
    set_time_tooltip,
    set_window_size_and_present,
    setup_playback_control_widgets,
    setup_thumb_preview as controls_setup_thumb_preview,
    toggle_elapsed_remaining,
    update_duration,
    update_loop_state,
    update_play_pause_icon,
    update_progress,
    update_volume_icon,
)
from .window_diagnostics import (
    handle_first_media_frame_published,
    handle_first_video_frame,
    init_diagnostics_exporter,
    notify_diagnostics_shutdown,
)
from .window_input import (
    cancel_click_hold,
    hide_ui,
    hide_ui_timeout as input_hide_ui_timeout,
    is_hovering_ui,
    on_click_hold,
    on_click_pressed,
    on_click_released,
    on_key_event,
    on_mouse_motion,
    on_mouse_scroll,
    on_mouse_scroll_volume,
    run_mpv_command,
    set_fs_state,
    set_space_holding,
    setup_input_controllers_and_window_signals,
    show_ui,
    sync_fullscreen,
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.app: Adw.Application = cast(Adw.Application, kwargs.get("application"))
        self._mpris: MPRIS = self.app.mpris  # type: ignore
        self._is_gpu_validation: bool = (
            getattr(self.app, "gpu_validation_config", None) is not None
        )

        Gtk.WindowGroup().add_window(self)
        self._init_window_state()

        mpv_logger = logging.getLogger("mpv")
        self._diagnostics_exporter = init_diagnostics_exporter(
            __package__, logger, load_optional_validation
        )
        self._mpv_log_handler = make_mpv_log_handler(self, mpv_logger)
        self.mpv = self._create_mpv_player()

        self._video_area = MpvVideoWidget(
            self.mpv,
            render_backend_selection=self.app.render_backend_selection,  # type: ignore
        )
        self._configure_video_offload_and_mpv_defaults()
        self._setup_actions()
        self._setup_widgets()
        self._setup_observers()
        self._load_input_bindings()
        sync_mpv_with_settings(self)

    def _init_window_state(self):
        self._presented: bool = False
        self._session_restoring: bool = False
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
        self.dovi_rpu_warning_count: int = 0
        self.dovi_rpu_warning_text: str | None = None
        self._dovi_rpu_logged: bool = False
        self._shutting_down: bool = False
        self._media_load_generation: int = 0

    def _create_mpv_player(self):
        init_setts = get_init_mpv_settings()
        return mpv.MPV(
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
            alang=init_setts["alang"],
            slang=init_setts["slang"],
            sub_auto="fuzzy",
            sub_file_paths="sub:subs:subtitles:Sub:Subs:Subtitles:srt:srts:Srt:Srts",
            sub_border_size=2,
            sub_color=init_setts["sub-color"],
            sub_scale=init_setts["sub-scale"],
            sub_font=init_setts["sub-font"],
            sub_border_style=init_setts["sub-border-style"],
            sub_shadow_offset=init_setts["sub-shadow-offset"],
            sub_border_color=init_setts["sub-border-color"],
            sub_back_color=init_setts["sub-back-color"],
            sub_shadow_color=init_setts["sub-back-color"],
            sub_use_margins=False,
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
            volume=init_setts["volume"],
            volume_max=150,
            keep_open=True,
            ytdl=True,
            ytdl_raw_options="yes-playlist=",
            cursor_autohide_fs_only=True,
            autocreate_playlist="filter",
            save_position_on_quit=init_setts["save-position-on-quit"],
            save_watch_history=True,
            watch_history_path=WATCH_HISTORY_JSONL,
        )

    def _configure_video_offload_and_mpv_defaults(self):
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
        self.mpv["vo"] = "libmpv"
        self.mpv["osc"] = "no"
        self.mpv["load-console"] = "no"
        for opt in ("vid", "aid", "volume", "sub-scale", "ab-loop-a", "ab-loop-b"):
            self.mpv.command("change-list", "watch-later-options", "remove", opt)

    def restore_session(self):
        if not self._is_gpu_validation:
            self._session_restoring = restore_last_playlist(self)

    def ensure_video_render_context(self):
        if not self.get_realized():
            self.realize()
        if not self._video_area.get_realized():
            self._video_area.realize()

    def set_win_size_and_present(self, width=1920, height=1080):
        set_window_size_and_present(self, width, height)

    def _load_input_bindings(self):
        try:
            self.mpv.command("load-input-conf", f"memory://{INTERNAL_BINDINGS}")
            self.mpv.command("load-input-conf", INPUT_CONF)
        except Exception:
            logger.exception("load-input-conf failed")

        self._input_bindings = cast(dict, self.mpv.input_bindings)
        self._nonrepeat_keys, self._has_enter_binding, self._has_kp_enter_binding = (
            parse_bindings(self._input_bindings)
        )

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
        setup_playback_control_widgets(self)
        setup_input_controllers_and_window_signals(self)

    def _set_fs_state(self, _window, _gparam):
        set_fs_state(self)

    def _show_ui(self):
        show_ui(self)

    def hide_ui_timeout(self, *args, s=2):
        input_hide_ui_timeout(self, s=s)

    def _hide_ui(self, *args):
        hide_ui(self)

    def _on_mouse_motion(self, controller: Gtk.EventControllerMotion, x, y):
        on_mouse_motion(self, controller, x, y)

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
        controls_setup_thumb_preview(self)

    def _hide_time_tooltip(self, *args):
        hide_time_tooltip(self)

    def _set_time_tooltip(self, *args):
        set_time_tooltip(self)

    def _on_progress_motion(self, _controller, x, y):
        on_progress_motion(self, x, y)

    def _go_to_chapter_start(self, *args):
        if self._curr_chapter_time is not None:
            self.mpv.command_async("seek", self._curr_chapter_time, "absolute")

    def _on_progress_scroll(self, controller, _dx, dy):
        return on_progress_scroll(controller, dy, self.video_progress_scale)

    def _update_volume_icon(self):
        update_volume_icon(self)

    def _set_time_margin(self):
        self.time_elapsed_label.props.margin_end = 3 if self._show_remaining else 0

    @Gtk.Template.Callback()
    def _toggle_elapsed_remaining(self, _btn):
        toggle_elapsed_remaining(self)

    def _update_progress(self, curr_time, update_bar=True):
        update_progress(self, curr_time, update_bar=update_bar)

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
        update_play_pause_icon(self, paused)

    def _update_duration(self, duration):
        update_duration(self, duration)

    @Gtk.Template.Callback()
    def _cycle_pause(self, *args):
        self.mpv.command_async("cycle", "pause")

    def _on_progress_pressed(self, *args):
        on_progress_pressed(self)

    def _on_progress_released(self, *args):
        on_progress_released(self)

    def _on_progress_adjusted(self, adjustment):
        self.mpv.command_async("seek", adjustment.props.value, "absolute")

    @Gtk.Template.Callback()
    def _on_shuffle_toggled(self, button):
        media_on_shuffle_toggled(self, button)

    def _on_ab_loop_btn_toggled(self, button):
        on_ab_loop_btn_toggled(self, button)

    def _update_loop_state(self):
        update_loop_state(self)

    def _on_loop_toggled(self, _button):
        on_loop_toggled(self)

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
        sync_fullscreen(self, mpv_is_fs)

    def _set_space_holding(self, hold):
        set_space_holding(self, hold)

    def _on_key_event(self, _controller, keyval, _keycode, state, event_type):
        return on_key_event(self, keyval, state, event_type)

    def _on_click_pressed(self, gesture, _n_press, x, y, button):
        on_click_pressed(self, gesture, x, y, button)

    def _on_click_hold(self, gesture, *args):
        on_click_hold(self)

    def _run_command(self, cmd):
        run_mpv_command(self, cmd)

    def _on_click_released(self, gesture, n_press, _x, _y, button):
        on_click_released(self, n_press, button)

    def _cancel_click_hold(self, *args):
        cancel_click_hold(self)

    def _on_mouse_scroll(self, controller, dx, dy):
        return on_mouse_scroll(self, controller, dx, dy)

    def _on_mouse_scroll_volume(self, controller, _dx, dy):
        return on_mouse_scroll_volume(self, controller, dy)

    def _is_hovering_ui(self):
        return is_hovering_ui(self)


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
